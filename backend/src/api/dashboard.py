"""GET /api/dashboard/{connection_id} — aggregated health + query metrics."""

from __future__ import annotations

import asyncio
import json
from urllib.parse import unquote

from fastapi import APIRouter
from pydantic import BaseModel

from api.health import HealthReport, _run_health_stream
from api.queries import QueryReport, _run_query_stream

router = APIRouter(prefix="/api/dashboard")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class DashboardError(BaseModel):
    step: str
    message: str


class TopQuery(BaseModel):
    queryid: str
    query_fingerprint: str
    calls: int
    mean_exec_time_ms: float
    total_exec_time_ms: float
    cache_hit_ratio: float
    score: float


class QuerySummary(BaseModel):
    total_queries: int
    top_queries: list[TopQuery]
    avg_score: float
    high_priority_count: int
    total_exec_time_ms: float
    errors: list[DashboardError]


class DashboardReport(BaseModel):
    connection_id: str
    health: HealthReport | None
    queries: QuerySummary | None
    errors: list[DashboardError]


# ---------------------------------------------------------------------------
# Stream helpers
# ---------------------------------------------------------------------------


async def _run_health(dsn: str) -> HealthReport:
    """Drive the health SSE stream to completion and return the final HealthReport."""
    async for raw in _run_health_stream(dsn):
        if raw.startswith("data: "):
            ev = json.loads(raw[len("data: "):])
            if ev["step"] == "result":
                return HealthReport(**ev["data"])
    raise RuntimeError("Health stream ended without result event")


async def _run_queries(dsn: str) -> QueryReport:
    """Drive the queries SSE stream to completion and return the final QueryReport."""
    async for raw in _run_query_stream(dsn):
        if raw.startswith("data: "):
            ev = json.loads(raw[len("data: "):])
            if ev["step"] == "result":
                return QueryReport(**ev["data"])
    raise RuntimeError("Queries stream ended without result event")


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _build_query_summary(report: QueryReport) -> QuerySummary:
    queries = report.queries
    top_queries = [
        TopQuery(
            queryid=q.queryid,
            query_fingerprint=q.query_fingerprint,
            calls=q.calls,
            mean_exec_time_ms=q.mean_exec_time_ms,
            total_exec_time_ms=q.total_exec_time_ms,
            cache_hit_ratio=q.cache_hit_ratio,
            score=q.score,
        )
        for q in queries[:10]
    ]
    avg_score = round(sum(q.score for q in queries) / len(queries), 1) if queries else 0.0
    high_priority_count = sum(1 for q in queries if q.score >= 70)
    total_exec_time_ms = sum(q.total_exec_time_ms for q in queries)
    errors = [DashboardError(step=e.step, message=e.message) for e in report.errors]
    return QuerySummary(
        total_queries=report.total_queries,
        top_queries=top_queries,
        avg_score=avg_score,
        high_priority_count=high_priority_count,
        total_exec_time_ms=total_exec_time_ms,
        errors=errors,
    )


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.get("/{connection_id:path}")
async def get_dashboard(connection_id: str) -> DashboardReport:
    """Fan out to health + queries in parallel, aggregate, and return."""
    dsn = unquote(connection_id)
    errors: list[DashboardError] = []

    health_result, query_result = await asyncio.gather(
        _run_health(dsn),
        _run_queries(dsn),
        return_exceptions=True,
    )

    health: HealthReport | None = None
    if isinstance(health_result, Exception):
        errors.append(DashboardError(step="health", message=str(health_result)))
    else:
        health = health_result

    queries: QuerySummary | None = None
    if isinstance(query_result, Exception):
        errors.append(DashboardError(step="queries", message=str(query_result)))
    else:
        queries = _build_query_summary(query_result)

    return DashboardReport(connection_id=dsn, health=health, queries=queries, errors=errors)
