"""Unit tests for agent/autofix/fix_generator.py."""

from __future__ import annotations

import pytest

from agent.autofix.fix_generator import (
    FixGenerator,
    _extract_from_table,
    _extract_where_columns,
    _has_index,
)


# ---------------------------------------------------------------------------
# _extract_from_table
# ---------------------------------------------------------------------------


class TestExtractFromTable:
    def test_simple_table(self):
        assert _extract_from_table("SELECT id FROM users WHERE x = 1") == "users"

    def test_schema_qualified_table(self):
        assert _extract_from_table("SELECT id FROM public.orders WHERE x = 1") == "orders"

    def test_uppercase_from(self):
        assert _extract_from_table("select id from events") == "events"

    def test_no_from_returns_none(self):
        assert _extract_from_table("SELECT 1") is None

    def test_subquery_picks_first_table(self):
        result = _extract_from_table("SELECT * FROM customers WHERE id IN (SELECT id FROM orders)")
        assert result == "customers"


# ---------------------------------------------------------------------------
# _extract_where_columns
# ---------------------------------------------------------------------------


class TestExtractWhereColumns:
    def test_single_equality(self):
        cols = _extract_where_columns("SELECT id FROM users WHERE email = $1")
        assert "email" in cols

    def test_multiple_conditions(self):
        cols = _extract_where_columns("SELECT id FROM t WHERE a = 1 AND b > 2")
        assert "a" in cols
        assert "b" in cols

    def test_no_where_returns_empty(self):
        assert _extract_where_columns("SELECT * FROM users") == []

    def test_in_clause(self):
        cols = _extract_where_columns("SELECT id FROM t WHERE status IN ('a', 'b')")
        assert "status" in cols

    def test_stops_at_order_by(self):
        cols = _extract_where_columns(
            "SELECT id FROM t WHERE created_at > $1 ORDER BY id"
        )
        assert "created_at" in cols
        assert "id" not in cols

    def test_no_duplicates(self):
        cols = _extract_where_columns("SELECT id FROM t WHERE a = 1 AND a = 2")
        assert cols.count("a") == 1


# ---------------------------------------------------------------------------
# _has_index
# ---------------------------------------------------------------------------


class TestHasIndex:
    def test_matching_index_returns_true(self):
        assert _has_index("users", ["email"], ["idx_users_email"])

    def test_no_matching_index_returns_false(self):
        assert not _has_index("users", ["email"], ["idx_orders_id"])

    def test_empty_columns_returns_false(self):
        assert not _has_index("users", [], ["idx_users_email"])

    def test_empty_indexes_returns_false(self):
        assert not _has_index("users", ["email"], [])

    def test_partial_column_match_returns_true(self):
        assert _has_index("orders", ["customer_id"], ["idx_orders_customer_id_status"])

    def test_case_insensitive(self):
        assert _has_index("Users", ["Email"], ["idx_users_email"])


# ---------------------------------------------------------------------------
# FixGenerator.detect_problem
# ---------------------------------------------------------------------------


