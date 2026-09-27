"""Tests for GET /api/explain/{connection_id}/{queryid}."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from api.explain import _step_explain, _step_fetch_query


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_ROW: dict[str, Any] = {
    "queryid": "42",
    "query": "SELECT * FROM orders WHERE customer_id = $1",
    "calls": 100,
    "mean_exec_time_ms": 312.4,
    "total_exec_time_ms": 31240.0,
    "stddev_exec_time_ms": 88.1,
    "rows_per_call": 1.2,
    "shared_blks_hit": 9200,
    "shared_blks_read": 340,
    "cache_hit_ratio": 96.4,
}

_FAKE_PLAN = [{"Plan": {"Node Type": "Seq Scan", "Startup Cost": 0.0, "Total Cost": 100.0, "Plan Rows": 1000}}]


def _mock_conn_with_row(row: dict[str, Any] | None) -> AsyncMock:
    """Return an AsyncMock connection whose fetchrow returns the given row dict (or None)."""
    conn = AsyncMock()
    if row is None:
        conn.fetchrow = AsyncMock(return_value=None)
    else:
        # asyncpg Record-like object; dict() should work on it
        class FakeRow(dict):
            pass
        conn.fetchrow = AsyncMock(return_value=FakeRow(row))
    return conn


# ---------------------------------------------------------------------------
# Unit tests — _step_fetch_query
# ---------------------------------------------------------------------------


class TestStepFetchQuery:
    async def test_ok(self):
        conn = _mock_conn_with_row(_FAKE_ROW)
        status, data = await _step_fetch_query(conn, "42")
        assert status == "ok"
        assert data["queryid"] == "42"
        assert data["calls"] == 100

    async def test_not_found(self):
        conn = _mock_conn_with_row(None)
        status, data = await _step_fetch_query(conn, "999")
        assert status == "not_found"
        assert data == {}

    async def test_timeout(self):
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(side_effect=asyncio.TimeoutError())
        status, data = await _step_fetch_query(conn, "42")
        assert status == "fail"
        assert data["message"] == "Step timed out"

    async def test_postgres_error(self):
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(
            side_effect=asyncpg.exceptions.PostgresConnectionError("db gone")
        )
        status, data = await _step_fetch_query(conn, "42")
        assert status == "fail"
        assert "message" in data

    async def test_insufficient_privilege(self):
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(side_effect=asyncpg.InsufficientPrivilegeError())
        conn.fetchval = AsyncMock(return_value="testuser")
        status, data = await _step_fetch_query(conn, "42")
        assert status == "fail"
        assert "testuser" in data["message"]

    async def test_undefined_table(self):
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(side_effect=asyncpg.UndefinedTableError())
        status, data = await _step_fetch_query(conn, "42")
        assert status == "fail"
        assert "pg_stat_statements" in data["message"]


# ---------------------------------------------------------------------------
# Unit tests — _step_explain
# ---------------------------------------------------------------------------


class TestStepExplain:
    async def test_ok_with_json_string(self):
        conn = AsyncMock()
        conn.fetchval = AsyncMock(return_value=json.dumps(_FAKE_PLAN))
        status, plan = await _step_explain(conn, "SELECT 1")
        assert status == "ok"
        assert isinstance(plan, list)
        assert plan[0]["Plan"]["Node Type"] == "Seq Scan"

    async def test_ok_with_parsed_object(self):
        """asyncpg may return an already-parsed list, not a JSON string."""
        conn = AsyncMock()
        conn.fetchval = AsyncMock(return_value=_FAKE_PLAN)
        status, plan = await _step_explain(conn, "SELECT 1")
        assert status == "ok"
        assert plan == _FAKE_PLAN

    async def test_postgres_error_returns_fail(self):
        conn = AsyncMock()
        conn.fetchval = AsyncMock(side_effect=asyncpg.exceptions.InsufficientPrivilegeError())
        status, plan = await _step_explain(conn, "SELECT 1")
        assert status == "fail"
        assert plan is None

    async def test_timeout_returns_fail(self):
        conn = AsyncMock()
        conn.fetchval = AsyncMock(side_effect=asyncio.TimeoutError())
        status, plan = await _step_explain(conn, "SELECT 1")
        assert status == "fail"
        assert plan is None


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------


class TestGetExplainEndpoint:
    _ENCODED_DSN = "postgresql%3A%2F%2Flocalhost%2Fdb"
    _QUERYID = "42"

    async def test_connect_failure_returns_errors(self):
        with patch(
            "api.explain._step_connect",
            return_value=(None, "fail", {"message": "bad dsn"}),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/explain/{self._ENCODED_DSN}/{self._QUERYID}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["query"] is None
        assert body["plan"] is None
        assert any(e["step"] == "connect" for e in body["errors"])

    async def test_happy_path_returns_detail(self):
        class FakeRow(dict):
            pass

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=FakeRow(_FAKE_ROW))
        mock_conn.fetchval = AsyncMock(return_value=json.dumps(_FAKE_PLAN))
        mock_conn.close = AsyncMock()

        with patch(
            "api.explain._step_connect",
            return_value=(mock_conn, "ok", {}),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/explain/{self._ENCODED_DSN}/{self._QUERYID}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["query"] is not None
        assert body["plan"] is not None
        assert body["calls"] == 100
        assert body["errors"] == []

    async def test_queryid_not_found_returns_errors(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_conn.close = AsyncMock()

        with patch(
            "api.explain._step_connect",
            return_value=(mock_conn, "ok", {}),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/explain/{self._ENCODED_DSN}/999999")

        assert resp.status_code == 200
        body = resp.json()
        assert body["query"] is None
        assert body["plan"] is None
        assert any(e["step"] == "fetch_query" for e in body["errors"])

    async def test_explain_fail_still_returns_stats(self):
        class FakeRow(dict):
            pass

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=FakeRow(_FAKE_ROW))
        # fetchval raises — EXPLAIN fails
        mock_conn.fetchval = AsyncMock(
            side_effect=asyncpg.exceptions.InsufficientPrivilegeError()
        )
        mock_conn.close = AsyncMock()

        with patch(
            "api.explain._step_connect",
            return_value=(mock_conn, "ok", {}),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/explain/{self._ENCODED_DSN}/{self._QUERYID}")

        assert resp.status_code == 200
        body = resp.json()
        # Stats populated
        assert body["query"] is not None
        assert body["calls"] == 100
        # Plan absent but explain error recorded
        assert body["plan"] is None
        assert any(e["step"] == "explain" for e in body["errors"])
