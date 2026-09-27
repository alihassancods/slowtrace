"""GET /api/scan/{connection_id} — full progressive database scan via SSE.

Connection strings are registered via POST /api/scan/register and stored in
the module-level ``_CONNECTIONS`` dict keyed by a UUID.  The GET endpoint
streams eleven events in order:

  1. connected      — DB metadata (version, size, table_count, total_rows)
  2-9. health_check — one per check, sent as each finishes (asyncio.as_completed)
  10. slow_queries  — top-10 slow queries + wasted-minutes summary
  11. complete      — final score, counts, and quick-win recommendations
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any

import asyncpg
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

import uuid

from pydantic import BaseModel, field_validator

from api.store import CONNECTIONS

# Backwards-compatible alias so existing tests can import _CONNECTIONS from here
_CONNECTIONS = CONNECTIONS

# Re-use the health-check implementations from the existing health module.
from api.health import (
    WEIGHTS,
    _check_cache_hit_ratio,
    _check_connections,
    _check_index_usage,
    _check_lock_contention,
    _check_long_transactions,
    _check_replication_lag,
    _check_table_bloat,
    _compute_score,
    _build_check_result,
)
from api.queries import _fingerprint, _score_query

router = APIRouter()

# ---------------------------------------------------------------------------
# SSE helper
# ---------------------------------------------------------------------------


def _sse(stage: str, data: dict[str, Any]) -> str:
    """Format a single SSE line in the expected envelope."""
    # default=str keeps one exotic column type (Decimal, date, …) from taking
    # down the whole stream.
    payload = json.dumps({"stage": stage, "data": data}, default=str)
    return f"data: {payload}\n\n"


# ---------------------------------------------------------------------------
# Event 1 — connected  (4 queries in parallel)
# ---------------------------------------------------------------------------

_VERSION_SQL = "SELECT version()"
_SIZE_SQL = "SELECT pg_size_pretty(pg_database_size(current_database()))"
_TABLE_COUNT_SQL = """
SELECT count(*)
FROM information_schema.tables
WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
  AND table_type = 'BASE TABLE'
"""
_TOTAL_ROWS_SQL = """
SELECT coalesce(sum(reltuples), 0)::bigint
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind = 'r'
  AND n.nspname NOT IN ('information_schema', 'pg_catalog')
