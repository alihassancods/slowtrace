"""Unit tests for agent/autofix/fix_executor.py.

All tests use a temporary fix_history.json so they never touch real disk state.
No real database connection is required — asyncpg calls are mocked.
"""

from __future__ import annotations

import json
import pathlib
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import agent.autofix.fix_executor as fe
from agent.autofix.fix_executor import FixExecutor


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def tmp_history(tmp_path):
    """Redirect fix_history.json to a temp file for every test."""
    original = fe._HISTORY_PATH
    fe._HISTORY_PATH = tmp_path / "fix_history.json"
    yield fe._HISTORY_PATH
    fe._HISTORY_PATH = original


def _make_conn(execute_error=None):
    """Return a mock asyncpg Connection."""
    conn = AsyncMock()
    if execute_error:
        conn.execute = AsyncMock(side_effect=execute_error)
    else:
        conn.execute = AsyncMock(return_value=None)
    conn.fetchrow = AsyncMock(return_value=None)
    return conn


# ---------------------------------------------------------------------------
# History helpers
# ---------------------------------------------------------------------------


class TestHistoryHelpers:
    def test_load_history_missing_file_returns_empty(self, tmp_history):
        assert fe._load_history() == []

    def test_upsert_inserts_new_record(self, tmp_history):
        fe._upsert_record({"fix_id": "aaa", "status": "executing"})
        history = fe._load_history()
        assert len(history) == 1
        assert history[0]["status"] == "executing"

    def test_upsert_updates_existing_record(self, tmp_history):
        fe._upsert_record({"fix_id": "aaa", "status": "executing"})
        fe._upsert_record({"fix_id": "aaa", "status": "success"})
        history = fe._load_history()
        assert len(history) == 1
        assert history[0]["status"] == "success"

    def test_upsert_multiple_records_no_collision(self, tmp_history):
        fe._upsert_record({"fix_id": "aaa", "status": "success"})
        fe._upsert_record({"fix_id": "bbb", "status": "success"})
        assert len(fe._load_history()) == 2

    def test_load_history_invalid_json_returns_empty(self, tmp_history):
        tmp_history.write_text("NOT JSON", encoding="utf-8")
        assert fe._load_history() == []


# ---------------------------------------------------------------------------
# FixExecutor.rollback
# ---------------------------------------------------------------------------


class TestRollback:
    async def test_rollback_not_found_returns_error(self, tmp_history):
        executor = FixExecutor()
        result = await executor.rollback("nonexistent", AsyncMock())
        assert result["status"] == "error"
        assert "not found" in result["error"]

    async def test_rollback_comment_only_sql_returns_error(self, tmp_history):
        fe._upsert_record(
            {"fix_id": "aaa", "status": "success", "rollback_sql": "-- no rollback needed"}
        )
        executor = FixExecutor()
        result = await executor.rollback("aaa", AsyncMock())
        assert result["status"] == "error"
        assert "No executable rollback" in result["error"]

    async def test_rollback_empty_sql_returns_error(self, tmp_history):
        fe._upsert_record({"fix_id": "bbb", "status": "success", "rollback_sql": ""})
        executor = FixExecutor()
        result = await executor.rollback("bbb", AsyncMock())
        assert result["status"] == "error"

    async def test_rollback_success_updates_status(self, tmp_history):
        fe._upsert_record(
            {
                "fix_id": "ccc",
                "status": "success",
                "rollback_sql": "DROP INDEX CONCURRENTLY idx_users_email;",
            }
        )
        conn = _make_conn()
        executor = FixExecutor()
        result = await executor.rollback("ccc", conn)
        assert result["status"] == "manually_rolled_back"
        assert result["fix_id"] == "ccc"
        assert "rolled_back_at" in result
        conn.execute.assert_awaited_once_with("DROP INDEX CONCURRENTLY idx_users_email;")

    async def test_rollback_db_error_returns_error(self, tmp_history):
        import asyncpg

        fe._upsert_record(
            {
                "fix_id": "ddd",
                "status": "success",
                "rollback_sql": "DROP INDEX CONCURRENTLY idx_x;",
            }
        )
        conn = _make_conn(execute_error=asyncpg.exceptions.UndefinedTableError("no such index"))
        executor = FixExecutor()
        result = await executor.rollback("ddd", conn)
        assert result["status"] == "error"
        assert "Rollback failed" in result["error"]


# ---------------------------------------------------------------------------
# FixExecutor.execute
# ---------------------------------------------------------------------------


_SIMPLE_FIX = {
    "fix_sql": "CREATE INDEX CONCURRENTLY idx_users_email ON users(email);",
    "rollback_sql": "DROP INDEX CONCURRENTLY idx_users_email;",
    "query_time_before_ms": 1000.0,
    "query_time_after_ms": 10.0,
    "time_saved_per_day_minutes": 16.5,
}

