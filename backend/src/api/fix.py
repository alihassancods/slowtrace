"""GET /api/fix/{connection_id}/{queryid} — fix wizard recommendations."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote

from fastapi import APIRouter
from pydantic import BaseModel

from api.queries import _fingerprint, _score_query, _step_connect
from api.explain import _step_fetch_query, _step_explain

router = APIRouter(prefix="/api/fix")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class FixError(BaseModel):
    step: str
    message: str


class FixRecommendation(BaseModel):
    id: str
    title: str
    severity: str  # "high" | "medium" | "low"
    explanation: str
    sql: str


class FixReport(BaseModel):
    connection_id: str
    queryid: str
    query_fingerprint: str | None
    score: float | None
    mean_exec_time_ms: float | None
    total_exec_time_ms: float | None
    cache_hit_ratio: float | None
    recommendations: list[FixRecommendation]
    errors: list[FixError]


# ---------------------------------------------------------------------------
# Plan node traversal helper
# ---------------------------------------------------------------------------


def _walk_plan(plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return every plan node from the EXPLAIN JSON tree (depth-first)."""
    nodes: list[dict[str, Any]] = []

    def _recurse(node: dict[str, Any]) -> None:
        nodes.append(node)
        for child in node.get("Plans", []):
            _recurse(child)

    for entry in plan:
        root = entry.get("Plan")
        if root is not None:
            _recurse(root)

    return nodes


# ---------------------------------------------------------------------------
# Recommendation rule functions
# ---------------------------------------------------------------------------

_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _rule_seq_scan(nodes: list[dict[str, Any]]) -> FixRecommendation | None:
    """Rule 1 — Seq Scan on a large table."""
    for node in nodes:
        if node.get("Node Type") == "Seq Scan" and (node.get("Plan Rows") or 0) >= 1000:
            table = node.get("Relation Name")
            rows = node.get("Plan Rows", "?")
            cost_total = node.get("Total Cost", "?")

            if table:
                explanation = (
                    f"The EXPLAIN plan shows a Sequential Scan on '{table}' scanning an estimated "
                    f"{rows:,} rows (total cost {cost_total}). Adding an index on the columns "
                    f"referenced in the WHERE clause will allow PostgreSQL to use an Index Scan, "
                    f"reducing the rows examined from ~{rows:,} to only those matching the predicate."
                )
                sql = f"CREATE INDEX CONCURRENTLY idx_{table}_<column> ON {table} (<column>);"
            else:
                explanation = (
                    f"The EXPLAIN plan shows a Sequential Scan scanning an estimated {rows:,} rows "
                    f"(total cost {cost_total}). Adding an index on the columns referenced in the "
                    "WHERE clause will allow PostgreSQL to use an Index Scan, reducing the rows "
                    "examined proportionally."
                )
                sql = (
                    "-- Replace <table> and <column> with the actual table and filter column(s)\n"
                    "-- visible in the query fingerprint or plan node.\n"
                    "CREATE INDEX CONCURRENTLY idx_<table>_<column> ON <table> (<column>);"
                )

            return FixRecommendation(
                id="seq_scan_large_table",
                title="Sequential scan on large table — consider an index",
                severity="high",
                explanation=explanation,
                sql=sql,
            )
    return None


def _rule_low_cache(row: dict[str, Any]) -> FixRecommendation | None:
    """Rule 2 — Low buffer-cache hit ratio."""
    ratio: float | None = row.get("cache_hit_ratio")
    if ratio is None or ratio >= 95.0:
        return None

    severity = "high" if ratio < 80.0 else "medium"
    pct = f"{ratio:.1f}"
    explanation = (
        f"The cache hit ratio for this query is {pct}%, below the recommended 95% threshold. "
        f"This means roughly {100 - ratio:.0f}% of block reads go to disk. "
        "Increasing shared_buffers (typically to 25% of RAM) and ensuring autovacuum keeps "
        "the table statistics fresh will improve buffer pool effectiveness."
    )
    sql = (
        "-- In postgresql.conf, increase shared_buffers (restart required):\n"
        "shared_buffers = '2GB'   -- adjust to ~25% of total RAM\n\n"
        "-- Refresh table statistics (no restart required):\n"
        "VACUUM ANALYZE;"
    )
    return FixRecommendation(
        id="low_cache_hit",
        title=f"Low buffer-cache hit ratio ({pct}%)",
        severity=severity,
        explanation=explanation,
        sql=sql,
    )


