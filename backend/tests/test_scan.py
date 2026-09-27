"""Tests for GET /api/scan/{connection_id} — progressive SSE scan endpoint."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from api.scan import (
    _CONNECTIONS,
    _build_complete_event,
    _check_dead_tuples,
    _fetch_db_metadata,
    _fetch_slow_queries,
    _sse,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(**kwargs: Any) -> MagicMock:
    """Return a mock that behaves like an asyncpg Record (dict-style access)."""
    rec = MagicMock()
    rec.__getitem__ = lambda self, k: kwargs[k]
    rec.keys = lambda: list(kwargs.keys())
    rec.items = lambda: kwargs.items()
    rec.__iter__ = lambda self: iter(kwargs.keys())
    return rec


def _make_fetchrow(**kwargs: Any) -> MagicMock:
    return _make_record(**kwargs)


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


def _register_dsn(dsn: str) -> str:
    """Insert a DSN directly into the in-memory store and return the UUID."""
    import uuid
    cid = str(uuid.uuid4())
    _CONNECTIONS[cid] = dsn
    return cid


def _mock_full_conn() -> AsyncMock:
    """
    Return a mock asyncpg.Connection where every query returns healthy data.

    Call pattern inside _run_scan:
      fetchval x4  — version, size, table_count, total_rows  (metadata)
      fetchval x2  — active connections (10), max_connections ("100")
      fetchrow x1  — cache hit ratio row
      fetch    x5  — replication_lag, table_bloat, lock_contention,
                     long_transactions, index_usage
      fetch    x1  — dead_tuples
      fetch    x1  — slow_queries
    """
    conn = AsyncMock(spec=asyncpg.Connection)
    conn.fetchval = AsyncMock(
        side_effect=[
            # metadata (4)
            "PostgreSQL 15.0",
            "42 MB",
            5,
            12345,
            # connections check (2)
            10,
            "100",
        ]
    )
    cache_row = _make_record(hits=9900, reads=100)
    conn.fetchrow = AsyncMock(return_value=cache_row)
    conn.fetch = AsyncMock(return_value=[])  # all list-returning checks → empty
    conn.close = AsyncMock()
    return conn


# ---------------------------------------------------------------------------
# Unit tests — _sse helper
# ---------------------------------------------------------------------------


class TestSseHelper:
    def test_format_is_data_line(self):
        line = _sse("connected", {"version": "pg15"})
        assert line.startswith("data: ")
        assert line.endswith("\n\n")

    def test_payload_contains_stage_and_data(self):
        line = _sse("complete", {"health_score": 95})
        payload = json.loads(line[len("data: "):])
        assert payload["stage"] == "complete"
        assert payload["data"]["health_score"] == 95

    def test_data_is_json_serialisable(self):
        line = _sse("health_check", {"check": "connections", "status": "ok"})
        json.loads(line[len("data: "):])  # must not raise


# ---------------------------------------------------------------------------
# Unit tests — _fetch_db_metadata
# ---------------------------------------------------------------------------


class TestFetchDbMetadata:
    async def test_returns_expected_keys(self):
        conn = AsyncMock()
        conn.fetchval = AsyncMock(
            side_effect=["PostgreSQL 15.0", "42 MB", 7, 99999]
        )
        result = await _fetch_db_metadata(conn)
        assert set(result.keys()) == {"version", "size", "table_count", "total_rows"}

    async def test_casts_table_count_and_total_rows_to_int(self):
        conn = AsyncMock()
        conn.fetchval = AsyncMock(
            side_effect=["PostgreSQL 15.0", "1 GB", 3, 50000]
        )
        result = await _fetch_db_metadata(conn)
        assert isinstance(result["table_count"], int)
        assert isinstance(result["total_rows"], int)

    async def test_runs_four_queries(self):
        conn = AsyncMock()
        conn.fetchval = AsyncMock(
            side_effect=["ver", "1 kB", 1, 0]
        )
        await _fetch_db_metadata(conn)
        assert conn.fetchval.call_count == 4


# ---------------------------------------------------------------------------
# Unit tests — _check_dead_tuples
# ---------------------------------------------------------------------------


class TestCheckDeadTuples:
    async def test_ok_when_no_bloated_tables(self):
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])
        status, data = await _check_dead_tuples(conn)
        assert status == "ok"
        assert data["bloated_count"] == 0

    async def test_warning_for_one_bloated_table(self):
        conn = AsyncMock()
        row = _make_record(
            table_name="public.events",
            dead_ratio_pct=15.0,
            n_dead_tup=5000,
            last_autovacuum=None,
            last_autoanalyze=None,
        )
        conn.fetch = AsyncMock(return_value=[row])
        status, data = await _check_dead_tuples(conn)
        assert status == "warning"
        assert data["bloated_count"] == 1
        assert data["tables"][0]["table"] == "public.events"

    async def test_fail_for_three_or_more_tables(self):
        conn = AsyncMock()
        rows = [
            _make_record(
                table_name=f"public.t{i}",
                dead_ratio_pct=20.0,
                n_dead_tup=3000,
                last_autovacuum=None,
                last_autoanalyze=None,
            )
            for i in range(3)
        ]
        conn.fetch = AsyncMock(return_value=rows)
        status, data = await _check_dead_tuples(conn)
        assert status == "fail"
        assert data["bloated_count"] == 3

    async def test_insufficient_privilege_returns_warning(self):
        conn = AsyncMock()
        conn.fetch = AsyncMock(
            side_effect=asyncpg.InsufficientPrivilegeError()
        )
        status, data = await _check_dead_tuples(conn)
        assert status == "warning"
        assert "fix" in data

    async def test_postgres_error_returns_fail(self):
        conn = AsyncMock()
        conn.fetch = AsyncMock(
            side_effect=asyncpg.PostgresError("query failed")
        )
        status, data = await _check_dead_tuples(conn)
        assert status == "fail"
        assert "message" in data


# ---------------------------------------------------------------------------
# Unit tests — _fetch_slow_queries
# ---------------------------------------------------------------------------


class TestFetchSlowQueries:
    def _make_row(self, **overrides: Any) -> dict[str, Any]:
        base = {
            "queryid": "99",
            "query": "SELECT 1",
            "calls": 10,
            "mean_exec_time_ms": 200.0,
            "total_exec_time_ms": 2000.0,
            "stddev_exec_time_ms": 20.0,
            "rows_per_call": 1.0,
            "shared_blks_hit": 900,
            "shared_blks_read": 100,
            "cache_hit_ratio": 90.0,
        }
        base.update(overrides)
        return base

    async def test_returns_three_required_keys(self):
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])
        result = await _fetch_slow_queries(conn)
        assert "queries" in result
        assert "total_wasted_minutes" in result
        assert "human_description" in result

    async def test_empty_result_gives_no_stats_message(self):
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])
        result = await _fetch_slow_queries(conn)
        assert result["queries"] == []
        assert "pg_stat_statements" in result["human_description"]

    async def test_slow_queries_include_fingerprint(self):
        row = self._make_row()
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[row])
        result = await _fetch_slow_queries(conn)
        assert len(result["queries"]) == 1
        assert "query_fingerprint" in result["queries"][0]

    async def test_total_wasted_minutes_calculated(self):
        # 60_000 ms = 1 minute
        row = self._make_row(total_exec_time_ms=60_000.0)
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[row])
        result = await _fetch_slow_queries(conn)
        assert result["total_wasted_minutes"] == pytest.approx(1.0, abs=0.01)

    async def test_timeout_returns_fallback(self):
        import asyncio
        conn = AsyncMock()
        conn.fetch = AsyncMock(side_effect=asyncio.TimeoutError())
        result = await _fetch_slow_queries(conn)
        assert result["queries"] == []
        assert result["total_wasted_minutes"] == 0.0

    async def test_postgres_error_returns_fallback(self):
        conn = AsyncMock()
        conn.fetch = AsyncMock(
            side_effect=asyncpg.PostgresError("no such table")
        )
        result = await _fetch_slow_queries(conn)
        assert result["queries"] == []

    async def test_human_description_high_wasted(self):
        # 4_000_000 ms = 66.7 minutes → "Immediate optimisation" message
        row = self._make_row(total_exec_time_ms=4_000_000.0)
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[row])
        result = await _fetch_slow_queries(conn)
        assert "Immediate" in result["human_description"]

    async def test_human_description_moderate_wasted(self):
        # 90_000 ms = 1.5 minutes → "account for" message
        row = self._make_row(total_exec_time_ms=90_000.0)
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[row])
        result = await _fetch_slow_queries(conn)
        assert "minutes" in result["human_description"]


# ---------------------------------------------------------------------------
# Unit tests — _build_complete_event
# ---------------------------------------------------------------------------


class TestBuildCompleteEvent:
    def test_all_ok_gives_high_score(self):
        check_results = [
            ("connections", "ok", {"message": "fine"}),
            ("cache_hit_ratio", "ok", {"message": "fine"}),
            ("index_usage", "ok", {"message": "fine"}),
        ]
        result = _build_complete_event(check_results)
        assert result["health_score"] == 100
        assert result["critical_count"] == 0
        assert result["warning_count"] == 0
        assert result["healthy_count"] == 3
        assert result["quick_wins"] == []

    def test_fail_counts_are_correct(self):
        check_results = [
            ("connections", "fail", {"message": "bad"}),
            ("cache_hit_ratio", "warning", {"message": "meh"}),
            ("index_usage", "ok", {"message": "ok"}),
        ]
        result = _build_complete_event(check_results)
        assert result["critical_count"] == 1
        assert result["warning_count"] == 1
        assert result["healthy_count"] == 1

    def test_quick_wins_prefers_warnings_first(self):
        check_results = [
            ("connections", "fail", {"message": "bad"}),
            ("cache_hit_ratio", "warning", {"message": "meh"}),
        ]
        result = _build_complete_event(check_results)
        # warning should come before fail
        assert result["quick_wins"][0]["check"] == "cache_hit_ratio"
        assert result["quick_wins"][1]["check"] == "connections"

    def test_quick_wins_capped_at_three(self):
        check_results = [
            (name, "fail", {"message": "bad"})
            for name in ["connections", "cache_hit_ratio", "table_bloat",
                         "lock_contention", "index_usage"]
        ]
        result = _build_complete_event(check_results)
        assert len(result["quick_wins"]) == 3

    def test_quick_wins_contain_check_and_fix(self):
        check_results = [("index_usage", "warning", {"message": "under-indexed"})]
        result = _build_complete_event(check_results)
        assert len(result["quick_wins"]) == 1
        win = result["quick_wins"][0]
        assert "check" in win
        assert "fix" in win
        assert win["check"] == "index_usage"

    def test_unknown_check_gets_generic_fix(self):
        check_results = [("mystery_check", "fail", {"message": "unknown"})]
        result = _build_complete_event(check_results)
        assert result["quick_wins"][0]["fix"] == "Investigate the mystery_check check."

    def test_score_clamped_at_zero(self):
        check_results = [
            (name, "fail", {"message": "bad"})
            for name in ["connections", "cache_hit_ratio", "replication_lag",
                         "table_bloat", "lock_contention", "long_transactions",
                         "index_usage", "dead_tuples"]
        ]
        result = _build_complete_event(check_results)
        assert result["health_score"] == 0

    def test_result_has_all_required_keys(self):
        result = _build_complete_event([])
        required = {"health_score", "critical_count", "warning_count",
                    "healthy_count", "quick_wins"}
        assert required.issubset(result.keys())


# ---------------------------------------------------------------------------
# Registration endpoint tests
# ---------------------------------------------------------------------------


class TestRegisterEndpoint:
    async def test_post_register_returns_connection_id(self):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/scan/register",
                json={"connection_string": "postgresql://user:pass@host/db"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert "connection_id" in body
        assert len(body["connection_id"]) == 36  # UUID format

    async def test_registered_dsn_is_stored(self):
        dsn = "postgresql://test:test@localhost/testdb"
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/scan/register",
                json={"connection_string": dsn},
            )
        cid = resp.json()["connection_id"]
        assert _CONNECTIONS.get(cid) == dsn

    async def test_each_registration_yields_unique_id(self):
        dsn = "postgresql://a:b@host/db"
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            ids = []
            for _ in range(3):
                resp = await client.post(
                    "/api/scan/register", json={"connection_string": dsn}
                )
                ids.append(resp.json()["connection_id"])
        assert len(set(ids)) == 3


# ---------------------------------------------------------------------------
# GET /api/scan/{connection_id} — HTTP-level tests
# ---------------------------------------------------------------------------


class TestScanEndpoint:
    async def test_unknown_connection_id_returns_404(self):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/scan/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    async def test_returns_event_stream_content_type(self):
        cid = _register_dsn("postgresql://x:y@host/db")
        mock_conn = _mock_full_conn()
        with patch("api.scan.asyncpg.connect", AsyncMock(return_value=mock_conn)):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                async with client.stream("GET", f"/api/scan/{cid}") as resp:
                    assert resp.status_code == 200
                    assert "text/event-stream" in resp.headers["content-type"]

    async def test_no_cache_headers_present(self):
        cid = _register_dsn("postgresql://x:y@host/db")
        mock_conn = _mock_full_conn()
        with patch("api.scan.asyncpg.connect", AsyncMock(return_value=mock_conn)):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                async with client.stream("GET", f"/api/scan/{cid}") as resp:
                    assert resp.headers.get("cache-control") == "no-cache"
                    assert resp.headers.get("x-accel-buffering") == "no"

    async def test_connection_failure_emits_connected_error_event(self):
        cid = _register_dsn("postgresql://bad:creds@host/db")
        with patch(
            "api.scan.asyncpg.connect",
            AsyncMock(side_effect=asyncpg.InvalidPasswordError()),
        ):
            events = await _collect_sse(f"/api/scan/{cid}")
        assert len(events) == 1
        assert events[0]["stage"] == "connected"
        assert "error" in events[0]["data"]


# ---------------------------------------------------------------------------
# Full scan stream — event sequence and content
# ---------------------------------------------------------------------------


class TestScanStream:
    async def test_emits_eleven_events(self):
        cid = _register_dsn("postgresql://ok@host/db")
        mock_conn = _mock_full_conn()
        with patch("api.scan.asyncpg.connect", AsyncMock(return_value=mock_conn)):
            events = await _collect_sse(f"/api/scan/{cid}")
        # 1 connected + 8 health_check + 1 slow_queries + 1 complete = 11
        assert len(events) == 11

    async def test_first_event_is_connected(self):
        cid = _register_dsn("postgresql://ok@host/db")
        mock_conn = _mock_full_conn()
        with patch("api.scan.asyncpg.connect", AsyncMock(return_value=mock_conn)):
            events = await _collect_sse(f"/api/scan/{cid}")
        assert events[0]["stage"] == "connected"

    async def test_connected_event_has_metadata_keys(self):
        cid = _register_dsn("postgresql://ok@host/db")
        mock_conn = _mock_full_conn()
        with patch("api.scan.asyncpg.connect", AsyncMock(return_value=mock_conn)):
            events = await _collect_sse(f"/api/scan/{cid}")
        data = events[0]["data"]
        assert "version" in data
        assert "size" in data
        assert "table_count" in data
        assert "total_rows" in data

    async def test_eight_health_check_events(self):
        cid = _register_dsn("postgresql://ok@host/db")
        mock_conn = _mock_full_conn()
        with patch("api.scan.asyncpg.connect", AsyncMock(return_value=mock_conn)):
            events = await _collect_sse(f"/api/scan/{cid}")
        health_events = [e for e in events if e["stage"] == "health_check"]
        assert len(health_events) == 8

    async def test_health_check_events_have_required_fields(self):
        cid = _register_dsn("postgresql://ok@host/db")
        mock_conn = _mock_full_conn()
        with patch("api.scan.asyncpg.connect", AsyncMock(return_value=mock_conn)):
            events = await _collect_sse(f"/api/scan/{cid}")
        for evt in events:
            if evt["stage"] == "health_check":
                assert "check" in evt["data"]
                assert "status" in evt["data"]
                assert evt["data"]["status"] in ("ok", "warning", "fail")

    async def test_all_eight_check_names_present(self):
        cid = _register_dsn("postgresql://ok@host/db")
        mock_conn = _mock_full_conn()
        with patch("api.scan.asyncpg.connect", AsyncMock(return_value=mock_conn)):
            events = await _collect_sse(f"/api/scan/{cid}")
        check_names = {
            e["data"]["check"]
            for e in events
            if e["stage"] == "health_check"
        }
        expected = {
            "connections", "cache_hit_ratio", "replication_lag", "table_bloat",
            "lock_contention", "long_transactions", "index_usage", "dead_tuples",
        }
        assert check_names == expected

    async def test_slow_queries_event_before_complete(self):
        cid = _register_dsn("postgresql://ok@host/db")
        mock_conn = _mock_full_conn()
        with patch("api.scan.asyncpg.connect", AsyncMock(return_value=mock_conn)):
            events = await _collect_sse(f"/api/scan/{cid}")
        stages = [e["stage"] for e in events]
        sq_idx = stages.index("slow_queries")
        complete_idx = stages.index("complete")
        assert sq_idx < complete_idx

    async def test_slow_queries_event_has_required_keys(self):
        cid = _register_dsn("postgresql://ok@host/db")
        mock_conn = _mock_full_conn()
        with patch("api.scan.asyncpg.connect", AsyncMock(return_value=mock_conn)):
            events = await _collect_sse(f"/api/scan/{cid}")
        sq_evt = next(e for e in events if e["stage"] == "slow_queries")
        assert "queries" in sq_evt["data"]
        assert "total_wasted_minutes" in sq_evt["data"]
        assert "human_description" in sq_evt["data"]

    async def test_last_event_is_complete(self):
        cid = _register_dsn("postgresql://ok@host/db")
        mock_conn = _mock_full_conn()
        with patch("api.scan.asyncpg.connect", AsyncMock(return_value=mock_conn)):
            events = await _collect_sse(f"/api/scan/{cid}")
        assert events[-1]["stage"] == "complete"

    async def test_complete_event_has_required_keys(self):
        cid = _register_dsn("postgresql://ok@host/db")
        mock_conn = _mock_full_conn()
        with patch("api.scan.asyncpg.connect", AsyncMock(return_value=mock_conn)):
            events = await _collect_sse(f"/api/scan/{cid}")
        complete_data = events[-1]["data"]
        required = {"health_score", "critical_count", "warning_count",
                    "healthy_count", "quick_wins"}
        assert required.issubset(complete_data.keys())

    async def test_complete_event_score_is_integer_in_range(self):
        cid = _register_dsn("postgresql://ok@host/db")
        mock_conn = _mock_full_conn()
        with patch("api.scan.asyncpg.connect", AsyncMock(return_value=mock_conn)):
            events = await _collect_sse(f"/api/scan/{cid}")
        score = events[-1]["data"]["health_score"]
        assert isinstance(score, int)
        assert 0 <= score <= 100

    async def test_complete_event_quick_wins_is_list(self):
        cid = _register_dsn("postgresql://ok@host/db")
        mock_conn = _mock_full_conn()
        with patch("api.scan.asyncpg.connect", AsyncMock(return_value=mock_conn)):
            events = await _collect_sse(f"/api/scan/{cid}")
        assert isinstance(events[-1]["data"]["quick_wins"], list)

    async def test_connection_is_closed_after_scan(self):
        cid = _register_dsn("postgresql://ok@host/db")
        mock_conn = _mock_full_conn()
        with patch("api.scan.asyncpg.connect", AsyncMock(return_value=mock_conn)):
            await _collect_sse(f"/api/scan/{cid}")
        mock_conn.close.assert_awaited_once()

    async def test_all_events_have_stage_and_data_keys(self):
        cid = _register_dsn("postgresql://ok@host/db")
        mock_conn = _mock_full_conn()
        with patch("api.scan.asyncpg.connect", AsyncMock(return_value=mock_conn)):
            events = await _collect_sse(f"/api/scan/{cid}")
        for evt in events:
            assert "stage" in evt
            assert "data" in evt

    async def test_metadata_error_still_emits_connected_event(self):
        """If metadata fetch fails, connected is still emitted with error key."""
        cid = _register_dsn("postgresql://ok@host/db")
        mock_conn = AsyncMock(spec=asyncpg.Connection)
        mock_conn.fetchval = AsyncMock(
            side_effect=asyncpg.PostgresError("permission denied")
        )
        mock_conn.fetchrow = AsyncMock(
            return_value=_make_record(hits=9900, reads=100)
        )
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_conn.close = AsyncMock()

        with patch("api.scan.asyncpg.connect", AsyncMock(return_value=mock_conn)):
            events = await _collect_sse(f"/api/scan/{cid}")

        assert events[0]["stage"] == "connected"
        assert "error" in events[0]["data"]
