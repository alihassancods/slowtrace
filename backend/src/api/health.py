"""GET /api/health/{connection_id}[/stream] — PostgreSQL health monitoring via SSE."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any
from urllib.parse import unquote

import asyncpg
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

router = APIRouter(prefix="/api/health")

# ---------------------------------------------------------------------------
# Scoring constants
# ---------------------------------------------------------------------------

WEIGHTS: dict[str, int] = {
    "connections": 10,
    "cache_hit_ratio": 15,
    "replication_lag": 15,
    "table_bloat": 10,
    "lock_contention": 15,
    "long_transactions": 15,
    "index_usage": 20,
}


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class CheckResult(BaseModel):
    name: str
    status: str  # "ok" | "warning" | "fail"
    value: float | None = None
    unit: str | None = None
    threshold: float | None = None
    message: str
    deduction: int = 0


class Deduction(BaseModel):
    check: str
    points: int
    reason: str


class HealthError(BaseModel):
    check: str
    message: str


class HealthReport(BaseModel):
    connection_id: str
    score: int | None
    grade: str | None
    checks: list[CheckResult]
    deductions: list[Deduction]
    errors: list[HealthError]


# ---------------------------------------------------------------------------
# SSE helper
# ---------------------------------------------------------------------------


def _event(step: str, status: str, data: dict[str, Any]) -> str:
    """Format a single SSE data line."""
    payload = json.dumps({"step": step, "status": status, "data": data})
    return f"data: {payload}\n\n"


# ---------------------------------------------------------------------------
# Score service
# ---------------------------------------------------------------------------


def _grade(score: int) -> str:
    if score >= 90:
        return "A"
    if score >= 75:
        return "B"
    if score >= 60:
        return "C"
    if score >= 45:
        return "D"
    return "F"


def _compute_score(
    checks: list[CheckResult],
) -> tuple[int, str, list[Deduction]]:
    """Compute weighted health score, grade, and deduction list."""
    base = 100.0
    deductions: list[Deduction] = []
    for check in checks:
        weight = WEIGHTS.get(check.name, 0)
        if check.status == "fail":
            pts = weight
        elif check.status == "warning":
            pts = weight // 2
        else:
            pts = 0
        if pts:
            base -= pts
            deductions.append(
                Deduction(check=check.name, points=pts, reason=check.message)
            )
    score = max(0, int(base))
    return score, _grade(score), deductions


# ---------------------------------------------------------------------------
# Timeout helper
# ---------------------------------------------------------------------------


async def _with_timeout(
    coro: asyncio.coroutines, seconds: int = 10
) -> tuple[str, dict[str, Any]]:
    """Run *coro* with a timeout; return a fail tuple on TimeoutError."""
    try:
        return await asyncio.wait_for(coro, timeout=seconds)
    except asyncio.TimeoutError:
        return "fail", {"message": "Check timed out after 10 seconds."}


# ---------------------------------------------------------------------------
# Connect step
# ---------------------------------------------------------------------------


async def _check_connect(
    dsn: str,
) -> tuple[asyncpg.Connection | None, str, dict[str, Any]]:
    """Open a raw connection (5-second timeout)."""
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
        msg = str(exc)
        if "SSL" in msg.upper():
            return None, "fail", {"message": "SSL connection required by server."}
        return None, "fail", {"message": f"Connection error: {msg}"}


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


async def _check_connections(
    conn: asyncpg.Connection,
) -> tuple[str, dict[str, Any]]:
    """Active connections vs max_connections."""
    try:
        active: int = await conn.fetchval(
            "SELECT count(*) FROM pg_stat_activity WHERE state != 'idle'"
        )
        max_conn_str: str = await conn.fetchval("SHOW max_connections")
        max_conn = int(max_conn_str)
        ratio = active / max_conn if max_conn else 0.0
        pct = round(ratio * 100, 1)
        data: dict[str, Any] = {
            "active": int(active),
            "max": max_conn,
            "ratio_pct": pct,
        }
        msg = f"{active} active connections ({pct}% of max_connections={max_conn})"
        if ratio >= 0.90:
            return "fail", {**data, "message": msg}
        if ratio >= 0.70:
            return "warning", {**data, "message": msg}
        return "ok", {**data, "message": msg}
    except asyncpg.PostgresError as exc:
        return "fail", {"message": f"Could not query pg_stat_activity: {exc}"}


async def _check_cache_hit_ratio(
    conn: asyncpg.Connection,
) -> tuple[str, dict[str, Any]]:
    """Buffer cache hit ratio from pg_statio_user_tables."""
    try:
        row = await conn.fetchrow(
            """
            SELECT
                sum(heap_blks_hit)  AS hits,
                sum(heap_blks_read) AS reads
            FROM pg_statio_user_tables
            """
        )
        hits = row["hits"] or 0
        reads = row["reads"] or 0
        total = hits + reads
        if total == 0:
            return "ok", {
                "ratio_pct": None,
                "message": "No table I/O recorded yet (database may be new).",
            }
        ratio = hits / total * 100
        ratio_r = round(ratio, 2)
        msg = f"Cache hit ratio is {ratio_r}%"
        data: dict[str, Any] = {"ratio_pct": ratio_r}
        if ratio < 80.0:
            return "fail", {**data, "message": f"{msg} (target ≥ 95%, critical < 80%)"}
        if ratio < 95.0:
            return "warning", {**data, "message": f"{msg} (target ≥ 95%)"}
        return "ok", {**data, "message": f"{msg} (target ≥ 95%)"}
    except asyncpg.InsufficientPrivilegeError:
        return "warning", {
            "message": "Insufficient privilege to read pg_statio_user_tables.",
            "fix": "GRANT SELECT ON pg_catalog.pg_statio_user_tables TO <user>;",
        }
    except asyncpg.PostgresError as exc:
        return "fail", {"message": f"Cache hit ratio check failed: {exc}"}


async def _check_replication_lag(
    conn: asyncpg.Connection,
) -> tuple[str, dict[str, Any]]:
    """Max replication lag across all standbys (seconds)."""
    try:
        rows = await conn.fetch(
            """
            SELECT
                application_name,
                EXTRACT(EPOCH FROM write_lag)  AS write_lag_s,
                EXTRACT(EPOCH FROM flush_lag)  AS flush_lag_s,
                EXTRACT(EPOCH FROM replay_lag) AS replay_lag_s
            FROM pg_stat_replication
            """
        )
    except asyncpg.InsufficientPrivilegeError:
        return "warning", {
            "message": "Insufficient privilege to read pg_stat_replication.",
            "fix": "GRANT pg_monitor TO <user>;",
        }
    except asyncpg.PostgresError as exc:
        return "fail", {"message": f"Replication lag check failed: {exc}"}

    if not rows:
        return "ok", {"message": "No replicas configured — not a primary or no standbys."}

    max_lag = 0.0
    standbys = []
    for row in rows:
        lag = max(
            row["write_lag_s"] or 0.0,
            row["flush_lag_s"] or 0.0,
            row["replay_lag_s"] or 0.0,
        )
        standbys.append({"name": row["application_name"], "max_lag_s": round(lag, 2)})
        if lag > max_lag:
            max_lag = lag

    data: dict[str, Any] = {"max_lag_s": round(max_lag, 2), "standbys": standbys}
    msg = f"Max replication lag is {round(max_lag, 2)}s across {len(rows)} standby(s)"
    if max_lag > 300:
        return "fail", {**data, "message": f"{msg} (critical > 300s)"}
    if max_lag > 30:
        return "warning", {**data, "message": f"{msg} (warn > 30s)"}
    return "ok", {**data, "message": msg}


async def _check_table_bloat(
    conn: asyncpg.Connection,
) -> tuple[str, dict[str, Any]]:
    """Estimate table bloat via pg_class statistics (no pgstattuple required)."""
    try:
        rows = await conn.fetch(
            """
            SELECT
                n.nspname || '.' || c.relname AS table_name,
                c.relpages,
                c.reltuples,
                pg_relation_size(c.oid) AS actual_bytes
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind = 'r'
              AND n.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
              AND c.relpages > 0
              AND c.reltuples > 0
            ORDER BY
                (c.relpages - CEIL(c.reltuples / (8192.0 / 24))) / c.relpages DESC NULLS LAST
            LIMIT 5
            """
        )
    except asyncpg.InsufficientPrivilegeError:
        return "warning", {
            "message": "Insufficient privilege to estimate table bloat.",
            "fix": "GRANT SELECT ON pg_catalog.pg_class TO <user>;",
        }
    except asyncpg.PostgresError as exc:
        return "fail", {"message": f"Table bloat check failed: {exc}"}

    if not rows:
        return "ok", {"message": "No user tables found to analyse.", "tables": []}

    tables = []
    worst_pct = 0.0
    for row in rows:
        relpages = row["relpages"]
        reltuples = row["reltuples"]
        # Rough live-tuple pages: assume ~24 bytes per tuple header (not data)
        estimated_live = max(1, reltuples * 24 / 8192)
        bloat_pct = max(0.0, (relpages - estimated_live) / relpages * 100)
        bloat_pct = round(bloat_pct, 1)
        tables.append({"table": row["table_name"], "bloat_pct": bloat_pct})
        if bloat_pct > worst_pct:
            worst_pct = bloat_pct

    data: dict[str, Any] = {"worst_bloat_pct": round(worst_pct, 1), "tables": tables}
    msg = f"Worst table bloat estimate is {round(worst_pct, 1)}%"
    if worst_pct > 60:
        return "fail", {**data, "message": f"{msg} (critical > 60%)"}
    if worst_pct > 30:
        return "warning", {**data, "message": f"{msg} (warn > 30%)"}
    return "ok", {**data, "message": msg}


async def _check_lock_contention(
    conn: asyncpg.Connection,
) -> tuple[str, dict[str, Any]]:
    """Count lock-waiting queries."""
    try:
        rows = await conn.fetch(
            """
            SELECT
                blocked.pid          AS blocked_pid,
                blocked.query        AS blocked_query,
                blocking.pid         AS blocking_pid,
                blocking.query       AS blocking_query
            FROM pg_locks bl
            JOIN pg_stat_activity blocked  ON blocked.pid  = bl.pid
            JOIN pg_locks          bla     ON bla.transactionid = bl.transactionid
                                          AND bla.pid != bl.pid
            JOIN pg_stat_activity blocking ON blocking.pid = bla.pid
            WHERE NOT bl.granted
            LIMIT 20
            """
        )
    except asyncpg.InsufficientPrivilegeError:
        return "warning", {
            "message": "Insufficient privilege to read pg_locks.",
            "fix": "GRANT pg_monitor TO <user>;",
        }
    except asyncpg.PostgresError as exc:
        return "fail", {"message": f"Lock contention check failed: {exc}"}

    waiting = len(rows)
    blocked = [
        {
            "blocked_pid": row["blocked_pid"],
            "blocked_query": (row["blocked_query"] or "")[:200],
            "blocking_pid": row["blocking_pid"],
            "blocking_query": (row["blocking_query"] or "")[:200],
        }
        for row in rows
    ]
    data: dict[str, Any] = {"waiting_count": waiting, "blocked": blocked}
    msg = f"{waiting} lock-waiting query(s) detected"
    if waiting >= 5:
        return "fail", {**data, "message": f"{msg} (critical ≥ 5)"}
    if waiting >= 1:
        return "warning", {**data, "message": msg}
    return "ok", {"waiting_count": 0, "blocked": [], "message": "No lock contention detected."}


async def _check_long_transactions(
    conn: asyncpg.Connection,
) -> tuple[str, dict[str, Any]]:
    """Idle-in-transaction sessions open for more than 5 minutes."""
    try:
        rows = await conn.fetch(
            """
            SELECT
                pid,
                usename,
                EXTRACT(EPOCH FROM (now() - xact_start))::int AS age_s,
                LEFT(query, 200) AS query
            FROM pg_stat_activity
            WHERE state = 'idle in transaction'
              AND xact_start IS NOT NULL
              AND now() - xact_start > interval '5 minutes'
            ORDER BY xact_start ASC
            """
        )
    except asyncpg.InsufficientPrivilegeError:
        return "warning", {
            "message": "Insufficient privilege to read pg_stat_activity.",
            "fix": "GRANT pg_monitor TO <user>;",
        }
    except asyncpg.PostgresError as exc:
        return "fail", {"message": f"Long transaction check failed: {exc}"}

    count = len(rows)
    txns = [
        {"pid": row["pid"], "user": row["usename"], "age_s": row["age_s"], "query": row["query"]}
        for row in rows
    ]
    data: dict[str, Any] = {"count": count, "transactions": txns}
    msg = f"{count} long-running idle-in-transaction session(s) (> 5 min)"
    if count >= 3:
        return "fail", {**data, "message": f"{msg} (critical ≥ 3)"}
    if count >= 1:
        return "warning", {**data, "message": msg}
    return "ok", {"count": 0, "transactions": [], "message": "No long idle-in-transaction sessions."}


async def _check_index_usage(
    conn: asyncpg.Connection,
) -> tuple[str, dict[str, Any]]:
    """Tables with heavy sequential scans that likely need an index."""
    try:
        rows = await conn.fetch(
            """
            SELECT
                schemaname || '.' || relname AS table_name,
                seq_scan,
                idx_scan,
                seq_tup_read,
                CASE WHEN (seq_scan + idx_scan) > 0
                     THEN ROUND(idx_scan::numeric / (seq_scan + idx_scan) * 100, 1)
                     ELSE 0
                END AS idx_pct
            FROM pg_stat_user_tables
            WHERE seq_scan > 0
              AND seq_tup_read > 1000
              AND (seq_scan + idx_scan) > 0
              AND idx_scan::float / (seq_scan + idx_scan) < 0.5
            ORDER BY seq_tup_read DESC
            LIMIT 5
            """
        )
    except asyncpg.InsufficientPrivilegeError:
        return "warning", {
            "message": "Insufficient privilege to read pg_stat_user_tables.",
            "fix": "GRANT SELECT ON pg_catalog.pg_stat_user_tables TO <user>;",
        }
    except asyncpg.PostgresError as exc:
        return "fail", {"message": f"Index usage check failed: {exc}"}

    count = len(rows)
    tables = [
        {
            "table": row["table_name"],
            "seq_scan": row["seq_scan"],
            "idx_scan": row["idx_scan"],
            "seq_tup_read": row["seq_tup_read"],
            "idx_pct": float(row["idx_pct"]),
        }
        for row in rows
    ]
    data: dict[str, Any] = {"under_indexed_count": count, "tables": tables}
    msg = f"{count} table(s) relying heavily on sequential scans"
    if count >= 3:
        return "fail", {**data, "message": f"{msg} (critical ≥ 3)"}
    if count >= 1:
        return "warning", {**data, "message": msg}
    return "ok", {"under_indexed_count": 0, "tables": [], "message": "Index usage looks healthy."}


# ---------------------------------------------------------------------------
# Ordered check pipeline
# ---------------------------------------------------------------------------

_CHECKS = [
    ("connections", _check_connections),
    ("cache_hit_ratio", _check_cache_hit_ratio),
    ("replication_lag", _check_replication_lag),
    ("table_bloat", _check_table_bloat),
    ("lock_contention", _check_lock_contention),
    ("long_transactions", _check_long_transactions),
    ("index_usage", _check_index_usage),
]


def _build_check_result(name: str, status: str, data: dict[str, Any]) -> CheckResult:
    """Convert raw check output to a CheckResult model."""
    return CheckResult(
        name=name,
        status=status,
        message=data.get("message", ""),
        deduction=0,  # will be filled in by _compute_score
    )


# ---------------------------------------------------------------------------
# SSE generator
# ---------------------------------------------------------------------------


async def _run_health_stream(dsn: str) -> AsyncGenerator[str, None]:
    """Yield SSE events for each health check, then a final scored result."""
    checks: list[CheckResult] = []
    errors: list[HealthError] = []

    # Connect
    conn, status, data = await _check_connect(dsn)
    yield _event("connect", status, data)

    if conn is None:
        errors.append(HealthError(check="connect", message=data.get("message", "")))
        yield _event(
            "result",
            "fail",
            HealthReport(
                connection_id=dsn,
                score=None,
                grade=None,
                checks=[],
                deductions=[],
                errors=errors,
            ).model_dump(),
        )
        return

    try:
        for check_name, check_fn in _CHECKS:
            status, data = await _with_timeout(check_fn(conn))
            cr = _build_check_result(check_name, status, data)
            checks.append(cr)
            yield _event(check_name, status, data)

    finally:
        await conn.close()

    score, grade, deductions = _compute_score(checks)
    # Annotate each check with its actual deduction amount
    deduction_map = {d.check: d.points for d in deductions}
    for cr in checks:
        cr.deduction = deduction_map.get(cr.name, 0)

    report = HealthReport(
        connection_id=dsn,
        score=score,
        grade=grade,
        checks=checks,
        deductions=deductions,
        errors=errors,
    )
    yield _event("result", "ok" if score >= 60 else "fail", report.model_dump())


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/{connection_id:path}/stream")
async def stream_health(connection_id: str) -> StreamingResponse:
    """Stream health check results as Server-Sent Events."""
    dsn = unquote(connection_id)
    return StreamingResponse(
        _run_health_stream(dsn),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{connection_id:path}")
async def get_health(connection_id: str) -> HealthReport:
    """Return a complete health report as JSON (runs all checks sequentially)."""
    dsn = unquote(connection_id)
    checks: list[CheckResult] = []
    errors: list[HealthError] = []

    conn, status, data = await _check_connect(dsn)
    if conn is None:
        errors.append(HealthError(check="connect", message=data.get("message", "")))
        return HealthReport(
            connection_id=dsn,
            score=None,
            grade=None,
            checks=[],
            deductions=[],
            errors=errors,
        )

    try:
        for check_name, check_fn in _CHECKS:
            status, data = await _with_timeout(check_fn(conn))
            cr = _build_check_result(check_name, status, data)
            checks.append(cr)
    finally:
        await conn.close()

    score, grade, deductions = _compute_score(checks)
    deduction_map = {d.check: d.points for d in deductions}
    for cr in checks:
        cr.deduction = deduction_map.get(cr.name, 0)

    return HealthReport(
        connection_id=dsn,
        score=score,
        grade=grade,
        checks=checks,
        deductions=deductions,
        errors=errors,
    )