def _rule_high_mean(row: dict[str, Any]) -> FixRecommendation | None:
    """Rule 3 — High mean execution time."""
    mean_ms: float = row.get("mean_exec_time_ms") or 0.0
    if mean_ms < 500.0:
        return None

    severity = "high" if mean_ms >= 2000.0 else "medium"
    total_ms: float = row.get("total_exec_time_ms") or 0.0
    explanation = (
        f"The mean execution time for this query is {mean_ms:,.1f} ms "
        f"(total: {total_ms:,.0f} ms across all calls). "
        "Review the EXPLAIN plan for expensive nodes (Hash Join, Sort, nested loops on large "
        "sets), and check whether work_mem is sufficient to avoid spilling sort/hash operations "
        "to disk."
    )
    sql = (
        "-- Increase work_mem for sort/hash operations in this session:\n"
        "SET work_mem = '64MB';\n\n"
        "-- Or globally in postgresql.conf (requires reload):\n"
        "work_mem = '32MB'\n"
        "-- Then: SELECT pg_reload_conf();"
    )
    return FixRecommendation(
        id="high_mean_exec_time",
        title=f"High mean execution time ({mean_ms:,.0f} ms)",
        severity=severity,
        explanation=explanation,
        sql=sql,
    )


def _rule_high_stddev(row: dict[str, Any]) -> FixRecommendation | None:
    """Rule 4 — High execution time variability."""
    mean_ms: float = row.get("mean_exec_time_ms") or 0.0
    stddev_ms: float = row.get("stddev_exec_time_ms") or 0.0
    if mean_ms < 100.0:
        return None
    if stddev_ms / mean_ms < 0.5:
        return None

    ratio_pct = stddev_ms / mean_ms * 100
    explanation = (
        f"The execution time standard deviation is {stddev_ms:,.1f} ms against a mean of "
        f"{mean_ms:,.1f} ms (coefficient of variation {ratio_pct:.0f}%). High variability typically "
        "indicates lock contention, autovacuum interference, or plan instability. Check "
        "pg_stat_activity for blocking queries and consider resetting pg_stat_statements to get "
        "a fresh baseline."
    )
    sql = (
        "-- Check for current lock contention:\n"
        "SELECT pid, query, wait_event_type, wait_event, state\n"
        "FROM pg_stat_activity\n"
        "WHERE wait_event IS NOT NULL AND state != 'idle';\n\n"
        "-- Reset pg_stat_statements to get a fresh baseline:\n"
        "SELECT pg_stat_statements_reset();"
    )
    return FixRecommendation(
        id="high_stddev",
        title=f"High execution time variability (stddev {stddev_ms:,.0f} ms)",
        severity="medium",
        explanation=explanation,
        sql=sql,
    )


