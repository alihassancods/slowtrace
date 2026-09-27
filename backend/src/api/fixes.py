"""Fix management routes.

POST /api/fixes/generate  — generate fix SQL + AI explanation (+ HypoPG proof)
POST /api/fixes/apply     — SSE stream of fix execution progress
POST /api/fixes/rollback  — manually roll back a fix
GET  /api/fixes/history/{connection_id} — list applied fixes
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any

import asyncpg
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.store import CONNECTIONS
from agent.autofix.fix_generator import FixGenerator
from agent.autofix.fix_executor import FixExecutor, _load_history
from agent.autofix.hypopg_validator import HypoPGValidator
from services.ai_explainer import AIExplainer

router = APIRouter()

_fix_generator = FixGenerator()
_fix_executor = FixExecutor()
_ai_explainer = AIExplainer()
_hypopg_validator = HypoPGValidator()

# Problem types whose fix is a real index, and so can be proved with HypoPG.
_INDEX_PROBLEM_TYPES = frozenset({"missing_index", "seq_scan"})

# Planner cost ratios can be extreme on tiny tables; cap the claimed speedup.
_MAX_PLANNER_SPEEDUP = 50.0

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

_FETCH_INDEXES_SQL = """
SELECT indexname
FROM pg_indexes
WHERE tablename = $1
  AND schemaname NOT IN ('pg_catalog', 'information_schema')
"""

_FETCH_COLUMNS_SQL = """
SELECT column_name
FROM information_schema.columns
WHERE table_name = $1
  AND table_schema NOT IN ('pg_catalog', 'information_schema')
