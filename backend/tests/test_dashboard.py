"""Tests for GET /api/dashboard/{connection_id}."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from api.dashboard import _build_query_summary, DashboardError, TopQuery, QuerySummary
from api.health import CheckResult, Deduction, HealthError, HealthReport
from api.queries import QueryReport, SlowQuery, ScanError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_health_report(score: int = 87, grade: str = "B") -> HealthReport:
    return HealthReport(
        connection_id="postgresql://localhost/db",
        score=score,
        grade=grade,
        checks=[
            CheckResult(
                name="connections",
                status="ok",
                value=42.0,
                unit="active_connections",
                threshold=80.0,
                message="42 active connections",
                deduction=0,
            )
        ],
        deductions=[],
        errors=[],
    )


def _make_slow_query(queryid: str = "1", score: float = 50.0) -> SlowQuery:
    return SlowQuery(
        queryid=queryid,
        query="SELECT 1",
        query_fingerprint="select ?",
        calls=100,
        mean_exec_time_ms=10.0,
        total_exec_time_ms=1000.0,
        stddev_exec_time_ms=2.0,
        rows_per_call=1.0,
        shared_blks_hit=900,
        shared_blks_read=100,
        cache_hit_ratio=90.0,
        score=score,
    )


def _make_query_report(queries: list[SlowQuery] | None = None) -> QueryReport:
    qs = queries if queries is not None else [_make_slow_query()]
    return QueryReport(
        connection_id="postgresql://localhost/db",
        total_queries=len(qs),
        queries=qs,
        errors=[],
    )


# ---------------------------------------------------------------------------
# Unit tests — _build_query_summary
# ---------------------------------------------------------------------------


class TestBuildQuerySummary:
    def test_empty_queries_returns_zeros(self):
        report = _make_query_report(queries=[])
        summary = _build_query_summary(report)
        assert summary.total_queries == 0
        assert summary.top_queries == []
        assert summary.avg_score == 0.0
        assert summary.high_priority_count == 0
        assert summary.total_exec_time_ms == 0.0

    def test_five_queries_all_returned(self):
        qs = [_make_slow_query(queryid=str(i), score=float(i * 10)) for i in range(5)]
        report = _make_query_report(queries=qs)
        summary = _build_query_summary(report)
        assert len(summary.top_queries) == 5

    def test_fifteen_queries_returns_top_ten(self):
        # Queries already in score-desc order (as the real _run_queries would return)
        qs = [_make_slow_query(queryid=str(i), score=float(100 - i)) for i in range(15)]
        report = _make_query_report(queries=qs)
        summary = _build_query_summary(report)
        assert len(summary.top_queries) == 10
        # Should be the first 10 (highest scores)
        assert [q.queryid for q in summary.top_queries] == [str(i) for i in range(10)]

    def test_avg_score_correct(self):
        qs = [_make_slow_query(queryid="1", score=60.0), _make_slow_query(queryid="2", score=40.0)]
        report = _make_query_report(queries=qs)
        summary = _build_query_summary(report)
        assert summary.avg_score == 50.0

    def test_high_priority_count(self):
        qs = [
            _make_slow_query(queryid="1", score=70.0),  # high
            _make_slow_query(queryid="2", score=71.0),  # high
            _make_slow_query(queryid="3", score=69.9),  # not high
        ]
        report = _make_query_report(queries=qs)
        summary = _build_query_summary(report)
        assert summary.high_priority_count == 2

    def test_total_exec_time_ms_sum(self):
        qs = [
            _make_slow_query(queryid="1", score=50.0),
            _make_slow_query(queryid="2", score=60.0),
        ]
        report = _make_query_report(queries=qs)
        summary = _build_query_summary(report)
        # each _make_slow_query has total_exec_time_ms=1000.0
        assert summary.total_exec_time_ms == 2000.0

    def test_top_query_omits_raw_query_text(self):
        report = _make_query_report()
        summary = _build_query_summary(report)
        assert len(summary.top_queries) == 1
        tq = summary.top_queries[0]
        assert hasattr(tq, "query_fingerprint")
        assert not hasattr(tq, "query")

    def test_errors_propagated(self):
        report = QueryReport(
            connection_id="postgresql://localhost/db",
            total_queries=0,
            queries=[],
            errors=[ScanError(step="connect", message="bad dsn")],
        )
        summary = _build_query_summary(report)
        assert len(summary.errors) == 1
        assert summary.errors[0].step == "connect"


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------

DSN = "postgresql://localhost/db"
ENCODED_DSN = "postgresql%3A%2F%2Flocalhost%2Fdb"


class TestDashboardEndpoint:
    async def test_happy_path_both_populated(self):
        health = _make_health_report()
        queries = _make_query_report()

        with (
            patch("api.dashboard._run_health", new=AsyncMock(return_value=health)),
            patch("api.dashboard._run_queries", new=AsyncMock(return_value=queries)),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/dashboard/{ENCODED_DSN}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["health"] is not None
        assert body["health"]["score"] == 87
        assert body["queries"] is not None
        assert body["queries"]["total_queries"] == 1
        assert body["errors"] == []

    async def test_both_fail_returns_null_sections_with_errors(self):
        with (
            patch(
                "api.dashboard._run_health",
                new=AsyncMock(side_effect=RuntimeError("health failed")),
            ),
            patch(
                "api.dashboard._run_queries",
                new=AsyncMock(side_effect=RuntimeError("queries failed")),
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/dashboard/{ENCODED_DSN}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["health"] is None
        assert body["queries"] is None
        assert len(body["errors"]) == 2

    async def test_health_fails_queries_succeeds(self):
        queries = _make_query_report()

        with (
            patch(
                "api.dashboard._run_health",
                new=AsyncMock(side_effect=RuntimeError("health failed")),
            ),
            patch("api.dashboard._run_queries", new=AsyncMock(return_value=queries)),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/dashboard/{ENCODED_DSN}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["health"] is None
        assert body["queries"] is not None
        assert len(body["errors"]) == 1
        assert body["errors"][0]["step"] == "health"

    async def test_queries_fails_health_succeeds(self):
        health = _make_health_report()

        with (
            patch("api.dashboard._run_health", new=AsyncMock(return_value=health)),
            patch(
                "api.dashboard._run_queries",
                new=AsyncMock(side_effect=RuntimeError("queries failed")),
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/dashboard/{ENCODED_DSN}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["health"] is not None
        assert body["queries"] is None
        assert len(body["errors"]) == 1
        assert body["errors"][0]["step"] == "queries"

    async def test_connection_id_in_response(self):
        health = _make_health_report()
        queries = _make_query_report()

        with (
            patch("api.dashboard._run_health", new=AsyncMock(return_value=health)),
            patch("api.dashboard._run_queries", new=AsyncMock(return_value=queries)),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/dashboard/{ENCODED_DSN}")

        body = resp.json()
        assert body["connection_id"] == DSN