class TestDetectProblem:
    def setup_method(self):
        self.fg = FixGenerator()

    # Pattern 1 — missing_index
    def test_missing_index_detected(self):
        p = self.fg.detect_problem(
            "SELECT id FROM users WHERE email = $1",
            {"calls": 100, "mean_exec_time_ms": 800, "avg_rows_returned": 5},
        )
        assert p["type"] == "missing_index"
        assert p["table"] == "users"
        assert "email" in p["columns"]

    def test_missing_index_skipped_when_index_exists(self):
        p = self.fg.detect_problem(
            "SELECT * FROM orders WHERE id = $1",
            {
                "calls": 10,
                "mean_exec_time_ms": 50,
                "avg_rows_returned": 1,
                "existing_indexes": ["idx_orders_id"],
            },
        )
        # SELECT * fires before N+1 / missing_limit
        assert p["type"] == "select_star"

    # Pattern 2 — select_star
    def test_select_star_detected(self):
        p = self.fg.detect_problem(
            "SELECT * FROM orders",
            {
                "calls": 10,
                "avg_rows_returned": 5,
                "existing_indexes": ["idx_orders_id"],
            },
        )
        assert p["type"] == "select_star"

    # Pattern 3 — n_plus_one
    def test_n_plus_one_detected(self):
        p = self.fg.detect_problem(
            "SELECT id FROM products WHERE id = $1",
            {"calls": 60_000, "existing_indexes": ["idx_products_id"]},
        )
        assert p["type"] == "n_plus_one"
        assert p["calls_per_day"] == 60_000

    def test_n_plus_one_not_triggered_below_threshold(self):
        p = self.fg.detect_problem(
            "SELECT id FROM products WHERE id = $1",
            {"calls": 49_999, "existing_indexes": ["idx_products_id"]},
        )
        # No other pattern fires for this query, so result is empty
        assert p.get("type") != "n_plus_one"

    # Pattern 4 — missing_limit
    def test_missing_limit_detected(self):
        p = self.fg.detect_problem(
            "SELECT name FROM customers WHERE active = true",
            {
                "calls": 10,
                "avg_rows_returned": 5000,
                "existing_indexes": ["idx_customers_active"],
            },
        )
        assert p["type"] == "missing_limit"

    def test_missing_limit_not_triggered_when_limit_present(self):
        p = self.fg.detect_problem(
            "SELECT name FROM customers WHERE active = true LIMIT 10",
            {
                "calls": 10,
                "avg_rows_returned": 5000,
                "existing_indexes": ["idx_customers_active"],
            },
        )
        assert p.get("type") != "missing_limit"

    def test_missing_limit_not_triggered_for_count_query(self):
        p = self.fg.detect_problem(
            "SELECT COUNT(*) FROM customers WHERE active = true",
            {
                "calls": 10,
                "avg_rows_returned": 5000,
                "existing_indexes": ["idx_customers_active"],
            },
        )
        assert p.get("type") != "missing_limit"

    # Pattern 5 — seq_scan
    def test_seq_scan_detected_on_large_table(self):
        p = self.fg.detect_problem(
            "SELECT name FROM invoices WHERE status = $1",
            {
                "calls": 5,
                "avg_rows_returned": 50,
                "existing_indexes": ["idx_invoices_status"],
                "table_row_counts": {"invoices": 200_000},
            },
        )
        assert p["type"] == "seq_scan"
        assert p["table"] == "invoices"

    def test_seq_scan_not_triggered_on_small_table(self):
        p = self.fg.detect_problem(
            "SELECT name FROM tiny WHERE status = $1",
            {
                "calls": 5,
                "avg_rows_returned": 2,
                "existing_indexes": ["idx_tiny_status"],
                "table_row_counts": {"tiny": 100},
            },
        )
        assert p.get("type") != "seq_scan"

    # No problem
    def test_no_problem_returns_empty_dict(self):
        p = self.fg.detect_problem(
            "SELECT name FROM small_table LIMIT 10",
            {
                "calls": 100,
                "avg_rows_returned": 5,
                "existing_indexes": ["idx_small_table_name"],
                "table_row_counts": {"small_table": 50},
            },
        )
        assert p == {}


# ---------------------------------------------------------------------------
# FixGenerator.generate_fix_sql
# ---------------------------------------------------------------------------