_CODE_FIX = {
    "fix_sql": "-- Add LIMIT to your query in application code.",
    "rollback_sql": "-- No rollback needed; this is a code change.",
    "query_time_before_ms": 500.0,
    "query_time_after_ms": 50.0,
    "time_saved_per_day_minutes": 5.0,
}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Skip the 3-second settle sleep so tests finish instantly."""
    monkeypatch.setattr("agent.autofix.fix_executor.asyncio.sleep", AsyncMock())


class TestExecute:
    async def test_success_result_shape(self, tmp_history):
        conn = _make_conn()
        with patch("agent.autofix.fix_executor._measure_health", return_value=80):
            executor = FixExecutor()
            result = await executor.execute(_SIMPLE_FIX, conn)

        assert result["status"] == "success"
        assert "fix_id" in result
        assert result["health_before"] == 80
        assert result["health_after"] == 80
        assert result["health_improvement"] == 0
        assert result["speedup_factor"] == pytest.approx(100.0)
        assert result["time_saved_per_day_minutes"] == pytest.approx(16.5)
        assert result["can_rollback"] is True
        assert "rollback_available_until" in result

    async def test_success_record_persisted(self, tmp_history):
        conn = _make_conn()
        with patch("agent.autofix.fix_executor._measure_health", return_value=75):
            executor = FixExecutor()
            result = await executor.execute(_SIMPLE_FIX, conn)

        history = fe._load_history()
        assert len(history) == 1
        assert history[0]["status"] == "success"
        assert history[0]["fix_id"] == result["fix_id"]

    async def test_executing_record_written_before_sql(self, tmp_history):
        """The 'executing' record must hit disk before the SQL runs."""
        execution_order = []

        async def mock_execute(sql):
            execution_order.append("sql")

        conn = AsyncMock()
        conn.execute = mock_execute

        original_upsert = fe._upsert_record

        def tracking_upsert(record):
            if record.get("status") == "executing":
                execution_order.append("record")
            original_upsert(record)

        with (
            patch("agent.autofix.fix_executor._measure_health", return_value=70),
            patch("agent.autofix.fix_executor._upsert_record", side_effect=tracking_upsert),
        ):
            executor = FixExecutor()
            await executor.execute(_SIMPLE_FIX, conn)

        assert execution_order.index("record") < execution_order.index("sql")

    async def test_sql_error_returns_error_status(self, tmp_history):
        import asyncpg

        conn = _make_conn(
            execute_error=asyncpg.exceptions.UndefinedTableError("table not found")
        )
        with patch("agent.autofix.fix_executor._measure_health", return_value=80):
            executor = FixExecutor()
            result = await executor.execute(_SIMPLE_FIX, conn)

        assert result["status"] == "error"
        assert "fix_id" in result
        assert "error" in result
        history = fe._load_history()
        assert history[0]["status"] == "error"

    async def test_auto_rollback_when_health_drops(self, tmp_history):
        conn = _make_conn()
        health_values = iter([90, 70])  # before=90, after=70 → delta=-20

        with patch(
            "agent.autofix.fix_executor._measure_health",
            side_effect=lambda c: health_values.__next__(),
        ):
            executor = FixExecutor()
            result = await executor.execute(_SIMPLE_FIX, conn)

        assert result["status"] == "auto_rolled_back"
        assert result["health_before"] == 90
        assert result["health_after"] == 70
        assert result["health_delta"] == -20
        assert "rolled back automatically" in result["reason"]
        # rollback SQL should have been executed
        assert conn.execute.call_count == 2  # fix + rollback

    async def test_no_auto_rollback_when_health_drops_exactly_10(self, tmp_history):
        conn = _make_conn()
        health_values = iter([90, 80])  # delta = -10, not < -10

        with patch(
            "agent.autofix.fix_executor._measure_health",
            side_effect=lambda c: health_values.__next__(),
        ):
            executor = FixExecutor()
            result = await executor.execute(_SIMPLE_FIX, conn)

        assert result["status"] == "success"

    async def test_code_fix_can_rollback_is_false(self, tmp_history):
        conn = _make_conn()
        with patch("agent.autofix.fix_executor._measure_health", return_value=80):
            executor = FixExecutor()
            result = await executor.execute(_CODE_FIX, conn)

        assert result["can_rollback"] is False

    async def test_index_progress_collected_for_create_index(self, tmp_history):
        conn = _make_conn()

        progress_row = MagicMock()
        progress_row.__getitem__ = lambda self, k: {
            "phase": "building index",
            "blocks_done": 50,
            "blocks_total": 100,
            "tuples_done": 500,
            "tuples_total": 1000,
        }[k]
        # First call returns a row, second returns None (finished)
        conn.fetchrow = AsyncMock(side_effect=[progress_row, None])

        with patch("agent.autofix.fix_executor._measure_health", return_value=80):
            executor = FixExecutor()
            result = await executor.execute(_SIMPLE_FIX, conn)

        assert result["status"] == "success"
        assert isinstance(result["index_progress"], list)