ORDER BY ordinal_position
"""

_FETCH_ROW_ESTIMATE_SQL = """
SELECT reltuples::bigint
FROM pg_class
WHERE relname = $1 AND relkind = 'r'
LIMIT 1
"""

# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class GenerateRequest(BaseModel):
    connection_id: str
    queryid: str


class GenerateResponse(BaseModel):
    problem: dict[str, Any]
    fix_sql: str
    rollback_sql: str
    expected_impact: dict[str, Any]
    ai_explanation: str
    # HypoPG what-if proof for index fixes; None for non-index problems.
    hypopg_validation: dict[str, Any] | None = None


class SaveRequest(BaseModel):
    connection_id: str
    queryid: str
    fix_sql: str
    rollback_sql: str
    # Expected impact from /fixes/generate, so the result page can show real
    # before/after numbers instead of zeros.
    query_time_before_ms: float | None = None
    query_time_after_ms: float | None = None
    time_saved_per_day_minutes: float | None = None


class SaveResponse(BaseModel):
    fix_id: str


class ApplyRequest(BaseModel):
    connection_id: str
    fix_id: str


class RollbackRequest(BaseModel):
    fix_id: str
    connection_id: str


class RollbackResponse(BaseModel):
    status: str
    message: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _connect(dsn: str) -> asyncpg.Connection:
    """Open a connection or raise HTTPException on failure."""
    try:
        return await asyncio.wait_for(asyncpg.connect(dsn=dsn), timeout=5)
    except asyncpg.InvalidPasswordError:
        raise HTTPException(status_code=502, detail="Database authentication failed.")
    except asyncpg.InvalidCatalogNameError:
        raise HTTPException(status_code=502, detail="Database not found.")
    except OSError:
        raise HTTPException(status_code=502, detail="Database host not reachable.")
    except Exception:
        raise HTTPException(status_code=502, detail="Database connection failed.")


async def _fetch_query_row(conn: asyncpg.Connection, queryid: str) -> dict[str, Any]:
    """Fetch one row from pg_stat_statements or raise 404."""
    try:
        row = await asyncio.wait_for(conn.fetchrow(_FETCH_SINGLE_SQL, queryid), timeout=10)
    except Exception:
        raise HTTPException(status_code=502, detail="Failed to fetch query statistics.")
    if row is None:
        raise HTTPException(status_code=404, detail="Query ID not found in pg_stat_statements.")
    return dict(row)


async def _introspect_table(
    conn: asyncpg.Connection, table: str
) -> tuple[list[str], list[str], int | None]:
    """Return ``(index_names, columns, row_estimate)`` for *table*.

    Each lookup is best-effort: a permission error on one does not lose the
    others, and an empty result simply means "no facts to add".
    """
    indexes: list[str] = []
    columns: list[str] = []
    row_estimate: int | None = None

    try:
        rows = await asyncio.wait_for(conn.fetch(_FETCH_INDEXES_SQL, table), timeout=10)
        indexes = [r["indexname"] for r in rows]
    except Exception:
        pass

    try:
        rows = await asyncio.wait_for(conn.fetch(_FETCH_COLUMNS_SQL, table), timeout=10)
        columns = [r["column_name"] for r in rows]
    except Exception:
        pass

    try:
        value = await asyncio.wait_for(
            conn.fetchval(_FETCH_ROW_ESTIMATE_SQL, table), timeout=10
        )
        row_estimate = int(value) if value is not None else None
    except Exception:
        pass

    return indexes, columns, row_estimate


async def _validate_index_fix(
    conn: asyncpg.Connection,
    query_text: str,
    problem: dict[str, Any],
    fix_info: dict[str, Any],
) -> dict[str, Any] | None:
    """Prove an index suggestion with HypoPG; ``None`` for non-index fixes.

    Must run on the live connection: hypothetical indexes are session-local.
    """
    if problem.get("type") not in _INDEX_PROBLEM_TYPES:
        return None

    return await _hypopg_validator.validate_index_fix(
        conn,
        table=problem.get("table"),
        columns=list(problem.get("columns") or []),
        slow_query=query_text,
        index_ddl=fix_info.get("fix_sql"),
    )


def _apply_planner_impact(
    impact: dict[str, Any],
    validation: dict[str, Any],
    query_stats: dict[str, Any],
) -> dict[str, Any]:
    """Replace the guessed speedup with the planner's own cost ratio.

    Only a validated fix the planner would actually use rewrites the numbers;
    anything else keeps the heuristic estimate and says so.
    """
    refined = {**impact, "estimation_basis": "heuristic"}

    baseline_cost = float(validation.get("baseline_cost") or 0.0)
    improved_cost = validation.get("improved_cost")
    if (
        not validation.get("validated")
        or not validation.get("planner_would_use_index")
        or baseline_cost <= 0
        or improved_cost is None
        or float(improved_cost) >= baseline_cost
    ):
        return refined

    before_ms = float(query_stats.get("mean_exec_time_ms", 0))
    if before_ms <= 0:
        return refined

    speedup = min(baseline_cost / float(improved_cost), _MAX_PLANNER_SPEEDUP)
    after_ms = before_ms / speedup
    calls_per_day = float(query_stats.get("calls", 1))

    refined.update(
        {
            "after_ms": round(after_ms, 3),
            "speedup_factor": round(speedup, 2),
            "time_saved_per_day_minutes": round(
                (before_ms - after_ms) * calls_per_day / 1000 / 60, 2
            ),
            "estimation_basis": "hypopg_planner",
        }
    )
    return refined


# ---------------------------------------------------------------------------
# POST /api/fixes/generate
# ---------------------------------------------------------------------------


@router.post("/fixes/generate", response_model=GenerateResponse)
async def generate_fix(body: GenerateRequest) -> GenerateResponse:
    """Detect the problem for a query and generate fix SQL with AI explanation.

    Index suggestions are additionally proved with HypoPG when the monitored
    database has the extension: the planner costs the query against a
    hypothetical index, the index is dropped again, and the impact numbers are
    re-derived from the measured cost ratio instead of a fixed guess.
    """
    dsn = CONNECTIONS.get(body.connection_id)
    if dsn is None:
        raise HTTPException(status_code=404, detail="Connection ID not found.")

    conn = await _connect(dsn)
    try:
        r = await _fetch_query_row(conn, body.queryid)
        query_text: str = r.get("query", "")
        query_stats = {
            "calls": r.get("calls", 0),
            "mean_exec_time_ms": r.get("mean_exec_time_ms") or 0.0,
            "total_exec_time_ms": r.get("total_exec_time_ms") or 0.0,
            "avg_rows_returned": r.get("rows_per_call") or 0.0,
        }

        # Enrich detection with facts from the live database so we never suggest
        # an index that already exists, and can list real columns for SELECT *.
        table = _fix_generator.target_table(query_text)
        schema: dict[str, Any] = {"columns": {}}
        table_row_counts: dict[str, int] = {}
        if table:
            existing_indexes, columns, row_estimate = await _introspect_table(conn, table)
            query_stats["existing_indexes"] = existing_indexes
            if columns:
                schema["columns"][table] = columns
            if row_estimate is not None:
                table_row_counts[table] = row_estimate

        query_stats["table_row_counts"] = table_row_counts

        problem = _fix_generator.detect_problem(query_text, query_stats)
        fix_info = _fix_generator.generate_fix_sql(problem, schema)
        expected_impact = _fix_generator.calculate_expected_impact(problem, query_stats)

        # HypoPG needs the same session the hypothetical index lives in, so the
        # validation runs here rather than after the connection is closed.
        validation = await _validate_index_fix(conn, query_text, problem, fix_info)
        if validation is not None:
            expected_impact = _apply_planner_impact(
                expected_impact, validation, query_stats
            )
    finally:
        await conn.close()

    ai_explanation = await _ai_explainer.explain_problem(query_text, problem, query_stats)

    return GenerateResponse(
        problem=problem,
        fix_sql=fix_info.get("fix_sql", ""),
        rollback_sql=fix_info.get("rollback_sql", ""),
        expected_impact=expected_impact,
        ai_explanation=ai_explanation,
        hypopg_validation=validation,
    )


# ---------------------------------------------------------------------------
# POST /api/fixes/save  — persist a pending fix to history and return fix_id
# ---------------------------------------------------------------------------


@router.post("/fixes/save", response_model=SaveResponse)
async def save_fix(body: SaveRequest) -> SaveResponse:
    """Save a generated fix to fix_history.json and return a fix_id.

    This must be called before POST /api/fixes/apply so the executor can
    look up the fix SQL by ID.
    """
    import uuid as _uuid
    from datetime import datetime, timezone

    if body.connection_id not in CONNECTIONS:
        raise HTTPException(status_code=404, detail="Connection ID not found.")

    fix_id = str(_uuid.uuid4())
    record: dict[str, Any] = {
        "fix_id": fix_id,
        "status": "pending",
        "fix_sql": body.fix_sql,
        "rollback_sql": body.rollback_sql,
        "connection_id": body.connection_id,
        "queryid": body.queryid,
        "query_time_before_ms": body.query_time_before_ms or 0.0,
        "query_time_after_ms": body.query_time_after_ms or 0.0,
        "time_saved_per_day_minutes": body.time_saved_per_day_minutes or 0.0,
        "health_before": None,
        "health_after": None,
        "started_at": datetime.now(tz=timezone.utc).isoformat(),
        "finished_at": None,
    }
    from agent.autofix.fix_executor import _upsert_record
    _upsert_record(record)
    return SaveResponse(fix_id=fix_id)


# ---------------------------------------------------------------------------
# POST /api/fixes/apply  (SSE stream)
# ---------------------------------------------------------------------------


def _sse(event: str, data: dict[str, Any]) -> str:
    # default=str keeps one exotic column type (Decimal, date, …) from taking
    # down the whole stream.
    return f"data: {json.dumps({'event': event, 'data': data}, default=str)}\n\n"


async def _apply_stream(
    dsn: str,
    fix_id: str,
) -> AsyncGenerator[str, None]:
    """Execute a pending fix and stream progress as SSE."""
    history = _load_history()
    record = next((e for e in history if e.get("fix_id") == fix_id), None)

    if record is None:
        yield _sse("error", {"message": f"Fix ID '{fix_id}' not found in history."})
        return

    yield _sse("started", {"fix_id": fix_id, "fix_sql": record.get("fix_sql", "")})

    try:
        conn: asyncpg.Connection = await asyncio.wait_for(asyncpg.connect(dsn=dsn), timeout=5)
    except Exception:
        yield _sse("error", {"message": "Database connection failed."})
        return

    try:
        yield _sse("executing", {"message": "Applying fix SQL…"})
        result = await _fix_executor.execute(record, conn)
    finally:
        await conn.close()

    status = result.get("status", "unknown")
    if status == "success":
        yield _sse("success", result)
    elif status == "auto_rolled_back":
        yield _sse("auto_rolled_back", result)
    else:
        yield _sse("error", result)


@router.post("/fixes/apply")
async def apply_fix(body: ApplyRequest) -> StreamingResponse:
    """Stream fix execution progress as Server-Sent Events."""
    dsn = CONNECTIONS.get(body.connection_id)
    if dsn is None:
        raise HTTPException(status_code=404, detail="Connection ID not found.")

    return StreamingResponse(
        _apply_stream(dsn, body.fix_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# POST /api/fixes/rollback
# ---------------------------------------------------------------------------


@router.post("/fixes/rollback", response_model=RollbackResponse)
async def rollback_fix(body: RollbackRequest) -> RollbackResponse:
    """Manually roll back a previously applied fix."""
    dsn = CONNECTIONS.get(body.connection_id)
    if dsn is None:
        raise HTTPException(status_code=404, detail="Connection ID not found.")

    conn = await _connect(dsn)
    try:
        result = await _fix_executor.rollback(body.fix_id, conn)
    finally:
        await conn.close()

    status = result.get("status", "error")
    if status == "manually_rolled_back":
        return RollbackResponse(
            status="success",
            message=f"Fix {body.fix_id} rolled back at {result.get('rolled_back_at', '')}.",
        )
    return RollbackResponse(
        status="error",
        message=result.get("error", "Rollback failed."),
    )


# ---------------------------------------------------------------------------
# GET /api/fixes/history/{connection_id}
# ---------------------------------------------------------------------------


@router.get("/fixes/history/{connection_id}")
async def get_fix_history(connection_id: str) -> list[dict[str, Any]]:
    """Return list of applied fixes for this connection_id only."""
    if connection_id not in CONNECTIONS:
        raise HTTPException(status_code=404, detail="Connection ID not found.")
    history = _load_history()
    # Return only records belonging to this connection_id; fall back to all
    # records if none are tagged (backwards-compatible with older entries).
    scoped = [r for r in history if r.get("connection_id") == connection_id]
    return scoped if scoped else [r for r in history if "connection_id" not in r]
