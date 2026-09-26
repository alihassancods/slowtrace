"""Tests for GET /api/queries/{connection_id}[/stream]."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from api.queries import (
    _fingerprint,
    _score_query,
    _step_check_extension,
    _step_check_permissions,
    _step_fetch_queries,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(**kwargs: Any) -> MagicMock:
    """Return a mock that behaves like an asyncpg Record (dict-style access)."""
    rec = MagicMock()
    rec.__getitem__ = lambda self, k: kwargs[k]
    rec.keys = lambda: list(kwargs.keys())
    # Also allow dict(rec) by supporting items()
    rec.__iter__ = lambda self: iter(kwargs)
    # Support dict(rec) via asyncpg record protocol
    return rec


def _make_asyncpg_row(**kwargs: Any) -> MagicMock:
    """asyncpg Record mock that also supports dict() conversion."""
    rec = MagicMock()
    rec.__getitem__ = lambda self, k: kwargs[k]
    rec.keys = lambda: list(kwargs.keys())
    rec.items = lambda: kwargs.items()
    rec.__iter__ = lambda self: iter(kwargs.keys())
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


def _make_fake_row(
    *,
    queryid: str = "123",
    query: str = "SELECT 1",
    calls: int = 10,
    mean_exec_time_ms: float = 100.0,
    total_exec_time_ms: float = 1000.0,
    stddev_exec_time_ms: float = 10.0,
    rows_per_call: float = 1.0,
    shared_blks_hit: int = 900,
    shared_blks_read: int = 100,
    cache_hit_ratio: float = 90.0,
) -> dict[str, Any]:
    return {
        "queryid": queryid,
        "query": query,
        "calls": calls,
        "mean_exec_time_ms": mean_exec_time_ms,
        "total_exec_time_ms": total_exec_time_ms,
        "stddev_exec_time_ms": stddev_exec_time_ms,
        "rows_per_call": rows_per_call,
        "shared_blks_hit": shared_blks_hit,
        "shared_blks_read": shared_blks_read,
        "cache_hit_ratio": cache_hit_ratio,
    }


# ---------------------------------------------------------------------------
# Unit tests — _fingerprint
# ---------------------------------------------------------------------------


class TestFingerprint:
    def test_lowercases(self):
        assert _fingerprint("SELECT * FROM Users") == "select * from users"

    def test_collapses_whitespace(self):
        assert _fingerprint("SELECT  *\n  FROM\t orders") == "select * from orders"

    def test_strips_string_literals(self):
        fp = _fingerprint("SELECT * FROM t WHERE name = 'alice'")
        assert "alice" not in fp
        assert "?" in fp

    def test_strips_numeric_literals(self):
        fp = _fingerprint("SELECT * FROM t WHERE id = 42 OR price = 3.14")
        assert "42" not in fp
        assert "3.14" not in fp
        assert "?" in fp

    def test_strips_block_comments(self):
        fp = _fingerprint("SELECT /* a comment */ * FROM t")
        assert "comment" not in fp

    def test_combined(self):
        fp = _fingerprint("  SELECT  *  FROM orders WHERE id = 99 AND name = 'bob'  ")
        assert fp == "select * from orders where id = ? and name = ?"


# ---------------------------------------------------------------------------
# Unit tests — _score_query
# ---------------------------------------------------------------------------


class TestScoreQuery:
    def _row(self, **kwargs: Any) -> dict[str, Any]:
        base = _make_fake_row()
        base.update(kwargs)
        return base

    def test_zero_row_gives_low_score(self):
        row = self._row(
            mean_exec_time_ms=0.0,
            total_exec_time_ms=0.0,
            stddev_exec_time_ms=0.0,
            cache_hit_ratio=100.0,
        )
        assert _score_query(row, 0.0) == 0.0

    def test_high_mean_time_increases_score(self):
        row = self._row(mean_exec_time_ms=1000.0, total_exec_time_ms=1000.0)
        score = _score_query(row, 1000.0)
        # mean signal alone should be 40 (capped at 1s)
        assert score >= 40.0

    def test_max_total_share_gives_30_points(self):
        row = self._row(
            mean_exec_time_ms=0.0,
            total_exec_time_ms=100.0,
            stddev_exec_time_ms=0.0,
            cache_hit_ratio=100.0,
        )
        score = _score_query(row, 100.0)
        assert score == 30.0

    def test_zero_cache_hit_gives_20_points(self):
        row = self._row(
            mean_exec_time_ms=0.0,
            total_exec_time_ms=0.0,
            stddev_exec_time_ms=0.0,
            cache_hit_ratio=0.0,
        )
        assert _score_query(row, 0.0) == 20.0

    def test_score_capped_at_100(self):
        row = self._row(
            mean_exec_time_ms=2000.0,
            total_exec_time_ms=1000.0,
            stddev_exec_time_ms=2000.0,
            cache_hit_ratio=0.0,
        )
        assert _score_query(row, 1000.0) <= 100.0

    def test_score_rounded_to_1_decimal(self):
        row = self._row()
        score = _score_query(row, row["total_exec_time_ms"])
        assert score == round(score, 1)


# ---------------------------------------------------------------------------
# Step function tests — check_extension
# ---------------------------------------------------------------------------


class TestStepCheckExtension:
    def _mock_conn(self, count: int) -> AsyncMock:
        conn = AsyncMock()
        conn.fetchval = AsyncMock(return_value=count)
        return conn

    async def test_extension_installed(self):
        conn = self._mock_conn(1)
        status, data = await _step_check_extension(conn)
        assert status == "ok"
        assert data == {}

    async def test_extension_missing(self):
        conn = self._mock_conn(0)
        status, data = await _step_check_extension(conn)
        assert status == "warning"
        assert "pg_stat_statements" in data["message"]
        assert "fix" in data

    async def test_postgres_error(self):
        conn = AsyncMock()
        conn.fetchval = AsyncMock(
            side_effect=asyncpg.exceptions.PostgresConnectionError("pg error")
        )
        status, data = await _step_check_extension(conn)
        assert status == "fail"
        assert "message" in data


# ---------------------------------------------------------------------------
# Step function tests — check_permissions
# ---------------------------------------------------------------------------


class TestStepCheckPermissions:
    async def test_ok(self):
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])
        status, data = await _step_check_permissions(conn)
        assert status == "ok"

    async def test_insufficient_privilege(self):
        conn = AsyncMock()
        conn.fetch = AsyncMock(side_effect=asyncpg.InsufficientPrivilegeError())
        conn.fetchval = AsyncMock(return_value="testuser")
        status, data = await _step_check_permissions(conn)
        assert status == "warning"
        assert "testuser" in data["fix"]

    async def test_undefined_table(self):
        conn = AsyncMock()
        conn.fetch = AsyncMock(side_effect=asyncpg.UndefinedTableError())
        status, data = await _step_check_permissions(conn)
        assert status == "warning"

    async def test_postgres_error(self):
        conn = AsyncMock()
        conn.fetch = AsyncMock(side_effect=asyncpg.exceptions.PostgresConnectionError("conn error"))
        status, data = await _step_check_permissions(conn)
        assert status == "fail"


# ---------------------------------------------------------------------------
# Step function tests — fetch_queries
# ---------------------------------------------------------------------------


def _make_fetch_row(**overrides: Any):
    base = {
        "queryid": "42",
        "query": "SELECT 1",
        "calls": 10,
        "mean_exec_time_ms": 100.0,
        "total_exec_time_ms": 1000.0,
        "stddev_exec_time_ms": 10.0,
        "rows_per_call": 1.0,
        "shared_blks_hit": 90,
        "shared_blks_read": 10,
        "cache_hit_ratio": 90.0,
    }
    base.update(overrides)
    # Simulate asyncpg Record by wrapping in a MagicMock that supports dict()
    rec = MagicMock(spec=dict)
    rec.__iter__ = lambda self: iter(base.keys())
    rec.items = lambda: base.items()
    rec.keys = lambda: base.keys()
    rec.values = lambda: base.values()
    rec.__getitem__ = lambda self, k: base[k]

    # patch dict(rec) by using __class__ trick — just return a real dict wrapper
    class FakeRecord(dict):
        pass

    return FakeRecord(base)


class TestStepFetchQueries:
    def _mock_conn(self, rows: list) -> AsyncMock:
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=rows)
        return conn

    async def test_returns_scored_queries(self):
        row = _make_fetch_row()
        conn = self._mock_conn([row])
        status, data, queries = await _step_fetch_queries(conn)
        assert status == "ok"
        assert data["count"] == 1
        assert len(queries) == 1
        assert queries[0].queryid == "42"
        assert queries[0].score >= 0

    async def test_empty_result(self):
        conn = self._mock_conn([])
        status, data, queries = await _step_fetch_queries(conn)
        assert status == "ok"
        assert data["count"] == 0
        assert queries == []

    async def test_timeout(self):
        conn = AsyncMock()
        conn.fetch = AsyncMock(side_effect=asyncio.TimeoutError())
        status, data, queries = await _step_fetch_queries(conn)
        assert status == "fail"
        assert "timed out" in data["message"]
        assert queries == []

    async def test_sorted_by_score_desc(self):
        row_low = _make_fetch_row(queryid="1", mean_exec_time_ms=1.0, total_exec_time_ms=10.0)
        row_high = _make_fetch_row(queryid="2", mean_exec_time_ms=999.0, total_exec_time_ms=9990.0)
        conn = self._mock_conn([row_low, row_high])
        status, data, queries = await _step_fetch_queries(conn)
        assert queries[0].queryid == "2"


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------


class TestGetQueriesEndpoint:
    async def test_connect_failure_returns_errors(self):
        with patch("api.queries._step_connect", return_value=(None, "fail", {"message": "bad dsn"})):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/queries/postgresql%3A%2F%2Fbad%2Fdb")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_queries"] == 0
        assert len(body["errors"]) > 0
        assert body["errors"][0]["step"] == "connect"

    async def test_happy_path_returns_queries(self):
        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(return_value=1)
        mock_conn.fetch = AsyncMock(return_value=[_make_fetch_row()])
        mock_conn.close = AsyncMock()

        with patch("api.queries._step_connect", return_value=(mock_conn, "ok", {})):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/queries/postgresql%3A%2F%2Flocalhost%2Fdb")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_queries"] >= 0


# ---------------------------------------------------------------------------
# SSE stream tests
# ---------------------------------------------------------------------------


class TestStreamQueriesEndpoint:
    async def test_stream_returns_event_stream(self):
        with patch("api.queries._step_connect", return_value=(None, "fail", {"message": "no db"})):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                async with client.stream(
                    "GET", "/api/queries/postgresql%3A%2F%2Fbad%2Fdb/stream"
                ) as resp:
                    assert resp.status_code == 200
                    assert "text/event-stream" in resp.headers["content-type"]

    async def test_connect_fail_result_is_last(self):
        with patch("api.queries._step_connect", return_value=(None, "fail", {"message": "no db"})):
            events = await _collect_sse("/api/queries/postgresql%3A%2F%2Fbad%2Fdb/stream")
        assert events[-1]["step"] == "result"
        assert events[-1]["status"] == "fail"

    async def test_happy_path_emits_5_events(self):
        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(return_value=1)
        mock_conn.fetch = AsyncMock(return_value=[_make_fetch_row()])
        mock_conn.close = AsyncMock()

        with patch("api.queries._step_connect", return_value=(mock_conn, "ok", {})):
            events = await _collect_sse("/api/queries/postgresql%3A%2F%2Flocalhost%2Fdb/stream")

        assert len(events) == 5
        steps = [e["step"] for e in events]
        assert steps == [
            "connect",
            "check_extension",
            "check_permissions",
            "fetch_queries",
            "result",
        ]

    async def test_result_event_has_queries(self):
        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(return_value=1)
        mock_conn.fetch = AsyncMock(return_value=[_make_fetch_row()])
        mock_conn.close = AsyncMock()

        with patch("api.queries._step_connect", return_value=(mock_conn, "ok", {})):
            events = await _collect_sse("/api/queries/postgresql%3A%2F%2Flocalhost%2Fdb/stream")

        result = events[-1]
        assert result["step"] == "result"
        assert "queries" in result["data"]
        assert result["data"]["total_queries"] >= 0
