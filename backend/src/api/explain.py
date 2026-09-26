"""GET /api/explain/{connection_id}/{queryid} — single-query stats + EXPLAIN plan."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import unquote

import asyncpg
from fastapi import APIRouter
from pydantic import BaseModel

from api.queries import _fingerprint, _score_query, _step_connect

router = APIRouter(prefix="/api/explain")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ExplainError(BaseModel):
    step: str
    message: str


class QueryDetail(BaseModel):
    connection_id: str
    queryid: str
    query: str | None
    query_fingerprint: str | None
    calls: int | None
    mean_exec_time_ms: float | None
    total_exec_time_ms: float | None
    stddev_exec_time_ms: float | None
    rows_per_call: float | None
    shared_blks_hit: int | None
    shared_blks_read: int | None
    cache_hit_ratio: float | None
    score: float | None
    plan: list[Any] | None
    errors: list[ExplainError]


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

_FETCH_SINGLE_SQL = """
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
WHERE queryid::text = $1
LIMIT 1
"""


# ---------------------------------------------------------------------------
# Step implementations
# ---------------------------------------------------------------------------


async def _step_fetch_query(
    conn: asyncpg.Connection, queryid: str
) -> tuple[str, dict[str, Any]]:
    """Fetch a single query's stats from pg_stat_statements.

    Returns:
        ("ok", row_dict)         — row found
        ("not_found", {})        — queryid not in pg_stat_statements
        ("fail", {"message": …}) — DB / timeout error
    """
    try:
        row = await asyncio.wait_for(
            conn.fetchrow(_FETCH_SINGLE_SQL, queryid), timeout=10
        )
    except asyncio.TimeoutError:
        return "fail", {"message": "Step timed out"}
    except asyncpg.InsufficientPrivilegeError:
        try:
            user: str = await conn.fetchval("SELECT current_user")
        except Exception:
            user = "your_user"
        return "fail", {
            "message": f"Insufficient privileges. GRANT pg_read_all_stats TO {user};"
        }
    except asyncpg.UndefinedTableError:
        return "fail", {
            "message": "pg_stat_statements is not installed. CREATE EXTENSION pg_stat_statements;"
        }
    except asyncpg.PostgresError as exc:
        return "fail", {"message": f"Query fetch failed: {exc}"}

    if row is None:
        return "not_found", {}

    return "ok", dict(row)


async def _step_explain(
    conn: asyncpg.Connection, query_text: str
) -> tuple[str, list[Any] | None]:
    """Run EXPLAIN (ANALYZE false, BUFFERS, FORMAT JSON) on the given query text.

    Returns:
        ("ok", plan_list)  — plan parsed successfully
        ("fail", None)     — any error
    """
    explain_sql = f"EXPLAIN (ANALYZE false, BUFFERS, FORMAT JSON) {query_text}"
    try:
        raw = await asyncio.wait_for(conn.fetchval(explain_sql), timeout=10)
    except asyncio.TimeoutError:
        return "fail", None
    except asyncpg.PostgresError:
        return "fail", None

    if raw is None:
        return "fail", None

    if isinstance(raw, str):
        plan = json.loads(raw)
    else:
        plan = raw  # asyncpg may already parse JSON columns

    return "ok", plan


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.get("/{connection_id:path}/{queryid}")
async def get_explain(connection_id: str, queryid: str) -> QueryDetail:
    """Return single-query stats and EXPLAIN plan as one JSON response."""
    dsn = unquote(connection_id)
    errors: list[ExplainError] = []

    # Step 1 — connect
    conn, status, data = await _step_connect(dsn)
    if conn is None:
        errors.append(ExplainError(step="connect", message=data.get("message", "")))
        return QueryDetail(
            connection_id=dsn,
            queryid=queryid,
            query=None,
            query_fingerprint=None,
            calls=None,
            mean_exec_time_ms=None,
            total_exec_time_ms=None,
            stddev_exec_time_ms=None,
            rows_per_call=None,
            shared_blks_hit=None,
            shared_blks_read=None,
            cache_hit_ratio=None,
            score=None,
            plan=None,
            errors=errors,
        )

    try:
        # Step 2 — fetch query stats
        fetch_status, row = await _step_fetch_query(conn, queryid)

        if fetch_status != "ok":
            msg = row.get("message", "Query ID not found in pg_stat_statements.")
            if fetch_status == "not_found":
                msg = "Query ID not found in pg_stat_statements."
            errors.append(ExplainError(step="fetch_query", message=msg))
            return QueryDetail(
                connection_id=dsn,
                queryid=queryid,
                query=None,
                query_fingerprint=None,
                calls=None,
                mean_exec_time_ms=None,
                total_exec_time_ms=None,
                stddev_exec_time_ms=None,
                rows_per_call=None,
                shared_blks_hit=None,
                shared_blks_read=None,
                cache_hit_ratio=None,
                score=None,
                plan=None,
                errors=errors,
            )

        # Step 3 — EXPLAIN
        explain_status, plan = await _step_explain(conn, row["query"])
        if explain_status != "ok":
            errors.append(
                ExplainError(step="explain", message="EXPLAIN failed or was denied.")
            )

    finally:
        await conn.close()

    score = _score_query(
        {
            "mean_exec_time_ms": row.get("mean_exec_time_ms") or 0.0,
            "total_exec_time_ms": row.get("total_exec_time_ms") or 0.0,
            "stddev_exec_time_ms": row.get("stddev_exec_time_ms") or 0.0,
            "cache_hit_ratio": row.get("cache_hit_ratio"),
        },
        row.get("total_exec_time_ms") or 0.0,
    )

    return QueryDetail(
        connection_id=dsn,
        queryid=queryid,
        query=row["query"],
        query_fingerprint=_fingerprint(row["query"]),
        calls=row["calls"],
        mean_exec_time_ms=row.get("mean_exec_time_ms"),
        total_exec_time_ms=row.get("total_exec_time_ms"),
        stddev_exec_time_ms=row.get("stddev_exec_time_ms"),
        rows_per_call=row.get("rows_per_call"),
        shared_blks_hit=row["shared_blks_hit"],
        shared_blks_read=row["shared_blks_read"],
        cache_hit_ratio=row.get("cache_hit_ratio"),
        score=score,
        plan=plan,
        errors=errors,
    )
