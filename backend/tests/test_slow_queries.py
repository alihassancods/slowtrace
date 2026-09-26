"""Unit tests for src/db_engine/slow_queries.py."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.db_engine.slow_queries import (
    calculate_total_wasted,
    classify_severity,
    get_slow_queries,
    humanize_time_wasted,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(**kwargs) -> MagicMock:
    """Return a mock that behaves like an asyncpg Record (dict-style access)."""
    rec = MagicMock()
    rec.__getitem__ = lambda self, k: kwargs[k]
    rec.keys = lambda: list(kwargs.keys())
    rec.items = lambda: kwargs.items()

    # dict(record) uses __iter__ + __getitem__ via asyncpg; we back it with
    # a real dict so dict(rec) works properly.
    real = dict(**kwargs)
    rec.__iter__ = lambda self: iter(real)
    rec.__len__ = lambda self: len(real)
    # Make dict(rec) work via the mapping protocol
    rec.__class__ = type(
        "FakeRecord",
        (),
        {
            "keys": lambda self: list(real.keys()),
            "__getitem__": lambda self, k: real[k],
            "__iter__": lambda self: iter(real),
            "__len__": lambda self: len(real),
            "items": lambda self: real.items(),
        },
    )
    return rec


def _make_slow_query_record(**overrides) -> MagicMock:
    defaults = dict(
        queryid=1,
        query="SELECT * FROM events WHERE user_id = $1",
        calls=50,
        mean_exec_time=750.0,
        total_exec_time=37500.0,
        minutes_wasted=Decimal("0.63"),
        stddev_exec_time=50.0,
        rows=5000,
        avg_rows_returned=Decimal("100.0"),
        shared_blks_hit=4000,
        shared_blks_read=200,
        cache_hit_ratio=Decimal("95.2"),
    )
    defaults.update(overrides)
    rec = MagicMock()
    data = dict(defaults)
    rec.__iter__ = lambda self: iter(data)
    rec.__len__ = lambda self: len(data)
    rec.__getitem__ = lambda self, k: data[k]
    rec.keys = lambda: list(data.keys())
    rec.items = lambda: data.items()
    return rec


# ---------------------------------------------------------------------------
# get_slow_queries
# ---------------------------------------------------------------------------


class TestGetSlowQueries:
    async def test_returns_list_of_dicts(self):
        db = MagicMock()
        row = _make_slow_query_record()
        db.fetch = AsyncMock(return_value=[row])

        result = await get_slow_queries(db, limit=10)

        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], dict)

    async def test_passes_limit_as_positional_arg(self):
        db = MagicMock()
        db.fetch = AsyncMock(return_value=[])

        await get_slow_queries(db, limit=5)

        db.fetch.assert_awaited_once()
        call_args = db.fetch.call_args
        # Second positional argument to db.fetch should be the limit value
        assert call_args.args[1] == 5

    async def test_default_limit_is_10(self):
        db = MagicMock()
        db.fetch = AsyncMock(return_value=[])

        await get_slow_queries(db)

        call_args = db.fetch.call_args
        assert call_args.args[1] == 10

    async def test_empty_result(self):
        db = MagicMock()
        db.fetch = AsyncMock(return_value=[])

        result = await get_slow_queries(db)

        assert result == []

    async def test_multiple_rows_returned(self):
        db = MagicMock()
        rows = [_make_slow_query_record(queryid=i) for i in range(3)]
        db.fetch = AsyncMock(return_value=rows)

        result = await get_slow_queries(db)

        assert len(result) == 3

    async def test_sql_excludes_pg_stat_in_where_clause(self):
        """Verify the SQL template filters out pg_stat statements."""
        from src.db_engine.slow_queries import _SLOW_QUERY_SQL

        assert "pg_stat%" in _SLOW_QUERY_SQL
        assert "information_schema%" in _SLOW_QUERY_SQL
        assert "mean_exec_time > 100" in _SLOW_QUERY_SQL
        assert "calls > 10" in _SLOW_QUERY_SQL
        assert "ORDER BY total_exec_time DESC" in _SLOW_QUERY_SQL
        assert "LIMIT $1" in _SLOW_QUERY_SQL


# ---------------------------------------------------------------------------
# calculate_total_wasted
# ---------------------------------------------------------------------------


class TestCalculateTotalWasted:
    def test_empty_list_returns_zero(self):
        assert calculate_total_wasted([]) == 0.0

    def test_single_query(self):
        queries = [{"minutes_wasted": Decimal("5.50")}]
        assert calculate_total_wasted(queries) == pytest.approx(5.5)

    def test_multiple_queries_summed(self):
        queries = [
            {"minutes_wasted": Decimal("1.00")},
            {"minutes_wasted": Decimal("2.50")},
            {"minutes_wasted": Decimal("3.75")},
        ]
        assert calculate_total_wasted(queries) == pytest.approx(7.25)

    def test_none_minutes_wasted_treated_as_zero(self):
        queries = [{"minutes_wasted": None}, {"minutes_wasted": Decimal("4.00")}]
        assert calculate_total_wasted(queries) == pytest.approx(4.0)

    def test_missing_key_treated_as_zero(self):
        queries = [{"query": "SELECT 1"}, {"minutes_wasted": Decimal("2.00")}]
        assert calculate_total_wasted(queries) == pytest.approx(2.0)

    def test_float_values_accepted(self):
        queries = [{"minutes_wasted": 1.5}, {"minutes_wasted": 2.5}]
        assert calculate_total_wasted(queries) == pytest.approx(4.0)


# ---------------------------------------------------------------------------
# humanize_time_wasted
# ---------------------------------------------------------------------------


class TestHumanizeTimeWasted:
    def test_exact_standup_multiples(self):
        # 3 minutes = 1 standup
        result = humanize_time_wasted(3.0)
        assert "1 standup" in result
        assert "3 minutes" in result

    def test_87_minutes(self):
        result = humanize_time_wasted(87.0)
        assert "87 minutes" in result
        assert "29 standup" in result

    def test_zero_minutes(self):
        result = humanize_time_wasted(0.0)
        assert "0 minutes" in result
        assert "0 standup" in result

    def test_fractional_standups_truncated(self):
        # 10 minutes / 3 = 3.33 → truncated to 3
        result = humanize_time_wasted(10.0)
        assert "3 standup" in result

    def test_returns_string(self):
        assert isinstance(humanize_time_wasted(60.0), str)

    def test_contains_wasted_compute(self):
        result = humanize_time_wasted(30.0)
        assert "wasted compute" in result


# ---------------------------------------------------------------------------
# classify_severity
# ---------------------------------------------------------------------------


class TestClassifySeverity:
    # --- critical ---
    def test_exactly_2001ms_is_critical(self):
        assert classify_severity(2001.0) == "critical"

    def test_5000ms_is_critical(self):
        assert classify_severity(5000.0) == "critical"

    def test_boundary_2000ms_is_not_critical(self):
        assert classify_severity(2000.0) != "critical"

    # --- warning ---
    def test_501ms_is_warning(self):
        assert classify_severity(501.0) == "warning"

    def test_1999ms_is_warning(self):
        assert classify_severity(1999.0) == "warning"

    def test_boundary_500ms_is_not_warning(self):
        assert classify_severity(500.0) != "warning"

    # --- slow ---
    def test_101ms_is_slow(self):
        assert classify_severity(101.0) == "slow"

    def test_499ms_is_slow(self):
        assert classify_severity(499.0) == "slow"

    def test_exactly_500ms_is_slow(self):
        assert classify_severity(500.0) == "slow"

    def test_exactly_2000ms_is_warning(self):
        assert classify_severity(2000.0) == "warning"

    # --- return type ---
    def test_returns_string(self):
        for ms in [150.0, 600.0, 3000.0]:
            assert isinstance(classify_severity(ms), str)