class TestGenerateFixSql:
    def setup_method(self):
        self.fg = FixGenerator()

    def test_missing_index_produces_create_index(self):
        fix = self.fg.generate_fix_sql(
            {"type": "missing_index", "table": "users", "columns": ["email"]}, {}
        )
        assert "CREATE INDEX CONCURRENTLY idx_users_email" in fix["fix_sql"]
        assert "ON users(email)" in fix["fix_sql"]
        assert "DROP INDEX CONCURRENTLY idx_users_email" in fix["rollback_sql"]
        assert fix["risk_level"] == "low"
        assert fix["is_blocking"] is False
        assert fix["estimated_duration"] == "30-60 seconds"

    def test_missing_index_multi_column(self):
        fix = self.fg.generate_fix_sql(
            {"type": "missing_index", "table": "orders", "columns": ["user_id", "status"]},
            {},
        )
        assert "idx_orders_user_id_status" in fix["fix_sql"]
        assert "user_id, status" in fix["fix_sql"]

    def test_seq_scan_same_as_missing_index(self):
        fix = self.fg.generate_fix_sql(
            {"type": "seq_scan", "table": "logs", "columns": ["level"]}, {}
        )
        assert "CREATE INDEX CONCURRENTLY idx_logs_level" in fix["fix_sql"]
        assert fix["is_blocking"] is False

    def test_select_star_with_schema_columns(self):
        fix = self.fg.generate_fix_sql(
            {"type": "select_star", "table": "users"},
            {"columns": {"users": ["id", "name", "email"]}},
        )
        assert "id, name, email" in fix["fix_sql"]
        assert fix["estimated_duration"] == "< 1 second"
        assert fix["is_blocking"] is False

    def test_select_star_without_schema_columns(self):
        fix = self.fg.generate_fix_sql({"type": "select_star", "table": "users"}, {})
        assert "information_schema" in fix["fix_sql"]
        assert "-- No rollback" in fix["rollback_sql"]

    def test_n_plus_one_guidance(self):
        fix = self.fg.generate_fix_sql({"type": "n_plus_one"}, {})
        assert "N+1" in fix["fix_sql"]
        assert fix["is_blocking"] is False
        assert fix["estimated_duration"] == "< 1 second"

    def test_missing_limit_guidance(self):
        fix = self.fg.generate_fix_sql({"type": "missing_limit"}, {})
        assert "LIMIT" in fix["fix_sql"]
        assert fix["estimated_duration"] == "< 1 second"

    def test_unknown_type_returns_no_fix(self):
        fix = self.fg.generate_fix_sql({"type": "unknown"}, {})
        assert "No fix available" in fix["fix_sql"]


# ---------------------------------------------------------------------------
# FixGenerator.calculate_expected_impact
# ---------------------------------------------------------------------------


class TestCalculateExpectedImpact:
    def setup_method(self):
        self.fg = FixGenerator()

    def test_missing_index_100x_speedup(self):
        impact = self.fg.calculate_expected_impact(
            {"type": "missing_index"}, {"calls": 1000, "mean_exec_time_ms": 1000}
        )
        assert impact["before_ms"] == 1000
        assert impact["after_ms"] == pytest.approx(10.0)
        assert impact["speedup_factor"] == pytest.approx(100.0)

    def test_select_star_30_percent_improvement(self):
        impact = self.fg.calculate_expected_impact(
            {"type": "select_star"}, {"calls": 1000, "mean_exec_time_ms": 100}
        )
        assert impact["after_ms"] == pytest.approx(70.0)
        assert impact["speedup_factor"] == pytest.approx(100 / 70, rel=1e-2)

    def test_missing_limit_10x_speedup(self):
        impact = self.fg.calculate_expected_impact(
            {"type": "missing_limit"}, {"calls": 500, "mean_exec_time_ms": 1000}
        )
        assert impact["after_ms"] == pytest.approx(100.0)
        assert impact["speedup_factor"] == pytest.approx(10.0)

    def test_time_saved_per_day_calculation(self):
        # 1000 calls * (1000 - 10) ms saved = 990 000 ms = 16.5 minutes
        impact = self.fg.calculate_expected_impact(
            {"type": "missing_index"}, {"calls": 1000, "mean_exec_time_ms": 1000}
        )
        assert impact["time_saved_per_day_minutes"] == pytest.approx(16.5, rel=1e-3)

    def test_unknown_problem_no_change(self):
        impact = self.fg.calculate_expected_impact(
            {"type": "unknown"}, {"calls": 100, "mean_exec_time_ms": 500}
        )
        assert impact["before_ms"] == impact["after_ms"]
        assert impact["speedup_factor"] == pytest.approx(1.0)
        assert impact["time_saved_per_day_minutes"] == pytest.approx(0.0)

    def test_zero_before_ms_returns_defaults(self):
        impact = self.fg.calculate_expected_impact(
            {"type": "missing_index"}, {"calls": 100, "mean_exec_time_ms": 0}
        )
        assert impact["before_ms"] == 0
        assert impact["after_ms"] == pytest.approx(0.0)
