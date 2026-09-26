"""GET /api/queries/{connection_id}[/stream] — slow query analysis via pg_stat_statements."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncGenerator
from typing import Any
from urllib.parse import unquote

import asyncpg
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

router = APIRouter(prefix="/api/queries")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class SlowQuery(BaseModel):
    queryid: str
    query: str
    query_fingerprint: str
    calls: int
    mean_exec_time_ms: float
    total_exec_time_ms: float
    stddev_exec_time_ms: float
    rows_per_call: float
    shared_blks_hit: int
    shared_blks_read: int
    cache_hit_ratio: float
    score: float


class ScanError(BaseModel):
    step: str
    message: str


class QueryReport(BaseModel):
    connection_id: str
    total_queries: int
    queries: list[SlowQuery]
    errors: list[ScanError]


# ---------------------------------------------------------------------------
# SSE helper
# ---------------------------------------------------------------------------


def _event(step: str, status: str, data: dict[str, Any]) -> str:
    """Format a single SSE data line."""
    payload = json.dumps({"step": step, "status": status, "data": data})
    return f"data: {payload}\n\n"


# ---------------------------------------------------------------------------
# Query fingerprinting
# ---------------------------------------------------------------------------

_RE_COMMENTS = re.compile(r"/\*.*?\*/", re.DOTALL)
_RE_STRINGS = re.compile(r"'[^']*'")
_RE_NUMBERS = re.compile(r"\b\d+(?:\.\d+)?\b")
_RE_WHITESPACE = re.compile(r"\s+")


def _fingerprint(query: str) -> str:
    """Normalise a SQL query to a lowercase, literal-free fingerprint."""
    q = _RE_COMMENTS.sub("", query)
    q = _RE_STRINGS.sub("?", q)
    q = _RE_NUMBERS.sub("?", q)
    q = _RE_WHITESPACE.sub(" ", q).strip().lower()
    return q


# ---------------------------------------------------------------------------
# Scoring algorithm
# ---------------------------------------------------------------------------


def _score_query(row: dict[str, Any], max_total_ms: float) -> float:
    """Return a badness score in [0, 100] — higher means higher priority to fix."""
    mean_ms: float = row["mean_exec_time_ms"] or 0.0
    total_ms: float = row["total_exec_time_ms"] or 0.0
    stddev_ms: float = row["stddev_exec_time_ms"] or 0.0
    cache_hit: float = row["cache_hit_ratio"] if row["cache_hit_ratio"] is not None else 100.0

    mean_signal = min(mean_ms / 1000.0, 1.0) * 40.0
    total_signal = (total_ms / max_total_ms * 30.0) if max_total_ms > 0 else 0.0
    cache_signal = (1.0 - cache_hit / 100.0) * 20.0
    stddev_signal = (min(stddev_ms / mean_ms, 1.0) * 10.0) if mean_ms > 0 else 0.0

    score = mean_signal + total_signal + cache_signal + stddev_signal
    return round(score, 1)


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

_FETCH_SQL = """
SELECT
    queryid::text                        AS queryid,
    query,
    calls,
    mean_exec_time                       AS mean_exec_time_ms,
    total_exec_time                      AS total_exec_time_ms,
    stddev_exec_time                     AS stddev_exec_time_ms,
    rows / NULLIF(calls, 0)              AS rows_per_call,
    shared_blks_hit,
    shared_blks_read,
    CASE
        WHEN (shared_blks_hit + shared_blks_read) = 0 THEN 100.0
        ELSE shared_blks_hit::float * 100.0
             / (shared_blks_hit + shared_blks_read)
    END                                  AS cache_hit_ratio
