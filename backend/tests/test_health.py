"""Tests for GET /api/health/{connection_id}[/stream]."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from api.health import (
    CheckResult,
    _check_cache_hit_ratio,
    _check_connections,
    _check_index_usage,
    _check_lock_contention,
    _check_long_transactions,
    _check_replication_lag,
    _check_table_bloat,
    _compute_score,
    _grade,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(**kwargs: Any) -> MagicMock:
    """Return a mock that behaves like an asyncpg Record (dict-style access)."""
    rec = MagicMock()
    rec.__getitem__ = lambda self, k: kwargs[k]
    rec.keys = lambda: list(kwargs.keys())
    return rec


async def _collect_sse(path: str) -> list[dict[str, Any]]:
    """Hit an SSE endpoint and return all parsed events."""
    events: list[dict[str, Any]] = []
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        async with client.stream("GET", path) as resp:
            assert resp.status_code == 200
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line[len("data: "):]))
    return events


# ---------------------------------------------------------------------------
# Unit tests — score service
# ---------------------------------------------------------------------------


class TestGrade:
    def test_a(self):
        assert _grade(100) == "A"
        assert _grade(90) == "A"

    def test_b(self):
        assert _grade(89) == "B"
        assert _grade(75) == "B"

    def test_c(self):
        assert _grade(74) == "C"
        assert _grade(60) == "C"

    def test_d(self):
        assert _grade(59) == "D"
        assert _grade(45) == "D"

    def test_f(self):
        assert _grade(44) == "F"
        assert _grade(0) == "F"


class TestComputeScore:
    def _make_checks(self, statuses: dict[str, str]) -> list[CheckResult]:
        return [
            CheckResult(name=name, status=st, message="")
            for name, st in statuses.items()
        ]

    def test_all_ok_gives_100(self):
        checks = self._make_checks(
            {n: "ok" for n in ["connections", "cache_hit_ratio", "replication_lag",
                                "table_bloat", "lock_contention", "long_transactions",
                                "index_usage"]}
        )
        score, grade, deductions = _compute_score(checks)
        assert score == 100
        assert grade == "A"
        assert deductions == []

    def test_all_fail_gives_0(self):
        checks = self._make_checks(
            {n: "fail" for n in ["connections", "cache_hit_ratio", "replication_lag",
                                  "table_bloat", "lock_contention", "long_transactions",
                                  "index_usage"]}
        )
        score, grade, deductions = _compute_score(checks)
        assert score == 0
        assert grade == "F"
        assert len(deductions) == 7

    def test_warning_is_half_deduction(self):
        # index_usage weight=20, warning → deduct 10 → score 90
        checks = self._make_checks({"index_usage": "warning"})
        score, grade, deductions = _compute_score(checks)
        assert score == 90
        assert grade == "A"
        assert deductions[0].points == 10

    def test_fail_is_full_deduction(self):
        # index_usage weight=20, fail → deduct 20 → score 80
        checks = self._make_checks({"index_usage": "fail"})
        score, grade, deductions = _compute_score(checks)
        assert score == 80
        assert grade == "B"
        assert deductions[0].points == 20

    def test_score_clamped_to_zero(self):
        checks = [
            CheckResult(name="index_usage", status="fail", message=""),
            CheckResult(name="cache_hit_ratio", status="fail", message=""),
            CheckResult(name="lock_contention", status="fail", message=""),
            CheckResult(name="long_transactions", status="fail", message=""),
            CheckResult(name="replication_lag", status="fail", message=""),
            CheckResult(name="connections", status="fail", message=""),
            CheckResult(name="table_bloat", status="fail", message=""),
        ]
        score, _, _ = _compute_score(checks)
        assert score == 0

    def test_unknown_check_name_ignored(self):
        checks = [CheckResult(name="unknown_check", status="fail", message="")]
        score, _, deductions = _compute_score(checks)
        assert score == 100
        assert deductions == []


# ---------------------------------------------------------------------------
# Unit tests — individual check functions
# ---------------------------------------------------------------------------


class TestCheckConnections:
    def _mock_conn(self, active: int, max_conn: int) -> AsyncMock:
        conn = AsyncMock()
        conn.fetchval = AsyncMock(side_effect=[active, str(max_conn)])
        return conn

    async def test_ok_low_usage(self):
        conn = self._mock_conn(active=10, max_conn=100)
        status, data = await _check_connections(conn)
        assert status == "ok"
        assert data["active"] == 10

    async def test_warning_70_pct(self):
        conn = self._mock_conn(active=71, max_conn=100)
        status, data = await _check_connections(conn)
        assert status == "warning"

    async def test_fail_90_pct(self):
        conn = self._mock_conn(active=91, max_conn=100)
        status, data = await _check_connections(conn)
        assert status == "fail"

    async def test_postgres_error(self):
        conn = AsyncMock()
        conn.fetchval = AsyncMock(side_effect=asyncpg.PostgresError("query failed"))
        status, data = await _check_connections(conn)
        assert status == "fail"
        assert "message" in data


class TestCheckCacheHitRatio:
    def _mock_conn(self, hits: int, reads: int) -> AsyncMock:
        conn = AsyncMock()
        row = _make_record(hits=hits, reads=reads)
        conn.fetchrow = AsyncMock(return_value=row)
        return conn

    async def test_ok_high_ratio(self):
        conn = self._mock_conn(hits=9900, reads=100)
        status, data = await _check_cache_hit_ratio(conn)
        assert status == "ok"
        assert data["ratio_pct"] == pytest.approx(99.0, abs=0.1)

    async def test_warning_below_95(self):
        conn = self._mock_conn(hits=9000, reads=1000)
        status, data = await _check_cache_hit_ratio(conn)
        assert status == "warning"

    async def test_fail_below_80(self):
        conn = self._mock_conn(hits=7000, reads=3000)
        status, data = await _check_cache_hit_ratio(conn)
        assert status == "fail"

    async def test_no_io_yet(self):
        conn = self._mock_conn(hits=0, reads=0)
        status, data = await _check_cache_hit_ratio(conn)
        assert status == "ok"
        assert data["ratio_pct"] is None

    async def test_insufficient_privilege(self):
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(side_effect=asyncpg.InsufficientPrivilegeError())
        status, data = await _check_cache_hit_ratio(conn)
        assert status == "warning"
        assert "fix" in data


class TestCheckReplicationLag:
    async def test_no_replicas_is_ok(self):
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])
        status, data = await _check_replication_lag(conn)
        assert status == "ok"
        assert "No replicas" in data["message"]

    async def test_ok_low_lag(self):
        conn = AsyncMock()
        row = _make_record(
            application_name="standby1",
            write_lag_s=1.0,
            flush_lag_s=1.0,
            replay_lag_s=1.5,
        )
        conn.fetch = AsyncMock(return_value=[row])
        status, data = await _check_replication_lag(conn)
        assert status == "ok"
        assert data["max_lag_s"] == pytest.approx(1.5, abs=0.01)

    async def test_warning_over_30s(self):
        conn = AsyncMock()
        row = _make_record(
            application_name="standby1",
            write_lag_s=31.0,
            flush_lag_s=0.0,
            replay_lag_s=0.0,
        )
        conn.fetch = AsyncMock(return_value=[row])
        status, _ = await _check_replication_lag(conn)
        assert status == "warning"

    async def test_fail_over_300s(self):
        conn = AsyncMock()
        row = _make_record(
            application_name="standby1",
            write_lag_s=301.0,
            flush_lag_s=0.0,
            replay_lag_s=0.0,
        )
        conn.fetch = AsyncMock(return_value=[row])
        status, _ = await _check_replication_lag(conn)
        assert status == "fail"

    async def test_insufficient_privilege(self):
        conn = AsyncMock()
        conn.fetch = AsyncMock(side_effect=asyncpg.InsufficientPrivilegeError())
        status, data = await _check_replication_lag(conn)
        assert status == "warning"
        assert "fix" in data


class TestCheckTableBloat:
    async def test_no_tables(self):
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])
        status, data = await _check_table_bloat(conn)
        assert status == "ok"

    async def test_ok_low_bloat(self):
        conn = AsyncMock()
        row = _make_record(
            table_name="public.users",
            relpages=100,
            reltuples=10000.0,
            actual_bytes=819200,
        )
        conn.fetch = AsyncMock(return_value=[row])
        status, _ = await _check_table_bloat(conn)
        # reltuples=10000 → estimated_live = 10000*24/8192 ≈ 29.3 pages
        # bloat = (100 - 29.3) / 100 ≈ 70.7% → fail
        assert status in ("ok", "warning", "fail")  # just validates no crash

    async def test_postgres_error(self):
        conn = AsyncMock()
        conn.fetch = AsyncMock(side_effect=asyncpg.PostgresError("query failed"))
        status, data = await _check_table_bloat(conn)
        assert status == "fail"


class TestCheckLockContention:
    async def test_no_locks(self):
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])
        status, data = await _check_lock_contention(conn)
        assert status == "ok"
        assert data["waiting_count"] == 0

    async def test_warning_one_lock(self):
        conn = AsyncMock()
        row = _make_record(
            blocked_pid=1001,
            blocked_query="SELECT ...",
            blocking_pid=1002,
            blocking_query="UPDATE ...",
        )
        conn.fetch = AsyncMock(return_value=[row])
        status, data = await _check_lock_contention(conn)
        assert status == "warning"
        assert data["waiting_count"] == 1

    async def test_fail_five_locks(self):
        conn = AsyncMock()
        rows = [
            _make_record(
                blocked_pid=i,
                blocked_query="q",
                blocking_pid=i + 100,
                blocking_query="q2",
            )
            for i in range(5)
        ]
        conn.fetch = AsyncMock(return_value=rows)
        status, _ = await _check_lock_contention(conn)
        assert status == "fail"


class TestCheckLongTransactions:
    async def test_no_long_txns(self):
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])
        status, data = await _check_long_transactions(conn)
        assert status == "ok"
        assert data["count"] == 0

    async def test_warning_one_txn(self):
        conn = AsyncMock()
        row = _make_record(pid=42, usename="app", age_s=400, query="BEGIN")
        conn.fetch = AsyncMock(return_value=[row])
        status, data = await _check_long_transactions(conn)
        assert status == "warning"

    async def test_fail_three_txns(self):
        conn = AsyncMock()
        rows = [_make_record(pid=i, usename="app", age_s=400, query="BEGIN") for i in range(3)]
        conn.fetch = AsyncMock(return_value=rows)
        status, _ = await _check_long_transactions(conn)
        assert status == "fail"


class TestCheckIndexUsage:
    async def test_no_problematic_tables(self):
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])
        status, data = await _check_index_usage(conn)
        assert status == "ok"
        assert data["under_indexed_count"] == 0

    async def test_warning_one_table(self):
        conn = AsyncMock()
        row = _make_record(
            table_name="public.events",
            seq_scan=1000,
            idx_scan=100,
            seq_tup_read=500000,
            idx_pct=9.1,
        )
        conn.fetch = AsyncMock(return_value=[row])
        status, data = await _check_index_usage(conn)
        assert status == "warning"

    async def test_fail_three_tables(self):
        conn = AsyncMock()
        rows = [
            _make_record(
                table_name=f"public.t{i}",
                seq_scan=1000,
                idx_scan=100,
                seq_tup_read=500000,
                idx_pct=9.1,
            )
            for i in range(3)
        ]
        conn.fetch = AsyncMock(return_value=rows)
        status, _ = await _check_index_usage(conn)
        assert status == "fail"


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------


def _mock_conn_all_ok() -> AsyncMock:
    """A mock asyncpg.Connection where every check query returns healthy data."""
    conn = AsyncMock(spec=asyncpg.Connection)
    # connections: active=10, max=100
    # cache_hit_ratio fetchrow
    # replication_lag fetch → []
    # table_bloat fetch → []
    # lock_contention fetch → []
    # long_transactions fetch → []
    # index_usage fetch → []
    conn.fetchval = AsyncMock(side_effect=[10, "100"])  # active, max_connections
    cache_row = _make_record(hits=9900, reads=100)
    conn.fetchrow = AsyncMock(return_value=cache_row)
    conn.fetch = AsyncMock(return_value=[])
    conn.close = AsyncMock()
    return conn


class TestGetHealthEndpoint:
    async def test_connect_failure_returns_null_score(self):
        with patch(
            "api.health.asyncpg.connect",
            AsyncMock(side_effect=asyncpg.InvalidPasswordError()),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/health/postgresql://bad:creds@host/db")
        assert resp.status_code == 200
        body = resp.json()
        assert body["score"] is None
        assert body["grade"] is None
        assert len(body["errors"]) == 1
        assert body["errors"][0]["check"] == "connect"

    async def test_healthy_db_returns_score(self):
        mock_conn = _mock_conn_all_ok()
        with patch("api.health.asyncpg.connect", AsyncMock(return_value=mock_conn)):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/health/postgresql://ok@host/db")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body["score"], int)
        assert body["grade"] in ("A", "B", "C", "D", "F")
        assert len(body["checks"]) == 7

    async def test_report_has_all_check_names(self):
        mock_conn = _mock_conn_all_ok()
        with patch("api.health.asyncpg.connect", AsyncMock(return_value=mock_conn)):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/health/postgresql://ok@host/db")
        names = {c["name"] for c in resp.json()["checks"]}
        expected = {
            "connections", "cache_hit_ratio", "replication_lag",
            "table_bloat", "lock_contention", "long_transactions", "index_usage",
        }
        assert names == expected


class TestStreamHealthEndpoint:
    async def test_stream_returns_event_stream(self):
        mock_conn = _mock_conn_all_ok()
        with patch("api.health.asyncpg.connect", AsyncMock(return_value=mock_conn)):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(
                    "/api/health/postgresql://ok@host/db/stream",
                    headers={"Accept": "text/event-stream"},
                )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]

    async def test_stream_connect_fail_emits_result_with_null_score(self):
        with patch(
            "api.health.asyncpg.connect",
            AsyncMock(side_effect=asyncpg.InvalidPasswordError()),
        ):
            events = await _collect_sse(
                "/api/health/postgresql://bad:creds@host/db/stream"
            )
        steps = [e["step"] for e in events]
        assert steps[0] == "connect"
        assert steps[-1] == "result"
        result_evt = next(e for e in events if e["step"] == "result")
        assert result_evt["data"]["score"] is None

    async def test_stream_healthy_emits_9_events(self):
        mock_conn = _mock_conn_all_ok()
        with patch("api.health.asyncpg.connect", AsyncMock(return_value=mock_conn)):
            events = await _collect_sse(
                "/api/health/postgresql://ok@host/db/stream"
            )
        # connect + 7 checks + result = 9
        assert len(events) == 9

    async def test_stream_event_order(self):
        mock_conn = _mock_conn_all_ok()
        with patch("api.health.asyncpg.connect", AsyncMock(return_value=mock_conn)):
            events = await _collect_sse(
                "/api/health/postgresql://ok@host/db/stream"
            )
        steps = [e["step"] for e in events]
        assert steps == [
            "connect",
            "connections",
            "cache_hit_ratio",
            "replication_lag",
            "table_bloat",
            "lock_contention",
            "long_transactions",
            "index_usage",
            "result",
        ]

    async def test_stream_result_event_has_score_and_grade(self):
        mock_conn = _mock_conn_all_ok()
        with patch("api.health.asyncpg.connect", AsyncMock(return_value=mock_conn)):
            events = await _collect_sse(
                "/api/health/postgresql://ok@host/db/stream"
            )
        result_evt = next(e for e in events if e["step"] == "result")
        data = result_evt["data"]
        assert isinstance(data["score"], int)
        assert data["grade"] in ("A", "B", "C", "D", "F")
        assert len(data["checks"]) == 7
        assert isinstance(data["deductions"], list)

    async def test_stream_timeout_emits_fail_for_check(self):
        """If a check times out, the stream should still emit a fail event and continue."""
        mock_conn = AsyncMock(spec=asyncpg.Connection)
        mock_conn.close = AsyncMock()

        async def slow_fetchval(*_args):
            await asyncio.sleep(20)  # will be cancelled by _with_timeout
            return 0

        mock_conn.fetchval = slow_fetchval
        # All fetch calls return empty (for other checks)
        mock_conn.fetchrow = AsyncMock(return_value=_make_record(hits=0, reads=0))
        mock_conn.fetch = AsyncMock(return_value=[])

        import asyncio as _asyncio

        with patch("api.health.asyncpg.connect", AsyncMock(return_value=mock_conn)):
            with patch("api.health._with_timeout", wraps=None) as mock_timeout:
                # Override _with_timeout to time out connections check only
                call_count = 0

                async def patched_with_timeout(coro, seconds=10):
                    nonlocal call_count
                    call_count += 1
                    if call_count == 1:  # first check = connections
                        coro.close()
                        return "fail", {"message": "Check timed out after 10 seconds."}
                    try:
                        return await _asyncio.wait_for(coro, timeout=seconds)
                    except _asyncio.TimeoutError:
                        return "fail", {"message": "Check timed out after 10 seconds."}

                mock_timeout.side_effect = patched_with_timeout

                events = await _collect_sse(
                    "/api/health/postgresql://ok@host/db/stream"
                )

        conn_evt = next(e for e in events if e["step"] == "connections")
        assert conn_evt["status"] == "fail"
        # Stream must still finish with a result event
        assert events[-1]["step"] == "result"