def _rule_missing_vacuum(row: dict[str, Any], nodes: list[dict[str, Any]]) -> FixRecommendation | None:
    """Rule 5 — Table may benefit from VACUUM ANALYZE."""
    has_seq_scan = any(n.get("Node Type") == "Seq Scan" for n in nodes)
    if not has_seq_scan:
        return None

    blks_hit: int = row.get("shared_blks_hit") or 0
    blks_read: int = row.get("shared_blks_read") or 0
    if blks_read <= blks_hit:
        return None

    # Find table name from first seq scan node
    table: str | None = next(
        (n.get("Relation Name") for n in nodes if n.get("Node Type") == "Seq Scan"),
        None,
    )
    table_ref = f"'{table}'" if table else "the scanned table"
    explanation = (
        f"A high ratio of physical disk reads relative to buffer cache hits on the sequentially "
        f"scanned {table_ref} often indicates table bloat (dead tuples) or out-of-date planner "
        "statistics. Running VACUUM ANALYZE reclaims dead tuple space and refreshes row-count "
        "estimates, which may cause the planner to switch from a Seq Scan to an Index Scan."
    )
    sql = (
        f"-- Replace <table> with the actual table name from the EXPLAIN plan:\n"
        f"VACUUM ANALYZE {table or '<table>'};"
    )
    return FixRecommendation(
        id="missing_vacuum",
        title="Table may benefit from VACUUM ANALYZE",
        severity="low",
        explanation=explanation,
        sql=sql,
    )


# ---------------------------------------------------------------------------
# Analysis engine
# ---------------------------------------------------------------------------


def _analyse(row: dict[str, Any], plan: list[dict[str, Any]] | None) -> list[FixRecommendation]:
    """Run all recommendation rules and return results sorted by severity."""
    nodes: list[dict[str, Any]] = _walk_plan(plan) if plan is not None else []

    candidates: list[FixRecommendation | None] = [
        _rule_seq_scan(nodes) if plan is not None else None,
        _rule_low_cache(row),
        _rule_high_mean(row),
        _rule_high_stddev(row),
        _rule_missing_vacuum(row, nodes) if plan is not None else None,
    ]

    results = [r for r in candidates if r is not None]
    results.sort(key=lambda r: _SEVERITY_ORDER.get(r.severity, 99))
    return results


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


def _redact_dsn(dsn: str) -> str:
    """Replace the password in a DSN with '***' so it is safe to return to callers."""
    return re.sub(r"(://[^:@/]*:)[^@/]+(@)", r"\1***\2", dsn)


@router.get("/{connection_id:path}/{queryid}")
async def get_fix(connection_id: str, queryid: str) -> FixReport:
    """Return ranked fix recommendations for a single slow query."""
    dsn = unquote(connection_id)
    safe_id = _redact_dsn(dsn)
    errors: list[FixError] = []

    # Step 1 — connect
    conn, status, data = await _step_connect(dsn)
    if conn is None:
        errors.append(FixError(step="connect", message=data.get("message", "")))
        return FixReport(
            connection_id=safe_id,
            queryid=queryid,
            query_fingerprint=None,
            score=None,
            mean_exec_time_ms=None,
            total_exec_time_ms=None,
            cache_hit_ratio=None,
            recommendations=[],
            errors=errors,
        )

    try:
        # Step 2 — fetch query stats
        fetch_status, row = await _step_fetch_query(conn, queryid)

        if fetch_status != "ok":
            msg = row.get("message", "Query ID not found in pg_stat_statements.")
            if fetch_status == "not_found":
                msg = "Query ID not found in pg_stat_statements."
            errors.append(FixError(step="fetch_query", message=msg))
            return FixReport(
                connection_id=safe_id,
                queryid=queryid,
                query_fingerprint=None,
                score=None,
                mean_exec_time_ms=None,
                total_exec_time_ms=None,
                cache_hit_ratio=None,
                recommendations=[],
                errors=errors,
            )

        # Step 3 — EXPLAIN
        explain_status, plan = await _step_explain(conn, row["query"])
        if explain_status != "ok":
            errors.append(FixError(step="explain", message="EXPLAIN failed or was denied."))
            plan = None

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

    recommendations = _analyse(row, plan)

    return FixReport(
        connection_id=safe_id,
        queryid=queryid,
        query_fingerprint=_fingerprint(row["query"]) if row.get("query") else None,
        score=score,
        mean_exec_time_ms=row.get("mean_exec_time_ms"),
        total_exec_time_ms=row.get("total_exec_time_ms"),
        cache_hit_ratio=row.get("cache_hit_ratio"),
        recommendations=recommendations,
        errors=errors,
    )
