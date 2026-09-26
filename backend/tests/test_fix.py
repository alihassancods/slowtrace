"""Tests for GET /api/fix/{connection_id}/{queryid} — fix wizard."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from api.fix import (
    _analyse,
    _rule_high_mean,
    _rule_high_stddev,
    _rule_low_cache,
    _rule_missing_vacuum,
    _rule_seq_scan,
    _walk_plan,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_ROW: dict[str, Any] = {
    "queryid": "42",
    "query": "SELECT * FROM orders WHERE customer_id = $1",
    "calls": 100,
    "mean_exec_time_ms": 600.0,
    "total_exec_time_ms": 60000.0,
    "stddev_exec_time_ms": 350.0,
    "rows_per_call": 1.2,
    "shared_blks_hit": 100,
    "shared_blks_read": 500,
    "cache_hit_ratio": 70.0,
}

# Plan that has a Seq Scan on a large table (triggers rules 1 and 5)
_FAKE_PLAN = [
    {
        "Plan": {
            "Node Type": "Seq Scan",
            "Startup Cost": 0.0,
            "Total Cost": 9500.0,
            "Plan Rows": 5000,
            "Relation Name": "orders",
            "Plans": [],
        }
    }
]

# Three-level nested plan
_NESTED_PLAN = [
    {
        "Plan": {
            "Node Type": "Hash Join",
            "Plans": [
                {
                    "Node Type": "Seq Scan",
                    "Relation Name": "orders",
                    "Plan Rows": 2000,
                    "Plans": [
                        {
                            "Node Type": "Index Scan",
                            "Relation Name": "customers",
                            "Plans": [],
                        }
                    ],
                },
                {
                    "Node Type": "Hash",
                    "Plans": [],
                },
            ],
        }
    }
]


def _mock_conn(row: dict[str, Any] | None, plan: Any = None) -> AsyncMock:
    """Return a mock connection for the full connect→fetch→explain flow."""
    conn = AsyncMock()

    class FakeRow(dict):
        pass

    conn.fetchrow = AsyncMock(return_value=FakeRow(row) if row is not None else None)
    conn.fetchval = AsyncMock(return_value=json.dumps(plan) if plan is not None else None)
    conn.close = AsyncMock()
    return conn


# ---------------------------------------------------------------------------
# Unit tests — _walk_plan
# ---------------------------------------------------------------------------


class TestWalkPlan:
    def test_flat_plan(self):
        plan = [{"Plan": {"Node Type": "Seq Scan", "Plans": []}}]
        nodes = _walk_plan(plan)
        assert len(nodes) == 1
        assert nodes[0]["Node Type"] == "Seq Scan"

    def test_nested_plan(self):
        nodes = _walk_plan(_NESTED_PLAN)
        # Hash Join + Seq Scan + Index Scan + Hash = 4 nodes
        assert len(nodes) == 4
        node_types = {n["Node Type"] for n in nodes}
        assert node_types == {"Hash Join", "Seq Scan", "Index Scan", "Hash"}

    def test_empty_plan(self):
        assert _walk_plan([]) == []

    def test_three_level_plan(self):
        plan = [
            {
                "Plan": {
                    "Node Type": "A",
                    "Plans": [
                        {
                            "Node Type": "B",
                            "Plans": [{"Node Type": "C", "Plans": []}],
                        }
                    ],
                }
            }
        ]
        nodes = _walk_plan(plan)
        assert [n["Node Type"] for n in nodes] == ["A", "B", "C"]


# ---------------------------------------------------------------------------
# Unit tests — rule functions (trigger + non-trigger for each)
# ---------------------------------------------------------------------------


class TestRuleSeqScan:
    def _node(self, node_type: str = "Seq Scan", plan_rows: int = 5000, relation: str | None = "orders") -> dict:
        n: dict[str, Any] = {"Node Type": node_type, "Plan Rows": plan_rows}
        if relation:
            n["Relation Name"] = relation
        return n

    def test_triggers_on_large_seq_scan(self):
        rec = _rule_seq_scan([self._node()])
        assert rec is not None
        assert rec.id == "seq_scan_large_table"
        assert rec.severity == "high"
        assert "orders" in rec.sql

    def test_no_trigger_below_threshold(self):
        rec = _rule_seq_scan([self._node(plan_rows=500)])
        assert rec is None

    def test_no_trigger_on_index_scan(self):
        rec = _rule_seq_scan([self._node(node_type="Index Scan", plan_rows=50000)])
        assert rec is None

    def test_generic_snippet_when_no_relation_name(self):
        rec = _rule_seq_scan([{"Node Type": "Seq Scan", "Plan Rows": 2000}])
        assert rec is not None
        assert "<table>" in rec.sql

    def test_empty_nodes(self):
        assert _rule_seq_scan([]) is None


class TestRuleLowCache:
    def _row(self, ratio: float | None) -> dict:
        return {"cache_hit_ratio": ratio}

    def test_triggers_medium_between_80_and_95(self):
        rec = _rule_low_cache(self._row(85.0))
        assert rec is not None
        assert rec.id == "low_cache_hit"
        assert rec.severity == "medium"

    def test_triggers_high_below_80(self):
        rec = _rule_low_cache(self._row(70.0))
        assert rec is not None
        assert rec.severity == "high"

    def test_no_trigger_at_or_above_95(self):
        assert _rule_low_cache(self._row(95.0)) is None
        assert _rule_low_cache(self._row(100.0)) is None

    def test_no_trigger_on_none(self):
        assert _rule_low_cache(self._row(None)) is None


class TestRuleHighMean:
    def _row(self, mean_ms: float, total_ms: float = 1000.0) -> dict:
        return {"mean_exec_time_ms": mean_ms, "total_exec_time_ms": total_ms}

    def test_triggers_medium_between_500_and_2000(self):
        rec = _rule_high_mean(self._row(800.0))
        assert rec is not None
        assert rec.id == "high_mean_exec_time"
        assert rec.severity == "medium"

    def test_triggers_high_at_or_above_2000(self):
        rec = _rule_high_mean(self._row(2500.0))
        assert rec is not None
        assert rec.severity == "high"

    def test_no_trigger_below_500(self):
        assert _rule_high_mean(self._row(499.0)) is None
        assert _rule_high_mean(self._row(0.0)) is None


class TestRuleHighStddev:
    def _row(self, mean_ms: float, stddev_ms: float) -> dict:
        return {"mean_exec_time_ms": mean_ms, "stddev_exec_time_ms": stddev_ms}

    def test_triggers_when_ratio_at_50_percent(self):
        rec = _rule_high_stddev(self._row(mean_ms=200.0, stddev_ms=100.0))
        assert rec is not None
        assert rec.id == "high_stddev"
        assert rec.severity == "medium"

    def test_no_trigger_below_ratio(self):
        rec = _rule_high_stddev(self._row(mean_ms=200.0, stddev_ms=50.0))
        assert rec is None

    def test_no_trigger_when_mean_below_100(self):
        # ratio would be 0.6 but mean is too low
        rec = _rule_high_stddev(self._row(mean_ms=50.0, stddev_ms=30.0))
        assert rec is None


class TestRuleMissingVacuum:
    def _row(self, blks_hit: int, blks_read: int) -> dict:
        return {"shared_blks_hit": blks_hit, "shared_blks_read": blks_read}

    def _seq_scan_nodes(self, relation: str | None = "orders") -> list[dict]:
        n: dict[str, Any] = {"Node Type": "Seq Scan"}
        if relation:
            n["Relation Name"] = relation
        return [n]

    def test_triggers_when_more_reads_than_hits(self):
        rec = _rule_missing_vacuum(self._row(100, 500), self._seq_scan_nodes())
        assert rec is not None
        assert rec.id == "missing_vacuum"
        assert rec.severity == "low"
        assert "orders" in rec.sql

    def test_no_trigger_when_more_hits_than_reads(self):
        rec = _rule_missing_vacuum(self._row(900, 100), self._seq_scan_nodes())
        assert rec is None

    def test_no_trigger_when_no_seq_scan(self):
        rec = _rule_missing_vacuum(
            self._row(100, 500),
            [{"Node Type": "Index Scan", "Relation Name": "orders"}],
        )
        assert rec is None

    def test_no_trigger_with_empty_nodes(self):
        rec = _rule_missing_vacuum(self._row(100, 500), [])
        assert rec is None


# ---------------------------------------------------------------------------
# Unit tests — _analyse
# ---------------------------------------------------------------------------


class TestAnalyse:
    def _row_triggering_all_stats(self) -> dict[str, Any]:
        return {
            "mean_exec_time_ms": 3000.0,   # rule 3: high
            "total_exec_time_ms": 300000.0,
            "stddev_exec_time_ms": 2000.0,  # rule 4: ratio 0.67 → medium
            "shared_blks_hit": 50,
            "shared_blks_read": 500,        # rule 5 (with plan): low
            "cache_hit_ratio": 60.0,        # rule 2: high
        }

    def test_sorted_high_before_medium_before_low(self):
        recs = _analyse(self._row_triggering_all_stats(), _FAKE_PLAN)
        severities = [r.severity for r in recs]
        order = [{"high": 0, "medium": 1, "low": 2}[s] for s in severities]
        assert order == sorted(order), f"Not sorted: {severities}"

    def test_plan_none_skips_rules_1_and_5(self):
        recs = _analyse(self._row_triggering_all_stats(), None)
        ids = {r.id for r in recs}
        assert "seq_scan_large_table" not in ids
        assert "missing_vacuum" not in ids
        # stat-based rules still run
        assert "low_cache_hit" in ids
        assert "high_mean_exec_time" in ids
        assert "high_stddev" in ids

    def test_empty_plan_triggers_stat_rules_only(self):
        recs = _analyse(self._row_triggering_all_stats(), [])
        ids = {r.id for r in recs}
        assert "seq_scan_large_table" not in ids
        assert "missing_vacuum" not in ids

    def test_all_rules_fire_with_full_data(self):
        recs = _analyse(self._row_triggering_all_stats(), _FAKE_PLAN)
        ids = {r.id for r in recs}
        assert "seq_scan_large_table" in ids
        assert "low_cache_hit" in ids
        assert "high_mean_exec_time" in ids
        assert "high_stddev" in ids
        assert "missing_vacuum" in ids


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------

_ENCODED_DSN = "postgresql%3A%2F%2Flocalhost%2Fdb"
_QUERYID = "42"


class TestGetFixEndpoint:
    async def test_connect_failure_returns_errors(self):
        with patch(
            "api.fix._step_connect",
            return_value=(None, "fail", {"message": "bad dsn"}),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/fix/{_ENCODED_DSN}/{_QUERYID}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["recommendations"] == []
        assert body["score"] is None
        assert any(e["step"] == "connect" for e in body["errors"])

    async def test_queryid_not_found_returns_errors(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_conn.close = AsyncMock()

        with patch("api.fix._step_connect", return_value=(mock_conn, "ok", {})):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/fix/{_ENCODED_DSN}/999999")

        assert resp.status_code == 200
        body = resp.json()
        assert body["recommendations"] == []
        assert body["score"] is None
        assert any(e["step"] == "fetch_query" for e in body["errors"])

    async def test_happy_path_returns_recommendations(self):
        conn = _mock_conn(_FAKE_ROW, _FAKE_PLAN)

        with patch("api.fix._step_connect", return_value=(conn, "ok", {})):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/fix/{_ENCODED_DSN}/{_QUERYID}")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["recommendations"]) > 0
        assert body["score"] is not None
        assert body["query_fingerprint"] is not None
        rec_ids = {r["id"] for r in body["recommendations"]}
        assert "seq_scan_large_table" in rec_ids

    async def test_explain_fail_still_returns_stat_recommendations(self):
        """If EXPLAIN fails, stat-based rules still fire."""
        conn = AsyncMock()

        class FakeRow(dict):
            pass

        conn.fetchrow = AsyncMock(return_value=FakeRow(_FAKE_ROW))
        # fetchval raises → EXPLAIN fails
        import asyncpg
        conn.fetchval = AsyncMock(side_effect=asyncpg.exceptions.InsufficientPrivilegeError())
        conn.close = AsyncMock()

        with patch("api.fix._step_connect", return_value=(conn, "ok", {})):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/fix/{_ENCODED_DSN}/{_QUERYID}")

        assert resp.status_code == 200
        body = resp.json()
        rec_ids = {r["id"] for r in body["recommendations"]}
        # Plan-based rules absent, but stat-based ones fire
        assert "seq_scan_large_table" not in rec_ids
        assert "low_cache_hit" in rec_ids
        # explain error recorded
        assert any(e["step"] == "explain" for e in body["errors"])