FROM pg_stat_statements
WHERE calls > 0
ORDER BY total_exec_time DESC
LIMIT 200
"""


# ---------------------------------------------------------------------------
# Step implementations
# ---------------------------------------------------------------------------


async def _step_connect(dsn: str) -> tuple[asyncpg.Connection | None, str, dict[str, Any]]:
    """Open a raw connection with a 5-second timeout."""
    try:
        conn = await asyncio.wait_for(asyncpg.connect(dsn=dsn), timeout=5)
        return conn, "ok", {}
    except asyncio.TimeoutError:
        return None, "fail", {"message": "Connection timed out after 5 seconds."}
    except asyncpg.InvalidPasswordError:
        return None, "fail", {"message": "Authentication failed: wrong password."}
    except asyncpg.InvalidCatalogNameError as exc:
        return None, "fail", {"message": f"Database not found: {exc}"}
    except OSError as exc:
        return None, "fail", {"message": f"Host not reachable: {exc}"}
    except asyncpg.PostgresError as exc:
        return None, "fail", {"message": f"Connection error: {exc}"}


async def _step_check_extension(conn: asyncpg.Connection) -> tuple[str, dict[str, Any]]:
    """Verify pg_stat_statements is installed."""
    try:
        count: int = await conn.fetchval(
            "SELECT count(*) FROM pg_extension WHERE extname = 'pg_stat_statements'"
        )
        if count:
            return "ok", {}
        return "warning", {
            "message": "pg_stat_statements extension is not installed.",
            "fix": "CREATE EXTENSION pg_stat_statements;",
        }
    except asyncpg.PostgresError as exc:
        return "fail", {"message": f"Could not query pg_extension: {exc}"}


async def _step_check_permissions(conn: asyncpg.Connection) -> tuple[str, dict[str, Any]]:
    """Verify the user can SELECT from pg_stat_statements."""
    try:
        await conn.fetch("SELECT * FROM pg_stat_statements LIMIT 1")
        return "ok", {}
    except asyncpg.InsufficientPrivilegeError:
        try:
            user: str = await conn.fetchval("SELECT current_user")
        except Exception:
            user = "your_user"
        return "warning", {
            "message": "Insufficient privileges to read pg_stat_statements.",
            "fix": f"GRANT pg_read_all_stats TO {user};",
        }
    except asyncpg.UndefinedTableError:
        return "warning", {
            "message": "pg_stat_statements view is not accessible.",
            "fix": "CREATE EXTENSION pg_stat_statements;",
        }
    except asyncpg.PostgresError as exc:
        return "fail", {"message": f"Permission check failed: {exc}"}


async def _step_fetch_queries(
    conn: asyncpg.Connection,
) -> tuple[str, dict[str, Any], list[SlowQuery]]:
    """Run the pg_stat_statements query, fingerprint, and score each row."""
    try:
        rows = await asyncio.wait_for(conn.fetch(_FETCH_SQL), timeout=30)
    except asyncio.TimeoutError:
        return "fail", {"message": "Query fetch timed out"}, []
    except asyncpg.PostgresError as exc:
        return "fail", {"message": f"Query fetch failed: {exc}"}, []

    raw: list[dict[str, Any]] = [dict(r) for r in rows]
    max_total = max((r["total_exec_time_ms"] or 0.0 for r in raw), default=0.0)

    queries: list[SlowQuery] = []
    for r in raw:
        queries.append(
            SlowQuery(
                queryid=r["queryid"],
                query=r["query"],
                query_fingerprint=_fingerprint(r["query"]),
                calls=r["calls"],
                mean_exec_time_ms=r["mean_exec_time_ms"] or 0.0,
                total_exec_time_ms=r["total_exec_time_ms"] or 0.0,
                stddev_exec_time_ms=r["stddev_exec_time_ms"] or 0.0,
                rows_per_call=r["rows_per_call"] or 0.0,
                shared_blks_hit=r["shared_blks_hit"],
                shared_blks_read=r["shared_blks_read"],
                cache_hit_ratio=r["cache_hit_ratio"] if r["cache_hit_ratio"] is not None else 100.0,
                score=_score_query(r, max_total),
            )
        )

    queries.sort(key=lambda q: q.score, reverse=True)
    return "ok", {"count": len(queries)}, queries


# ---------------------------------------------------------------------------
# SSE generator
# ---------------------------------------------------------------------------


async def _run_query_stream(dsn: str) -> AsyncGenerator[str, None]:
    """Yield SSE events for each analysis step, then a final result event."""
    errors: list[ScanError] = []

    # Step 1 – connect
    conn, status, data = await _step_connect(dsn)
    yield _event("connect", status, data)
    if conn is None:
        errors.append(ScanError(step="connect", message=data.get("message", "")))
        report = QueryReport(connection_id=dsn, total_queries=0, queries=[], errors=errors)
        yield _event("result", "fail", report.model_dump())
        return

    try:
        # Step 2 – check extension
        status, data = await _step_check_extension(conn)
        yield _event("check_extension", status, data)
        if status == "fail":
            errors.append(ScanError(step="check_extension", message=data.get("message", "")))
            report = QueryReport(connection_id=dsn, total_queries=0, queries=[], errors=errors)
            yield _event("result", "fail", report.model_dump())
            return
        if status == "warning":
            errors.append(ScanError(step="check_extension", message=data.get("message", "")))

        # Step 3 – check permissions
        status, data = await _step_check_permissions(conn)
        yield _event("check_permissions", status, data)
        if status == "fail":
            errors.append(ScanError(step="check_permissions", message=data.get("message", "")))
            report = QueryReport(connection_id=dsn, total_queries=0, queries=[], errors=errors)
            yield _event("result", "fail", report.model_dump())
            return
        if status == "warning":
            errors.append(ScanError(step="check_permissions", message=data.get("message", "")))

        # Step 4 – fetch queries
        status, data, queries = await _step_fetch_queries(conn)
        yield _event("fetch_queries", status, data)
        if status == "fail":
            errors.append(ScanError(step="fetch_queries", message=data.get("message", "")))
            report = QueryReport(connection_id=dsn, total_queries=0, queries=[], errors=errors)
            yield _event("result", "fail", report.model_dump())
            return

    finally:
        await conn.close()

    # Step 5 – result
    report = QueryReport(
        connection_id=dsn,
        total_queries=len(queries),
        queries=queries,
        errors=errors,
    )
    yield _event("result", "ok", report.model_dump())


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/{connection_id:path}/stream")
async def stream_queries(connection_id: str) -> StreamingResponse:
    """Stream slow-query analysis step results as Server-Sent Events."""
    dsn = unquote(connection_id)
    return StreamingResponse(
        _run_query_stream(dsn),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{connection_id:path}")
async def get_queries(connection_id: str) -> QueryReport:
    """One-shot JSON endpoint — run all analysis and return results."""
    dsn = unquote(connection_id)
    events: list[dict[str, Any]] = []
    async for raw_event in _run_query_stream(dsn):
        if raw_event.startswith("data: "):
            events.append(json.loads(raw_event[len("data: "):]))
    # The last event is always "result"
    result_event = next((e for e in reversed(events) if e["step"] == "result"), None)
    if result_event:
        return QueryReport(**result_event["data"])
    return QueryReport(connection_id=dsn, total_queries=0, queries=[], errors=[])
