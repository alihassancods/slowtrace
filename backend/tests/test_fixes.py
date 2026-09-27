"""Tests for the AI fix API: generate / save / history.

These exercise the full route with a mocked database connection so the
introspection-driven fix generation can be verified without a live Postgres.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from api.store import CONNECTIONS
from app.main import app


class _Row(dict):
    pass


def _mock_conn(
    query_row: dict | None,
    indexes: tuple[str, ...] = (),
    columns: tuple[str, ...] = (),
    row_estimate: int | None = None,
) -> AsyncMock:
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(
        return_value=_Row(query_row) if query_row is not None else None
    )
    # _introspect_table fetches indexes first, then columns.
    conn.fetch = AsyncMock(
        side_effect=[
            [_Row(indexname=i) for i in indexes],
            [_Row(column_name=c) for c in columns],
        ]
    )
    conn.fetchval = AsyncMock(return_value=row_estimate)
    conn.close = AsyncMock()
    return conn


_BASE_ROW: dict = {
    "queryid": "42",
    "query": "SELECT id FROM users WHERE email = $1",
    "calls": 100,
    "mean_exec_time_ms": 600.0,
    "total_exec_time_ms": 60000.0,
    "rows_per_call": 1.0,
}


async def _generate(conn: AsyncMock, query: str) -> "object":
    with (
        patch("api.fixes._connect", new=AsyncMock(return_value=conn)),
        patch(
            "api.fixes._ai_explainer.explain_problem",
            new=AsyncMock(return_value="because the index is missing"),
        ),
        patch.dict(CONNECTIONS, {"conn-1": "postgresql://localhost/db"}),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.post(
                "/api/fixes/generate",
                json={"connection_id": "conn-1", "queryid": "42"},
            )


# ---------------------------------------------------------------------------
# POST /api/fixes/generate
# ---------------------------------------------------------------------------


class TestGenerateFix:
    async def test_join_query_indexes_the_filter_table(self):
        query = (
            "SELECT o.id FROM orders o JOIN users u ON u.id = o.user_id "
            "WHERE u.email = $1"
        )
        conn = _mock_conn(
            {**_BASE_ROW, "query": query},
            indexes=(),
            columns=("id", "email"),
            row_estimate=5000,
        )

        resp = await _generate(conn, query)

        assert resp.status_code == 200
        body = resp.json()
        assert body["problem"]["type"] == "missing_index"
        assert body["problem"]["table"] == "users"  # not orders
        assert body["problem"]["columns"] == ["email"]
        assert "ON users(email)" in body["fix_sql"]
        assert body["ai_explanation"] == "because the index is missing"

    async def test_existing_index_avoids_duplicate_suggestion(self):
        query = "SELECT * FROM users WHERE email = $1"
        conn = _mock_conn(
            {**_BASE_ROW, "query": query},
            indexes=("idx_users_email",),
            columns=("id", "name", "email"),
            row_estimate=5000,
        )

        resp = await _generate(conn, query)

        body = resp.json()
        # Index already covers email → fall through to SELECT * with real columns.
        assert body["problem"]["type"] == "select_star"
        assert "id, name, email" in body["fix_sql"]

    async def test_quoted_orm_query(self):
        query = 'SELECT "o"."id" FROM "public"."orders" "o" WHERE "o"."customer_id" = $1'
        conn = _mock_conn({**_BASE_ROW, "query": query}, columns=("id", "customer_id"))

        resp = await _generate(conn, query)

        body = resp.json()
        assert body["problem"]["table"] == "orders"
        assert "ON orders(customer_id)" in body["fix_sql"]

    async def test_query_not_found_returns_404(self):
        conn = _mock_conn(None)
        resp = await _generate(conn, "SELECT 1")
        assert resp.status_code == 404

    async def test_unknown_connection_returns_404(self):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/fixes/generate",
                json={"connection_id": "nope", "queryid": "42"},
            )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# HypoPG validation on POST /api/fixes/generate
# ---------------------------------------------------------------------------


def _validation(**overrides) -> dict:
    base = {
        "validated": True,
        "reason": None,
        "baseline_cost": 71832.0,
        "improved_cost": 6221.0,
        "cost_reduction_percent": 91.3,
        "baseline_scan_type": "Seq Scan",
        "improved_scan_type": "Index Scan",
        "planner_would_use_index": True,
        "trials_run": 3,
        "trials_using_index": 3,
        "cost_reduction_min": 88.9,
        "cost_reduction_max": 91.3,
        "sample_values": {"$1": "'user_7@example.com'"},
        "estimated_size_bytes": 2_516_582,
        "proof_statement": "PostgreSQL planner confirmed: 91.3% reduction",
    }
    base.update(overrides)
    return base


class TestGenerateHypoPG:
    async def _generate_validated(self, validation) -> dict:
        conn = _mock_conn(_BASE_ROW, columns=("id", "email"), row_estimate=50_000)
        with patch(
            "api.fixes._hypopg_validator.validate_index_fix",
            new=AsyncMock(return_value=validation),
        ):
            resp = await _generate(conn, _BASE_ROW["query"])
        assert resp.status_code == 200
        return resp.json()

    async def test_index_fix_carries_the_planner_proof(self):
        body = await self._generate_validated(_validation())

        assert body["hypopg_validation"]["validated"] is True
        assert body["hypopg_validation"]["planner_would_use_index"] is True
        assert body["hypopg_validation"]["baseline_cost"] == 71832.0
        assert body["hypopg_validation"]["improved_scan_type"] == "Index Scan"
        # Trial agreement and the stand-in value must survive to the client,
        # which is what makes the claim auditable rather than magical.
        assert body["hypopg_validation"]["trials_using_index"] == 3
        assert body["hypopg_validation"]["cost_reduction_min"] == 88.9
        assert body["hypopg_validation"]["sample_values"]["$1"] == (
            "'user_7@example.com'"
        )

    async def test_proven_fix_replaces_the_guessed_speedup(self):
        body = await self._generate_validated(_validation())

        impact = body["expected_impact"]
        assert impact["estimation_basis"] == "hypopg_planner"
        # 71832 / 6221 ≈ 11.5×, not the 100× the heuristic claims.
        assert impact["speedup_factor"] == pytest.approx(11.55, abs=0.02)
        assert impact["after_ms"] == pytest.approx(600.0 / 11.546, abs=0.2)

    async def test_speedup_is_capped_at_a_plausible_ceiling(self):
        body = await self._generate_validated(
            _validation(baseline_cost=1_000_000.0, improved_cost=1.0)
        )

        assert body["expected_impact"]["speedup_factor"] == 50.0

    async def test_unproven_fix_keeps_the_heuristic(self):
        body = await self._generate_validated(
            _validation(
                validated=False,
                reason="hypopg_unavailable",
                planner_would_use_index=None,
            )
        )

        impact = body["expected_impact"]
        assert impact["estimation_basis"] == "heuristic"
        assert impact["after_ms"] == 6.0  # 600 ms / 100
        assert body["hypopg_validation"]["reason"] == "hypopg_unavailable"

    async def test_planner_rejecting_the_index_keeps_the_heuristic(self):
        body = await self._generate_validated(
            _validation(planner_would_use_index=False, cost_reduction_percent=0.4)
        )

        assert body["expected_impact"]["estimation_basis"] == "heuristic"
        assert body["expected_impact"]["after_ms"] == 6.0

    async def test_improved_cost_higher_than_baseline_keeps_the_heuristic(self):
        body = await self._generate_validated(
            _validation(improved_cost=80000.0, cost_reduction_percent=-11.4)
        )

        assert body["expected_impact"]["estimation_basis"] == "heuristic"
        assert body["expected_impact"]["after_ms"] == 6.0

    async def test_non_index_problem_skips_validation(self):
        conn = _mock_conn(
            {**_BASE_ROW, "query": "SELECT * FROM users WHERE id = $1"},
            indexes=("idx_users_id",),
            columns=("id", "email"),
        )
        validator = AsyncMock(return_value=_validation())
        with patch("api.fixes._hypopg_validator.validate_index_fix", new=validator):
            resp = await _generate(conn, "SELECT * FROM users WHERE id = $1")

        body = resp.json()
        assert body["problem"]["type"] == "select_star"
        assert body["hypopg_validation"] is None
        validator.assert_not_called()

    async def test_missing_hypopg_does_not_fail_generation(self):
        """A DB without the extension still returns a usable suggestion."""
        conn = _mock_conn(_BASE_ROW, columns=("id", "email"), row_estimate=50_000)

        resp = await _generate(conn, _BASE_ROW["query"])

        body = resp.json()
        assert body["problem"]["type"] == "missing_index"
        assert body["hypopg_validation"]["validated"] is False
        assert body["hypopg_validation"]["reason"] == "hypopg_unavailable"
        assert "EXPLAIN" not in body["fix_sql"]


# ---------------------------------------------------------------------------
# POST /api/fixes/save + GET /api/fixes/history/{connection_id}
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_history(tmp_path):
    import agent.autofix.fix_executor as fe

    original = fe._HISTORY_PATH
    fe._HISTORY_PATH = tmp_path / "fix_history.json"
    yield fe._HISTORY_PATH
    fe._HISTORY_PATH = original


class TestSaveFix:
    async def test_save_persists_metadata_and_impact(self, tmp_history):
        import agent.autofix.fix_executor as fe

        with patch.dict(CONNECTIONS, {"conn-1": "postgresql://localhost/db"}):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/fixes/save",
                    json={
                        "connection_id": "conn-1",
                        "queryid": "42",
                        "fix_sql": "CREATE INDEX CONCURRENTLY i ON t(c);",
                        "rollback_sql": "DROP INDEX CONCURRENTLY i;",
                        "query_time_before_ms": 600.0,
                        "query_time_after_ms": 6.0,
                        "time_saved_per_day_minutes": 1.0,
                    },
                )

        assert resp.status_code == 200
        fix_id = resp.json()["fix_id"]
        record = next(r for r in fe._load_history() if r["fix_id"] == fix_id)
        assert record["status"] == "pending"
        assert record["connection_id"] == "conn-1"
        assert record["queryid"] == "42"
        assert record["query_time_before_ms"] == 600.0
        assert record["query_time_after_ms"] == 6.0

    async def test_unknown_connection_returns_404(self, tmp_history):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/fixes/save",
                json={
                    "connection_id": "nope",
                    "queryid": "42",
                    "fix_sql": "SELECT 1",
                    "rollback_sql": "",
                },
            )
        assert resp.status_code == 404

    async def test_history_is_scoped_to_connection(self, tmp_history):
        import agent.autofix.fix_executor as fe

        fe._upsert_record({"fix_id": "a", "connection_id": "conn-1"})
        fe._upsert_record({"fix_id": "b", "connection_id": "other"})

        with patch.dict(CONNECTIONS, {"conn-1": "postgresql://localhost/db"}):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/fixes/history/conn-1")

        assert resp.status_code == 200
        assert [r["fix_id"] for r in resp.json()] == ["a"]
