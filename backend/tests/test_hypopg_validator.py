"""Unit tests for agent/autofix/hypopg_validator.py.

The PostgreSQL session is faked: these tests assert the *sequence* of statements
the validator issues and how it reads plans back, so the what-if flow is
verified without a live HypoPG install.
"""

from __future__ import annotations

import json
import re

import pytest

from agent.autofix.hypopg_validator import HypoPGValidator


# ---------------------------------------------------------------------------
# Fake asyncpg connection
# ---------------------------------------------------------------------------


class FakeConn:
    """Minimal asyncpg surface: fetchval / fetchrow / execute."""

    def __init__(
        self,
        *,
        ext: str | None = "hypopg",
        plans: list | None = None,
        create_error: Exception | None = None,
        size: int | None = 2_516_582,
        installable: bool = False,
        samples: dict[str, list[str]] | None = None,
        sampled_pages: bool = True,
        trials: int = 1,
    ):
        self.ext = ext
        self.plans = list(plans or [])
        self.create_error = create_error
        self.size = size
        self.installable = installable
        # quote_literal() results per column, as the sampler would see them.
        self.samples = samples or {}
        self.sampled_pages = sampled_pages
        self.trials = trials
        self.sampled_from: list[tuple[str, str, bool]] = []
        self.statements: list[tuple[str, tuple]] = []
        self.dropped: list = []
        self.reset_called = False

    async def fetchval(self, sql: str, *args):
        self.statements.append((sql, args))
        if sql.startswith("SELECT extname"):
            return self.ext
        if sql.lstrip().upper().startswith("EXPLAIN"):
            if not self.plans:
                raise RuntimeError("EXPLAIN denied")
            return json.dumps(self.plans.pop(0))
        if "relation_size" in sql:
            if self.size is None:
                raise RuntimeError("unsupported")
            return self.size
        raise AssertionError(f"unexpected fetchval: {sql}")

    async def fetch(self, sql: str, *args):
        """Sampling queries: return quote_literal() rows for the column."""
        self.statements.append((sql, args))
        if "quote_literal" not in sql:
            raise AssertionError(f"unexpected fetch: {sql}")
        # Both shapes embed: SELECT "<column>" AS col FROM "<table>"
        source = re.search(r'SELECT "([^"]+)" AS col FROM "([^"]+)"', sql)
        if not source:
            raise AssertionError(f"unrecognised sample query: {sql}")
        column, table = source.group(1), source.group(2)
        self.sampled_from.append((table, column, "TABLESAMPLE" in sql))
        if "TABLESAMPLE" in sql and not self.sampled_pages:
            # Simulates a table too small for random page sampling.
            return []
        values = self.samples.get(column, [])
        if "OFFSET" in sql:  # the median variant asks for exactly one value
            return [(value,) for value in values[len(values) // 2 :][:1]]
        return [(value,) for value in values[: self.trials]]

    async def fetchrow(self, sql: str, *args):
        self.statements.append((sql, args))
        if sql.startswith("SELECT * FROM hypopg_create_index"):
            if self.create_error is not None:
                raise self.create_error
            return {"indexoid": 21604, "indexname": "INDEX@2084214"}
        raise AssertionError(f"unexpected fetchrow: {sql}")

    async def execute(self, sql: str, *args):
        self.statements.append((sql, args))
        if sql.startswith("CREATE EXTENSION"):
            if self.installable:
                self.ext = "hypopg"
                return
            raise RuntimeError("extension file hypopg.control not found")
        if sql.startswith("SELECT hypopg_drop_index"):
            self.dropped.append(args[0])
            return
        if sql.startswith("SELECT hypopg_reset"):
            self.reset_called = True
            return
        raise AssertionError(f"unexpected execute: {sql}")


def _node(
    node_type: str,
    cost: float,
    *,
    relation: str = "users",
    index_name: str | None = None,
    child: dict | None = None,
) -> dict:
    node: dict = {
        "Node Type": node_type,
        "Total Cost": cost,
        "Startup Cost": 0.0,
        "Plan Rows": 1,
        "Relation Name": relation,
    }
    if index_name:
        node["Index Name"] = index_name
    if child:
        node["Plans"] = [child]
    return node


def _plan(node: dict) -> list:
    return [{"Plan": node}]


BASELINE_SEQ = _plan(_node("Seq Scan", 71832.0))
IMPROVED_INDEX = _plan(_node("Index Scan", 6221.0, index_name="INDEX@2084214"))


@pytest.fixture
def validator() -> HypoPGValidator:
    """One trial keeps the scripted plan lists easy to follow."""
    return HypoPGValidator(trials=1)


@pytest.fixture
def multi_validator() -> HypoPGValidator:
    return HypoPGValidator(trials=3)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestValidatedFix:
    async def test_reports_planner_cost_before_and_after(self, validator):
        conn = FakeConn(plans=[BASELINE_SEQ, IMPROVED_INDEX])

        result = await validator.validate_index_fix(
            conn,
            table="users",
            columns=["email"],
            slow_query="SELECT id FROM users WHERE email = $1",
        )

        assert result["validated"] is True
        assert result["reason"] is None
        assert result["baseline_cost"] == 71832.0
        assert result["improved_cost"] == 6221.0
        assert result["cost_reduction_percent"] == 91.3
        assert result["baseline_scan_type"] == "Seq Scan"
        assert result["improved_scan_type"] == "Index Scan"
        assert result["planner_would_use_index"] is True
        assert "PostgreSQL planner confirmed" in result["proof_statement"]

    async def test_estimates_index_size(self, validator):
        conn = FakeConn(plans=[BASELINE_SEQ, IMPROVED_INDEX], size=2_516_582)

        result = await validator.validate_index_fix(
            conn,
            table="users",
            columns=["email"],
            slow_query="SELECT 1 WHERE email = $1",
        )

        assert result["estimated_size_bytes"] == 2_516_582

    async def test_size_is_optional(self, validator):
        conn = FakeConn(plans=[BASELINE_SEQ, IMPROVED_INDEX], size=None)

        result = await validator.validate_index_fix(
            conn, table="users", columns=["email"], slow_query="SELECT 1"
        )

        assert result["validated"] is True
        assert result["estimated_size_bytes"] is None

    async def test_always_drops_the_hypothetical_index(self, validator):
        conn = FakeConn(plans=[BASELINE_SEQ, IMPROVED_INDEX])

        await validator.validate_index_fix(
            conn, table="users", columns=["email"], slow_query="SELECT 1"
        )

        assert conn.dropped == [21604]

    async def test_create_uses_parameterised_ddl_without_concurrently(self, validator):
        conn = FakeConn(plans=[BASELINE_SEQ, IMPROVED_INDEX])

        await validator.validate_index_fix(
            conn,
            table="users",
            columns=["email"],
            slow_query="SELECT 1",
            index_ddl="CREATE INDEX CONCURRENTLY idx_users_email\n  ON users(email);",
        )

        created = [
            (sql, args)
            for sql, args in conn.statements
            if sql.startswith("SELECT * FROM hypopg")
        ]
        ddl = created[0][1][0]
        assert "CONCURRENTLY" not in ddl.upper()
        assert ddl == "CREATE INDEX idx_users_email ON users(email)"
        # The DDL is bound as a parameter, never interpolated into the statement.
        assert "users(email)" not in next(
            sql for sql, _ in conn.statements if sql.startswith("SELECT * FROM hypopg")
        )


# ---------------------------------------------------------------------------
# Unvalidated paths — must degrade, never raise
# ---------------------------------------------------------------------------


class TestDegradedValidation:
    async def test_missing_extension_is_reported_not_raised(self, validator):
        conn = FakeConn(ext=None, plans=[BASELINE_SEQ, IMPROVED_INDEX])

        result = await validator.validate_index_fix(
            conn, table="users", columns=["email"], slow_query="SELECT 1"
        )

        assert result["validated"] is False
        assert result["reason"] == "hypopg_unavailable"
        assert result["proof_statement"] is None
        # No point running EXPLAIN when there is nothing to compare against.
        explained = [s for s in conn.statements if s[0].lstrip().startswith("EXPLAIN")]
        assert explained == []

    async def test_installs_extension_when_permitted(self, validator):
        conn = FakeConn(
            ext=None, installable=True, plans=[BASELINE_SEQ, IMPROVED_INDEX]
        )

        result = await validator.validate_index_fix(
            conn, table="users", columns=["email"], slow_query="SELECT 1"
        )

        assert result["validated"] is True
        assert any(s[0].startswith("CREATE EXTENSION") for s in conn.statements)

    async def test_baseline_explain_failure(self, validator):
        conn = FakeConn(plans=[])

        result = await validator.validate_index_fix(
            conn, table="users", columns=["email"], slow_query="DELETE FROM users"
        )

        assert result["validated"] is False
        assert result["reason"] == "explain_failed"

    async def test_improved_explain_failure_still_cleans_up(self, validator):
        conn = FakeConn(plans=[BASELINE_SEQ])

        result = await validator.validate_index_fix(
            conn, table="users", columns=["email"], slow_query="SELECT 1"
        )

        assert result["validated"] is False
        assert result["reason"] == "explain_failed"
        assert conn.dropped == [21604]

    async def test_hypopg_create_failure(self, validator):
        conn = FakeConn(
            plans=[BASELINE_SEQ],
            create_error=RuntimeError("HypoPG is not loaded, aborting."),
        )

        result = await validator.validate_index_fix(
            conn, table="users", columns=["email"], slow_query="SELECT 1"
        )

        assert result["validated"] is False
        assert result["reason"] == "hypopg_create_failed"
        # The preload hint is the usual cause, so pass the DB's message through.
        assert "not loaded" in result["detail"]
        assert conn.dropped == []

    async def test_no_columns_skips_validation(self, validator):
        conn = FakeConn(plans=[BASELINE_SEQ, IMPROVED_INDEX])

        result = await validator.validate_index_fix(
            conn, table="users", columns=[], slow_query="SELECT 1"
        )

        assert result["validated"] is False
        assert result["reason"] == "no_index_target"

    async def test_placeholder_column_skips_validation(self, validator):
        conn = FakeConn(plans=[BASELINE_SEQ, IMPROVED_INDEX])

        result = await validator.validate_index_fix(
            conn, table="<table>", columns=["<column>"], slow_query="SELECT 1"
        )

        assert result["validated"] is False
        assert result["reason"] == "no_index_target"


# ---------------------------------------------------------------------------
# Planner judgement
# ---------------------------------------------------------------------------


class TestPlannerJudgement:
    async def test_cost_drop_without_scan_change_is_limited(self, validator):
        conn = FakeConn(
            plans=[
                BASELINE_SEQ,
                _plan(_node("Seq Scan", 71000.0)),
            ]
        )

        result = await validator.validate_index_fix(
            conn, table="users", columns=["email"], slow_query="SELECT 1"
        )

        assert result["validated"] is True
        assert result["planner_would_use_index"] is False
        assert "Impact may be limited" in result["proof_statement"]

    async def test_detects_nested_index_scan(self, validator):
        conn = FakeConn(
            plans=[
                _plan(_node("Limit", 71832.0, child=_node("Seq Scan", 71800.0))),
                _plan(
                    _node(
                        "Limit",
                        6221.0,
                        child=_node("Index Scan", 6200.0, index_name="INDEX@2084214"),
                    )
                ),
            ]
        )

        result = await validator.validate_index_fix(
            conn, table="users", columns=["email"], slow_query="SELECT 1"
        )

        assert result["baseline_scan_type"] == "Seq Scan"
        assert result["improved_scan_type"] == "Index Scan"
        assert result["planner_would_use_index"] is True

    async def test_bitmap_scan_counts_as_using_index(self, validator):
        conn = FakeConn(
            plans=[
                BASELINE_SEQ,
                _plan(
                    _node(
                        "Bitmap Heap Scan",
                        6221.0,
                        child=_node("Bitmap Index Scan", 6100.0),
                    )
                ),
            ]
        )

        result = await validator.validate_index_fix(
            conn, table="users", columns=["email"], slow_query="SELECT 1"
        )

        assert result["planner_would_use_index"] is True

    async def test_zero_baseline_cost_avoids_division_error(self, validator):
        conn = FakeConn(plans=[_plan(_node("Seq Scan", 0.0)), IMPROVED_INDEX])

        result = await validator.validate_index_fix(
            conn, table="users", columns=["email"], slow_query="SELECT 1"
        )

        assert result["validated"] is True
        assert result["cost_reduction_percent"] == 0.0

    async def test_size_function_naming_is_tolerated(self, validator):
        conn = FakeConn(plans=[BASELINE_SEQ, IMPROVED_INDEX])
        conn.size = None  # hypopg_relation_size missing → try the other name

        result = await validator.validate_index_fix(
            conn, table="users", columns=["email"], slow_query="SELECT 1"
        )

        size_calls = [s for s in conn.statements if "relation_size" in s[0]]
        assert len(size_calls) == 2
        assert result["estimated_size_bytes"] is None


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_prepare_for_explain_uses_resolved_params(self, validator):
        query = "/*app:orders*/ SELECT id FROM users WHERE email = $1 AND age > $2"

        prepared = validator._prepare_for_explain(
            query, {"$1": "'a@b.com'", "$2": "30"}
        )

        assert "/*" not in prepared
        assert "$" not in prepared
        assert "email = 'a@b.com'" in prepared
        assert "age > 30" in prepared
        assert not prepared.endswith(";")

    def test_unresolved_parameter_is_never_null(self, validator):
        """NULL would let the planner skip the table entirely (0-cost Result)."""
        prepared = validator._prepare_for_explain(
            "SELECT id FROM t WHERE email = $1", {}
        )

        assert "NULL" not in prepared.upper()
        assert "$" not in prepared

    async def test_comparison_parameter_without_a_sample_uses_unknown_literal(
        self, validator
    ):
        conn = FakeConn(plans=[BASELINE_SEQ, IMPROVED_INDEX])

        resolved = await validator._resolve_parameters(
            conn, "SELECT 1 FROM t WHERE email = $1", "t"
        )

        assert resolved == {"$1": ["'1'"]}

    async def test_keyword_parameter_resolves_to_integer_one(self, validator):
        conn = FakeConn(plans=[BASELINE_SEQ, IMPROVED_INDEX])

        resolved = await validator._resolve_parameters(
            conn, "SELECT id FROM t WHERE x = 1 LIMIT $1", "t"
        )

        assert resolved == {"$1": ["1"]}

    def test_prepare_for_explain_removes_line_comments(self, validator):
        prepared = validator._prepare_for_explain("SELECT 1 -- inline note\n", {})
        assert prepared == "SELECT 1"

    def test_prepare_for_explain_empty_input(self, validator):
        assert validator._prepare_for_explain("", {}) == ""

    def test_falls_back_to_table_and_columns_when_ddl_unusable(self, validator):
        ddl = validator._build_hypo_ddl(
            "users", ["email", "created_at"], "-- code change only"
        )

        assert ddl == (
            "CREATE INDEX idx_users_email_created_at ON users(email, created_at)"
        )

    async def test_is_available_reports_extension_state(self, validator):
        assert await validator.is_available(FakeConn(ext="hypopg")) is True
        assert await validator.is_available(FakeConn(ext=None)) is False


# ---------------------------------------------------------------------------
# Parameter stand-in values (the $1 problem)
# ---------------------------------------------------------------------------


class TestParameterResolution:
    async def test_sampled_value_replaces_the_parameter(self, validator):
        conn = FakeConn(
            plans=[BASELINE_SEQ, IMPROVED_INDEX],
            samples={"email": ["user_1@example.com"]},
        )

        await validator.validate_index_fix(
            conn,
            table="users",
            columns=["email"],
            slow_query="SELECT id FROM users WHERE email = $1",
        )

        explained = [
            sql for sql, _ in conn.statements if sql.startswith("EXPLAIN")
        ]
        assert "email = 'user_1@example.com'" in explained[0]
        assert "NULL" not in explained[0]

    async def test_sampling_avoids_the_first_physical_row(self, validator):
        """Insertion-ordered rows correlate with old data and skew selectivity."""
        conn = FakeConn(
            plans=[BASELINE_SEQ, IMPROVED_INDEX],
            samples={"category": ["'Books'"]},
        )

        await validator.validate_index_fix(
            conn,
            table="products",
            columns=["category"],
            slow_query="SELECT id FROM products WHERE category = $1",
        )

        sampled = [sql for sql, _ in conn.statements if "quote_literal" in sql]
        assert "TABLESAMPLE" in sampled[0]

    async def test_falls_back_to_a_plain_scan_when_pages_sample_empty(
        self, validator
    ):
        conn = FakeConn(
            plans=[BASELINE_SEQ, IMPROVED_INDEX],
            samples={"category": ["'Books'"]},
            sampled_pages=False,
        )

        await validator.validate_index_fix(
            conn,
            table="products",
            columns=["category"],
            slow_query="SELECT id FROM products WHERE category = $1",
        )

        sampled = [sql for sql, _ in conn.statements if "quote_literal" in sql]
        assert len(sampled) == 2
        assert "TABLESAMPLE" not in sampled[1]
        explained = [sql for sql, _ in conn.statements if sql.startswith("EXPLAIN")]
        assert "category = 'Books'" in explained[0]

    async def test_limit_parameter_becomes_an_integer(self, validator):
        conn = FakeConn(plans=[BASELINE_SEQ, IMPROVED_INDEX])

        await validator.validate_index_fix(
            conn,
            table="orders",
            columns=["user_id"],
            slow_query="SELECT id FROM orders WHERE user_id = $1 LIMIT $2",
        )

        explained = [sql for sql, _ in conn.statements if sql.startswith("EXPLAIN")]
        assert "LIMIT 1" in explained[0]

    async def test_each_parameter_resolves_to_its_own_column(self, validator):
        conn = FakeConn(
            plans=[BASELINE_SEQ, IMPROVED_INDEX],
            samples={"user_id": ["42"], "order_status": ["shipped"]},
        )

        await validator.validate_index_fix(
            conn,
            table="orders",
            columns=["user_id"],
            slow_query="SELECT id FROM orders WHERE user_id = $1 "
                       "AND order_status = $2",
        )

        explained = [sql for sql, _ in conn.statements if sql.startswith("EXPLAIN")]
        assert "user_id = '42'" in explained[0]
        assert "order_status = 'shipped'" in explained[0]

    async def test_column_is_sampled_once_across_trials(self, multi_validator):
        conn = FakeConn(
            plans=[BASELINE_SEQ, IMPROVED_INDEX] * 3,
            samples={"category": ["'Books'", "'Toys'", "'Sports'"]},
            trials=3,
        )

        await multi_validator.validate_index_fix(
            conn,
            table="products",
            columns=["category"],
            slow_query="SELECT id FROM products WHERE category = $1",
        )

        sampled = [sql for sql, _ in conn.statements if "quote_literal" in sql]
        assert len(sampled) == 1

    async def test_each_trial_uses_a_different_sampled_value(
        self, multi_validator
    ):
        conn = FakeConn(
            plans=[BASELINE_SEQ, IMPROVED_INDEX] * 3,
            samples={"category": ["'Books'", "'Toys'", "'Sports'"]},
            trials=3,
        )

        await multi_validator.validate_index_fix(
            conn,
            table="products",
            columns=["category"],
            slow_query="SELECT id FROM products WHERE category = $1",
        )

        explained = [sql for sql, _ in conn.statements if sql.startswith("EXPLAIN")]
        assert "'Books'" in explained[0]
        assert "'Toys'" in explained[2]
        assert "'Sports'" in explained[4]

    async def test_equality_prefers_the_most_frequent_values(self, multi_validator):
        """The worst-case key: if it still pays, the claim is safe."""
        conn = FakeConn(
            plans=[BASELINE_SEQ, IMPROVED_INDEX] * 3,
            samples={"category": ["'Books'", "'Toys'", "'Sports'"]},
            trials=3,
        )

        await multi_validator.validate_index_fix(
            conn,
            table="products",
            columns=["category"],
            slow_query="SELECT id FROM products WHERE category = $1",
        )

        sql = [s for s, _ in conn.statements if "quote_literal" in s][0]
        assert "GROUP BY col ORDER BY count(*) DESC" in sql
        assert "OFFSET" not in sql

    async def test_range_uses_a_single_median_value(self, multi_validator):
        """`> 10` and `> 4990` justify different plans, so no arbitrary pick."""
        conn = FakeConn(
            plans=[BASELINE_SEQ, IMPROVED_INDEX] * 3,
            samples={"entity_id": ["11", "2500", "4990"]},
            trials=3,
        )

        await multi_validator.validate_index_fix(
            conn,
            table="audit_logs",
            columns=["entity_id"],
            slow_query="SELECT id FROM audit_logs WHERE entity_id > $1",
        )

        sql = [s for s, _ in conn.statements if "quote_literal" in s][0]
        assert "OFFSET (SELECT count(*) / 2 FROM s)" in sql
        explained = [s for s, _ in conn.statements if s.startswith("EXPLAIN")]
        # every trial costs the same representative value
        assert all("entity_id = '2500'" in s or "entity_id > '2500'" in s
                   for s in explained)


# ---------------------------------------------------------------------------
# Verdict across trials
# ---------------------------------------------------------------------------


class TestTrialVerdict:
    async def test_all_trials_must_agree_to_claim_the_index(
        self, multi_validator
    ):
        """A majority was measured to be fooled by low-cardinality columns."""
        conn = FakeConn(
            plans=[
                BASELINE_SEQ, IMPROVED_INDEX,      # index used
                BASELINE_SEQ, IMPROVED_INDEX,      # index used
                BASELINE_SEQ, _plan(_node("Seq Scan", 71832.0)),  # not used
            ],
            samples={"email": ["a@b.co", "c@d.com", "e@f.com"]},
            trials=3,
        )

        result = await multi_validator.validate_index_fix(
            conn,
            table="users",
            columns=["email"],
            slow_query="SELECT id FROM users WHERE email = $1",
        )

        assert result["trials_run"] == 3
        assert result["trials_using_index"] == 2
        assert result["planner_would_use_index"] is False
        assert "only 2 of 3 sampled values" in result["proof_statement"]

    async def test_unanimous_trials_confirm_the_index(self, multi_validator):
        conn = FakeConn(
            plans=[BASELINE_SEQ, IMPROVED_INDEX] * 3,
            samples={"email": ["a@b.co", "c@d.com", "e@f.com"]},
            trials=3,
        )

        result = await multi_validator.validate_index_fix(
            conn,
            table="users",
            columns=["email"],
            slow_query="SELECT id FROM users WHERE email = $1",
        )

        assert result["planner_would_use_index"] is True
        assert result["trials_using_index"] == 3
        assert "for all 3 sampled values" in result["proof_statement"]

    async def test_range_predicate_is_judged_on_the_median_value(
        self, multi_validator
    ):
        """`col > median` is the typical case; an index must survive it."""
        conn = FakeConn(
            plans=[BASELINE_SEQ, _plan(_node("Seq Scan", 71832.0))],
            samples={"entity_id": ["1", "2500", "4999"]},
            trials=3,
        )

        result = await multi_validator.validate_index_fix(
            conn,
            table="audit_logs",
            columns=["entity_id"],
            slow_query="SELECT id FROM audit_logs WHERE entity_id > $1",
        )

        # One representative value, so exactly one trial - no lucky redraws.
        assert result["trials_run"] == 1
        assert result["planner_would_use_index"] is False
        assert result["sample_values"] == {"$1": "'2500'"}

    async def test_reduction_range_is_reported(self, multi_validator):
        conn = FakeConn(
            plans=[
                BASELINE_SEQ, _plan(_node("Index Scan", 60000.0,
                                          index_name="INDEX@2084214")),
                BASELINE_SEQ, _plan(_node("Index Scan", 30000.0,
                                          index_name="INDEX@2084214")),
                BASELINE_SEQ, _plan(_node("Index Scan", 10000.0,
                                          index_name="INDEX@2084214")),
            ],
            samples={"email": ["a@b.co", "c@d.com", "e@f.com"]},
            trials=3,
        )

        result = await multi_validator.validate_index_fix(
            conn,
            table="users",
            columns=["email"],
            slow_query="SELECT id FROM users WHERE email = $1",
        )

        # baseline 71832 -> 60000 / 30000 / 10000
        assert result["cost_reduction_min"] == 16.5
        assert result["cost_reduction_max"] == 86.1
        assert result["cost_reduction_percent"] == 58.2  # median trial
        assert "range 16.5%-86.1%" in result["proof_statement"]

    async def test_sample_values_are_exposed_for_audit(self, validator):
        conn = FakeConn(
            plans=[BASELINE_SEQ, IMPROVED_INDEX],
            samples={"email": ["someone@example.com"]},
        )

        result = await validator.validate_index_fix(
            conn,
            table="users",
            columns=["email"],
            slow_query="SELECT id FROM users WHERE email = $1",
        )

        assert result["sample_values"] == {"$1": "'someone@example.com'"}

    async def test_join_alias_samples_from_the_aliased_table(self, validator):
        conn = FakeConn(
            plans=[BASELINE_SEQ, IMPROVED_INDEX],
            samples={"email": ["u@e.com"]},
        )

        await validator.validate_index_fix(
            conn,
            table="users",
            columns=["email"],
            slow_query="SELECT o.id FROM orders o JOIN users u "
                       "ON u.id = o.user_id WHERE u.email = $1",
        )

        sampled = [sql for sql, _ in conn.statements if "quote_literal" in sql]
        assert 'FROM "users"' in sampled[0]
        assert '"orders"' not in sampled[0]

    async def test_quoted_sample_is_escaped(self, validator):
        assert validator._as_literal("O'Brien") == "'O''Brien'"
        assert validator._as_literal(None) is None
        assert validator._as_literal("") is None