"""


async def _fetch_db_metadata(conn: asyncpg.Connection) -> dict[str, Any]:
    """Run the 4 metadata queries and return a combined dict.

    A single asyncpg connection cannot run concurrent operations, so the
    queries are issued one after another.
    """
    version = await conn.fetchval(_VERSION_SQL)
    size = await conn.fetchval(_SIZE_SQL)
    table_count = await conn.fetchval(_TABLE_COUNT_SQL)
    total_rows = await conn.fetchval(_TOTAL_ROWS_SQL)
    return {
        "version": version,
        "size": size,
        "table_count": int(table_count),
        "total_rows": int(total_rows),
    }


# ---------------------------------------------------------------------------
# Event 2-9 — health checks
# ---------------------------------------------------------------------------

# 8 checks: the 7 from health.py + dead_tuples autovacuum check
async def _check_dead_tuples(
    conn: asyncpg.Connection,
) -> tuple[str, dict[str, Any]]:
    """Tables with a high ratio of dead tuples that need VACUUM."""
    try:
        rows = await conn.fetch(
            """
            SELECT
                schemaname || '.' || relname AS table_name,
                n_dead_tup,
                n_live_tup,
                CASE WHEN (n_live_tup + n_dead_tup) > 0
                     THEN ROUND(n_dead_tup::numeric / (n_live_tup + n_dead_tup) * 100, 1)
                     ELSE 0
                END AS dead_ratio_pct,
                last_autovacuum,
                last_autoanalyze
            FROM pg_stat_user_tables
            WHERE (n_live_tup + n_dead_tup) > 1000
              AND n_dead_tup::float / (n_live_tup + n_dead_tup) > 0.1
            ORDER BY n_dead_tup DESC
            LIMIT 5
            """
        )
    except asyncpg.InsufficientPrivilegeError:
        return "warning", {
            "message": "Insufficient privilege to read pg_stat_user_tables.",
            "fix": "GRANT SELECT ON pg_catalog.pg_stat_user_tables TO <user>;",
        }
    except asyncpg.PostgresError as exc:
        return "fail", {"message": f"Dead tuples check failed: {exc}"}

    count = len(rows)
    tables = [
        {
            "table": row["table_name"],
            "dead_ratio_pct": float(row["dead_ratio_pct"]),
            "n_dead_tup": row["n_dead_tup"],
            "last_autovacuum": str(row["last_autovacuum"]) if row["last_autovacuum"] else None,
        }
        for row in rows
    ]
    data: dict[str, Any] = {"bloated_count": count, "tables": tables}
    msg = f"{count} table(s) with >10% dead tuples — VACUUM recommended"
    if count >= 3:
        return "fail", {**data, "message": f"{msg} (critical ≥ 3)"}
    if count >= 1:
        return "warning", {**data, "message": msg}
    return "ok", {"bloated_count": 0, "tables": [], "message": "Dead tuple ratio looks healthy."}


# Ordered list of (name, coroutine-factory) pairs for the 8 health checks.
_HEALTH_CHECKS: list[tuple[str, Any]] = [
    ("connections", _check_connections),
    ("cache_hit_ratio", _check_cache_hit_ratio),
    ("replication_lag", _check_replication_lag),
    ("table_bloat", _check_table_bloat),
    ("lock_contention", _check_lock_contention),
    ("long_transactions", _check_long_transactions),
    ("index_usage", _check_index_usage),
    ("dead_tuples", _check_dead_tuples),
]

# Extend WEIGHTS with the new check (non-destructive — adds only if absent)
WEIGHTS.setdefault("dead_tuples", 10)


# ---------------------------------------------------------------------------
# Event 10 — slow queries
# ---------------------------------------------------------------------------

_SLOW_QUERY_SQL = """
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
LIMIT 10
"""


async def _fetch_slow_queries(conn: asyncpg.Connection) -> dict[str, Any]:
    """Return top-10 slow queries and a human summary."""
    try:
        rows = await asyncio.wait_for(conn.fetch(_SLOW_QUERY_SQL), timeout=30)
    except asyncio.TimeoutError:
        return {"queries": [], "total_wasted_minutes": 0.0, "human_description": "Query timed out."}
    except asyncpg.PostgresError as exc:
        return {"queries": [], "total_wasted_minutes": 0.0, "human_description": str(exc)}

    raw = [dict(r) for r in rows]
    max_total = max((r["total_exec_time_ms"] or 0.0 for r in raw), default=0.0)

    queries = []
    total_wasted_ms = 0.0
    for r in raw:
        score = _score_query(r, max_total)
        total_wasted_ms += r["total_exec_time_ms"] or 0.0
        queries.append(
            {
                "queryid": r["queryid"],
                "query": r["query"],
                "query_fingerprint": _fingerprint(r["query"]),
                "calls": r["calls"],
                "mean_exec_time_ms": round(r["mean_exec_time_ms"] or 0.0, 2),
                "total_exec_time_ms": round(r["total_exec_time_ms"] or 0.0, 2),
                "cache_hit_ratio": round(r["cache_hit_ratio"] if r["cache_hit_ratio"] is not None else 100.0, 2),
                "score": score,
            }
        )

    total_wasted_minutes = round(total_wasted_ms / 60_000, 2)

    if not queries:
        human_description = "No query statistics available — pg_stat_statements may not be installed."
    elif total_wasted_minutes >= 60:
        human_description = (
            f"The top 10 slowest queries have collectively consumed "
            f"{total_wasted_minutes:.1f} minutes of database time. "
            "Immediate optimisation is recommended."
        )
    elif total_wasted_minutes >= 1:
        human_description = (
            f"The top 10 slowest queries account for "
            f"{total_wasted_minutes:.1f} minutes of total execution time. "
            "Review the highest-scored queries for indexing or rewrite opportunities."
        )
    else:
        human_description = (
            f"The top 10 queries have used {total_wasted_minutes:.2f} minutes total — "
            "query performance looks reasonable."
        )

    return {
        "queries": queries,
        "total_wasted_minutes": total_wasted_minutes,
        "human_description": human_description,
    }


# ---------------------------------------------------------------------------
# Event 11 — complete (score + quick wins)
# ---------------------------------------------------------------------------

# Maps check name → a short, actionable fix description
_QUICK_WIN_HINTS: dict[str, str] = {
    "connections": "Close idle connections or increase max_connections.",
    "cache_hit_ratio": "Increase shared_buffers to ~25% of RAM and run VACUUM ANALYZE.",
    "replication_lag": "Investigate network latency or I/O throughput on replicas.",
    "table_bloat": "Run VACUUM FULL or pg_repack on the bloated tables.",
    "lock_contention": "Identify and terminate blocking PIDs via pg_terminate_backend().",
    "long_transactions": "Set idle_in_transaction_session_timeout to limit open transactions.",
    "index_usage": "Add indexes on high-seq-scan columns identified in the health check.",
    "dead_tuples": "Run VACUUM ANALYZE on tables with high dead-tuple ratios.",
}


def _build_complete_event(
    check_results: list[tuple[str, str, dict[str, Any]]],
) -> dict[str, Any]:
    """Compute the final health score and surface up to 3 easy quick wins."""
    from api.health import CheckResult  # local import to avoid circular at module level

    check_models = [
        _build_check_result(name, status, data)
        for name, status, data in check_results
    ]
    score, _grade, deductions = _compute_score(check_models)

    critical_count = sum(1 for _, status, _ in check_results if status == "fail")
    warning_count = sum(1 for _, status, _ in check_results if status == "warning")
    healthy_count = sum(1 for _, status, _ in check_results if status == "ok")

    # Quick wins: prefer "warning" checks (easier to fix) then "fail", top 3
    non_ok = [
        (name, status)
        for name, status, _ in check_results
        if status != "ok"
    ]
    non_ok.sort(key=lambda t: (0 if t[1] == "warning" else 1, t[0]))
    quick_wins = [
        {"check": name, "fix": _QUICK_WIN_HINTS.get(name, f"Investigate the {name} check.")}
        for name, _ in non_ok[:3]
    ]

    return {
        "health_score": score,
        "critical_count": critical_count,
        "warning_count": warning_count,
        "healthy_count": healthy_count,
        "quick_wins": quick_wins,
    }


# ---------------------------------------------------------------------------
# Main SSE generator
# ---------------------------------------------------------------------------


async def _run_scan(dsn: str) -> AsyncGenerator[str, None]:
    """Stream all 11 scan events for *dsn*."""

    # ── Connect ──────────────────────────────────────────────────────────────
    try:
        # statement_cache_size=0: PgBouncer transaction poolers (e.g. Supabase
        # port 6543) cannot support server-side prepared statements.
        conn: asyncpg.Connection = await asyncio.wait_for(
            asyncpg.connect(dsn=dsn, statement_cache_size=0), timeout=10
        )
    except Exception as exc:
        try:
            msg = str(exc)
        except Exception:
            msg = type(exc).__name__
        yield _sse("connected", {"error": msg})
        return

    try:
        # ── Event 1: connected ───────────────────────────────────────────────
        try:
            metadata = await _fetch_db_metadata(conn)
        except Exception as exc:
            metadata = {"error": str(exc)}
        yield _sse("connected", metadata)

        # ── Events 2-9: health_check (stream as each completes) ──────────────
        check_results: list[tuple[str, str, dict[str, Any]]] = []

        # A single asyncpg connection cannot run concurrent operations, so the
        # checks run one at a time and each is streamed as it finishes.
        for name, check_fn in _HEALTH_CHECKS:
            try:
                status, data = await asyncio.wait_for(check_fn(conn), timeout=10)
            except asyncio.TimeoutError:
                status, data = "fail", {"message": "Check timed out after 10 seconds."}
            except Exception as exc:
                status, data = "fail", {"message": str(exc)}
            check_results.append((name, status, data))
            yield _sse(
                "health_check",
                {"check": name, "status": status, **data},
            )
            await asyncio.sleep(0.1)

        # ── Event 10: slow_queries ───────────────────────────────────────────
        slow_queries_data = await _fetch_slow_queries(conn)
        yield _sse("slow_queries", slow_queries_data)

    finally:
        await conn.close()

    # ── Event 11: complete ───────────────────────────────────────────────────
    complete_data = _build_complete_event(check_results)
    yield _sse("complete", complete_data)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


class _RegisterRequest(BaseModel):
    connection_string: str

    @field_validator("connection_string")
    @classmethod
    def validate_connection_string(cls, v: str) -> str:
        v = v.strip()
        if len(v) > 2048:
            raise ValueError("Connection string is too long (max 2048 characters).")
        if not v.startswith(("postgresql://", "postgres://")):
            raise ValueError(
                "Connection string must start with 'postgresql://' or 'postgres://'."
            )
        return v


class _RegisterResponse(BaseModel):
    connection_id: str


@router.post("/scan/register", response_model=_RegisterResponse)
async def register_connection(body: _RegisterRequest) -> _RegisterResponse:
    """Store a connection string and return a UUID to reference it later.

    Kept for backwards compatibility.  New code should use POST /api/connections/test
    which runs validation checks and returns a connection_id on success.
    """
    connection_id = str(uuid.uuid4())
    CONNECTIONS[connection_id] = body.connection_string
    return _RegisterResponse(connection_id=connection_id)


@router.get("/scan/{connection_id}")
async def scan_database(connection_id: str) -> StreamingResponse:
    """Stream a full database scan as Server-Sent Events."""
    dsn = CONNECTIONS.get(connection_id)
    if dsn is None:
        raise HTTPException(status_code=404, detail="Connection ID not found.")
    return StreamingResponse(
        _run_scan(dsn),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Content-Type": "text/event-stream",
        },
    )
