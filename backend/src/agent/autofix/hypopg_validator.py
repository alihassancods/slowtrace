"""HypoPG what-if validation for index fixes.

Creates a *hypothetical* index in the current session, lets the PostgreSQL
planner cost the slow query with and without it, then drops it again. Nothing
is written to disk and nothing is visible to other sessions, so a fix
suggestion can be backed by real planner numbers instead of a guessed ratio.

Research basis: HypoPG (https://github.com/HypoPG/hypopg) — pg_stat_statements
query text + hypopg_create_index + EXPLAIN before/after.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Iterator

# Per-statement guard: a validation step must never stall the generate request.
_TIMEOUT_SECONDS = 10.0

_EXT_SQL = "SELECT extname FROM pg_extension WHERE extname = 'hypopg'"
_CREATE_EXT_SQL = "CREATE EXTENSION IF NOT EXISTS hypopg"
# Explicit casts keep Postgres from reporting an ambiguous signature for the
# bound parameter.
_CREATE_INDEX_SQL = "SELECT * FROM hypopg_create_index($1::text)"
_DROP_INDEX_SQL = "SELECT hypopg_drop_index($1::oid)"
_RESET_SQL = "SELECT hypopg_reset()"

# Function name used by the research write-up vs. the one HypoPG actually ships.
_SIZE_FUNCTIONS = ("hypopg_relation_size", "hypopg_get_relation_size")

_INDEX_SCANS = frozenset({"Index Scan", "Index Only Scan", "Bitmap Index Scan"})
_TABLE_SCANS = frozenset(_INDEX_SCANS | {"Seq Scan", "Bitmap Heap Scan"})

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# A parameter's meaning depends on the column it is compared with, so look
# backwards from each ``$n`` for ``[qualifier.]column <op>``.
_PARAM_LOOKBACK = re.compile(
    r"(?:(?P<qualifier>[A-Za-z_]\w*)\.)?(?P<column>[A-Za-z_]\w*)\s*"
    r"(?P<op>=|<>|!=|>=|<=|>|<|\bIN\b\s*\(?|\bLIKE\b|\bILIKE\b|"
    r"\bIS\b(?:\s+NOT)?(?:\s+DISTINCT\s+FROM)?)\s*$",
    re.IGNORECASE,
)

# `>` and `<` need a *typical* value: picking an arbitrary one swings the
# verdict, since `col > 10` and `col > 4990` justify completely different plans.
_RANGE_OPS = frozenset({">", "<", ">=", "<="})

# How many rows to pull for the frequency/median estimates. Bounded by the
# LIMIT and by the page sample, not by the size of the table.
_SAMPLE_ROWS = 200

# How long a single sample lookup may take before we give up on that parameter.
_SAMPLE_TIMEOUT_SECONDS = 2.0

# Number of stand-in values a fix is proved against. One value can flatter an
# index by accident (a sampled high key makes `col > ?` look selective), so the
# verdict is taken across several.
_DEFAULT_TRIALS = 3


class HypoPGValidator:
    """Prove an index fix with the planner before applying it."""

    def __init__(self, trials: int = _DEFAULT_TRIALS) -> None:
        """Args:
            trials: stand-in values to prove the fix against. 1 keeps the
                original single-shot behaviour.
        """
        self._trials = max(1, int(trials))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def is_available(self, conn: Any) -> bool:
        """Return True if HypoPG is (or can be made) usable on this session."""
        return await self._ensure_extension(conn)

    async def validate_index_fix(
        self,
        conn: Any,
        *,
        table: str | None,
        columns: list[str],
        slow_query: str,
        index_ddl: str | None = None,
    ) -> dict[str, Any]:
        """Cost *slow_query* with and without a hypothetical index on *table*.

        Args:
            conn:        Open ``asyncpg.Connection`` (hypo indexes are session-local).
            table:       Table the suggested index belongs to.
            columns:     Indexed columns, in order.
            slow_query:  Raw statement text, typically from pg_stat_statements.
            index_ddl:   Optional real fix SQL to mirror (CONCURRENTLY is dropped).

        Returns:
            Validation dict — always present, never raises. ``validated`` is
            False with a ``reason`` when the extension or EXPLAIN is unusable.
        """
        clean_columns = [c for c in (columns or []) if c and "<" not in c]
        if not table or not clean_columns:
            return self._result(reason="no_index_target")

        if not await self._ensure_extension(conn):
            return self._result(reason="hypopg_unavailable")

        # pg_stat_statements stores `$1`-style parameters, so EXPLAIN needs
        # stand-in values before it can cost anything real.
        candidates = await self._resolve_parameters(conn, slow_query, table)

        trials, failure = await self._run_trials(
            conn, table, clean_columns, slow_query, index_ddl, candidates
        )
        if failure is not None:
            reason, detail = failure
            return self._result(reason=reason, detail=detail)
        if not trials:
            return self._result(reason="explain_failed")

        return self._aggregate(table, clean_columns, candidates, trials)

    # ------------------------------------------------------------------
    # Extension handling
    # ------------------------------------------------------------------

    async def _ensure_extension(self, conn: Any) -> bool:
        """Detect HypoPG, installing it best-effort when the DB allows."""
        try:
            if await self._extension_present(conn):
                return True
            await asyncio.wait_for(
                conn.execute(_CREATE_EXT_SQL), timeout=_TIMEOUT_SECONDS
            )
            return await self._extension_present(conn)
        except Exception:
            return False

    async def _extension_present(self, conn: Any) -> bool:
        value = await asyncio.wait_for(
            conn.fetchval(_EXT_SQL), timeout=_TIMEOUT_SECONDS
        )
        return isinstance(value, str) and value.lower() == "hypopg"

    # ------------------------------------------------------------------
    # Hypothetical index lifecycle
    # ------------------------------------------------------------------

    async def _create_hypo_index(
        self,
        conn: Any,
        table: str,
        columns: list[str],
        index_ddl: str | None,
    ) -> tuple[tuple[Any, str] | None, str | None]:
        """Register a hypo index.

        Returns ``(None, error)`` when HypoPG refuses — most often because the
        extension is installed but missing from ``shared_preload_libraries``.
        """
        ddl = self._build_hypo_ddl(table, columns, index_ddl)
        try:
            row = await asyncio.wait_for(
                conn.fetchrow(_CREATE_INDEX_SQL, ddl), timeout=_TIMEOUT_SECONDS
            )
        except Exception as exc:
            return None, str(exc)
        if row is None:
            return None, "hypopg_create_index() returned no row"

        index_oid = self._field(row, "indexrelid", "indexoid")
        if index_oid is None:
            return None, "hypopg_create_index() returned no index OID"
        name = self._field(row, "indexname")
        return (index_oid, str(name) if name is not None else ""), None

    async def _drop_hypo_index(self, conn: Any, index_oid: Any) -> None:
        try:
            await asyncio.wait_for(
                conn.execute(_DROP_INDEX_SQL, index_oid), timeout=_TIMEOUT_SECONDS
            )
        except Exception:
            try:
                await asyncio.wait_for(
                    conn.execute(_RESET_SQL), timeout=_TIMEOUT_SECONDS
                )
            except Exception:
                pass

    async def _hypo_size(self, conn: Any, index_oid: Any) -> int | None:
        """Estimated size of the real index, in bytes (None if unsupported)."""
        for function in _SIZE_FUNCTIONS:
            try:
                value = await asyncio.wait_for(
                    conn.fetchval(f"SELECT {function}($1::oid)", index_oid),
                    timeout=_TIMEOUT_SECONDS,
                )
            except Exception:
                continue
            if value is not None:
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return None
        return None

    def _build_hypo_ddl(
        self, table: str, columns: list[str], index_ddl: str | None
    ) -> str:
        """HypoPG needs a plain CREATE INDEX — no CONCURRENTLY, no comments."""
        if index_ddl:
            cleaned = re.sub(r"/\*.*?\*/", "", index_ddl, flags=re.DOTALL)
            cleaned = re.sub(r"\bCONCURRENTLY\b", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(";").strip()
            if cleaned.upper().startswith("CREATE INDEX") and "<" not in cleaned:
                return cleaned

        col_slug = "_".join(columns)
        return f"CREATE INDEX idx_{table}_{col_slug} ON {table}({', '.join(columns)})"

    # ------------------------------------------------------------------
    # EXPLAIN
    # ------------------------------------------------------------------

    async def _explain(
        self, conn: Any, query: str, params: dict[str, str]
    ) -> list[Any] | None:
        """``EXPLAIN (FORMAT JSON)`` — estimated plan only, never executed."""
        prepared = self._prepare_for_explain(query, params)
        if not prepared:
            return None
        try:
            raw = await asyncio.wait_for(
                conn.fetchval(f"EXPLAIN (FORMAT JSON) {prepared}"),
                timeout=_TIMEOUT_SECONDS,
            )
        except Exception:
            return None

        if raw is None:
            return None
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (ValueError, TypeError):
                return None
        if isinstance(raw, dict):
            raw = [raw]
        if not isinstance(raw, list) or not raw:
            return None
        return raw

    def _prepare_for_explain(self, query: str, params: dict[str, str]) -> str:
        """Make pg_stat_statements text EXPLAIN-able.

        Strips comments and swaps every ``$n`` for its stand-in value. NULL is
        deliberately *not* a valid stand-in: the planner proves ``col = NULL``
        can never match and returns a 0-cost ``Result`` node that never touches
        the table, which would make every index look worthless.
        """
        if not query:
            return ""
        stripped = re.sub(r"/\*.*?\*/", "", query, flags=re.DOTALL)
        stripped = re.sub(r"--[^\n]*", "", stripped)

        def replace(match: re.Match[str]) -> str:
            return params.get(match.group(0), "1")

        stripped = re.sub(r"\$\d+", replace, stripped)
        return stripped.strip().rstrip(";").strip()

    # ------------------------------------------------------------------
    # Parameter stand-in values
    # ------------------------------------------------------------------

    async def _resolve_parameters(
        self, conn: Any, query: str, table: str | None
    ) -> dict[str, list[str]]:
        """Map each ``$n`` to stand-in literals the planner can cost realistically.

        A parameter compared against a column gets *real sampled values* for
        that column, so row estimates carry actual selectivity. Anything else
        (LIMIT, OFFSET, unknown context) falls back to ``1``.
        """
        text = re.sub(r"/\*.*?\*/", "", query, flags=re.DOTALL)
        text = re.sub(r"--[^\n]*", "", text)

        candidates: dict[str, list[str]] = {}
        cache: dict[tuple[str, str, bool], list[str]] = {}
        for match in re.finditer(r"\$\d+", text):
            token = match.group(0)
            if token in candidates:
                continue
            found = _PARAM_LOOKBACK.search(text[: match.start()].rstrip())
            if not found:
                candidates[token] = ["1"]
                continue

            column = found.group("column")
            qualifier = found.group("qualifier")
            operator = (found.group("op") or "").strip().upper()
            sample_table = table
            if qualifier and _IDENT_RE.match(qualifier):
                # `u.email = $1` — only trust the qualifier when it is a plain
                # identifier; otherwise stay on the indexed table.
                sample_table = self._resolve_alias(text, qualifier) or table
            values: list[str] = []
            if sample_table and _IDENT_RE.match(column):
                values = await self._representative_values(
                    conn,
                    sample_table,
                    column,
                    operator in _RANGE_OPS,
                    cache,
                )
            # An unquoted value would type-mismatch half the column types, so
            # the fallback stays an unknown-type literal the column can coerce.
            candidates[token] = values or ["'1'"]
        return candidates

    @staticmethod
    def _resolve_alias(query: str, alias: str) -> str | None:
        """Best-effort alias → table name, reusing the generator's parser."""
        try:
            from agent.autofix.fix_generator import _table_alias_map

            return _table_alias_map(query).get(alias.lower())
        except Exception:
            return None

    async def _representative_values(
        self,
        conn: Any,
        table: str,
        column: str,
        is_range: bool,
        cache: dict[tuple[str, str, bool], list[str]],
    ) -> list[str]:
        """Stand-in literals whose selectivity is typical, not lucky.

        Equality gets the *most frequent* sampled values: if an index still
        pays for the worst-case key, the claim is safe. Range comparisons get
        the *median* key, because `col > 10` and `col > 4990` justify entirely
        different plans and an arbitrary pick makes the verdict flicker.
        """
        key = (table, column, is_range)
        if key in cache:
            return cache[key]

        # Page sampling can legitimately return nothing on a small or recently
        # vacuumed table, so the same query is retried as a plain scan.
        for sampled in (True, False):
            values = await self._sample_once(conn, table, column, is_range, sampled)
            if values:
                break

        cache[key] = values
        return values

    async def _sample_once(
        self, conn: Any, table: str, column: str, is_range: bool, sampled: bool
    ) -> list[str]:
        inner = self._sampled_subquery(table, column, sampled)
        if is_range:
            sql = (
                f"WITH s AS ({inner}) SELECT quote_literal(col) FROM s "
                f"ORDER BY col OFFSET (SELECT count(*) / 2 FROM s) LIMIT 1"
            )
        else:
            sql = (
                f"SELECT quote_literal(col) FROM ({inner}) s "
                f"GROUP BY col ORDER BY count(*) DESC, col LIMIT {self._trials}"
            )
        return await self._run_sample(conn, sql)

    @staticmethod
    def _sampled_subquery(table: str, column: str, sampled: bool = True) -> str:
        pages = " TABLESAMPLE SYSTEM (1)" if sampled else ""
        return (
            f'SELECT "{column}" AS col FROM "{table}"{pages} '
            f'WHERE "{column}" IS NOT NULL LIMIT {_SAMPLE_ROWS}'
        )

    async def _run_sample(self, conn: Any, sql: str) -> list[str]:
        """Run one sampling query, retrying without page sampling if empty."""
        try:
            rows = await asyncio.wait_for(
                conn.fetch(sql), timeout=_SAMPLE_TIMEOUT_SECONDS
            )
        except Exception:
            return []
        return [value for r in rows if (value := self._as_literal(r[0]))]

    @staticmethod
    def _as_literal(value: Any) -> str | None:
        """Normalise a driver value into a single-quoted SQL literal."""
        if value is None:
            return None
        if isinstance(value, str) and value.startswith("'") and value.endswith("'"):
            return value  # already produced by quote_literal()
        text = str(value)
        if not text:
            return None
        return "'" + text.replace("'", "''") + "'"

    # ------------------------------------------------------------------
    # Trial execution
    # ------------------------------------------------------------------

    async def _run_trials(
        self,
        conn: Any,
        table: str,
        columns: list[str],
        query: str,
        index_ddl: str | None,
        candidates: dict[str, list[str]],
    ) -> tuple[list[dict[str, Any]], tuple[str, str | None] | None]:
        """Cost the query with and without the hypo index, once per stand-in set.

        Returns ``(trials, failure)``; ``failure`` is ``(reason, detail)`` when
        HypoPG itself refused to cooperate.
        """
        trials: list[dict[str, Any]] = []
        size: int | None = None

        # No point re-running a trial with a value already used.
        attempts = max(
            1,
            min(self._trials, min((len(v) for v in candidates.values()), default=1)),
        )

        for attempt in range(attempts):
            params = {
                token: self._pick(values, attempt)
                for token, values in candidates.items()
            }
            baseline = self._summarize(await self._explain(conn, query, params))
            if baseline is None:
                continue

            created, error = await self._create_hypo_index(
                conn, table, columns, index_ddl
            )
            if created is None:
                return [], ("hypopg_create_failed", error)
            index_oid, index_name = created

            try:
                improved = self._summarize(await self._explain(conn, query, params))
                if improved is None:
                    continue
                if size is None:
                    size = await self._hypo_size(conn, index_oid)
                trials.append(
                    {
                        "params": params,
                        "baseline": baseline,
                        "improved": improved,
                        "reduction": self._cost_reduction(
                            baseline["total_cost"], improved["total_cost"]
                        ),
                        "used": self._planner_uses_index(
                            baseline, improved, index_name, table
                        ),
                        "before_scan": self._scan_label(baseline, table),
                        "after_scan": self._scan_label(improved, table),
                    }
                )
            finally:
                # Hypothetical indexes are session-local, but a pooled or reused
                # connection must not inherit them.
                await self._drop_hypo_index(conn, index_oid)

        for trial in trials:
            trial["size"] = size
        return trials, None

    @staticmethod
    def _pick(values: list[str], attempt: int) -> str:
        if not values:
            return "1"
        return values[attempt] if attempt < len(values) else values[-1]

    def _aggregate(
        self,
        table: str,
        columns: list[str],
        candidates: dict[str, list[str]],
        trials: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Fold the trials into one verdict: median trial + unanimous agreement.

        Unanimity is deliberate. A majority was measured to be fooled by a
        boolean column, where the two runner-up values both looked selective
        enough to justify an index the real planner then refused to use.
        """
        ordered = sorted(trials, key=lambda t: t["reduction"])
        median_trial = ordered[len(ordered) // 2]
        used_count = sum(1 for t in trials if t["used"])
        would_use = used_count == len(trials)

        return self._result(
            validated=True,
            baseline_cost=median_trial["baseline"]["total_cost"],
            improved_cost=median_trial["improved"]["total_cost"],
            cost_reduction_percent=median_trial["reduction"],
            cost_reduction_min=ordered[0]["reduction"],
            cost_reduction_max=ordered[-1]["reduction"],
            baseline_scan_type=median_trial["before_scan"],
            improved_scan_type=median_trial["after_scan"],
            planner_would_use_index=would_use,
            trials_run=len(trials),
            trials_using_index=used_count,
            sample_values=dict(median_trial["params"]),
            estimated_size_bytes=median_trial.get("size"),
            proof_statement=self._generate_proof(
                median_trial["reduction"],
                would_use,
                median_trial["before_scan"],
                median_trial["after_scan"],
                len(trials),
                used_count,
                ordered[0]["reduction"],
                ordered[-1]["reduction"],
            ),
        )

    # ------------------------------------------------------------------
    # Plan analysis
    # ------------------------------------------------------------------

    def _summarize(self, plan: list[Any] | None) -> dict[str, Any] | None:
        """Flatten a JSON plan into the facts needed for the before/after diff."""
        if not plan:
            return None
        top = plan[0].get("Plan") if isinstance(plan[0], dict) else None
        if not isinstance(top, dict):
            return None

        nodes = list(self._walk(top))
        return {
            "total_cost": float(top.get("Total Cost") or 0.0),
            "startup_cost": float(top.get("Startup Cost") or 0.0),
            "plan_rows": float(top.get("Plan Rows") or 0.0),
            "node_type": str(top.get("Node Type") or ""),
            "scan_types": [
                str(n.get("Node Type"))
                for n in nodes
                if n.get("Node Type") in _TABLE_SCANS
            ],
            "index_names": {
                str(n.get("Index Name"))
                for n in nodes
                if n.get("Index Name") is not None
            },
            "nodes": nodes,
        }

    @staticmethod
    def _walk(node: dict[str, Any]) -> Iterator[dict[str, Any]]:
        yield node
        for child in node.get("Plans") or []:
            if isinstance(child, dict):
                yield from HypoPGValidator._walk(child)

    def _planner_uses_index(
        self,
        baseline: dict[str, Any],
        improved: dict[str, Any],
        index_name: str,
        table: str,
    ) -> bool:
        """True when the new plan actually reaches for the hypothetical index."""
        if index_name and index_name in improved["index_names"]:
            return True
        # A Bitmap Heap Scan is only cheaper because of its Bitmap Index Scan
        # child, so check the whole tree rather than just the labelled scan.
        if self._has_index_scan(improved) and not self._has_index_scan(baseline):
            return True
        # Older/newer HypoPG releases report the synthetic name differently, so
        # fall back to the scan type on the indexed relation.
        before = self._scan_label(baseline, table)
        after = self._scan_label(improved, table)
        return after in _INDEX_SCANS and before not in _INDEX_SCANS

    @staticmethod
    def _has_index_scan(summary: dict[str, Any]) -> bool:
        return any(node.get("Node Type") in _INDEX_SCANS for node in summary["nodes"])

    def _scan_label(self, summary: dict[str, Any], table: str | None) -> str:
        """The scan a reader cares about: on *table*, else the root node."""
        for node in summary["nodes"]:
            if node.get("Node Type") in _TABLE_SCANS and self._matches_table(
                node.get("Relation Name"), table
            ):
                return str(node["Node Type"])
        return summary["node_type"]

    @staticmethod
    def _matches_table(relation: Any, table: str | None) -> bool:
        if not relation or not table:
            return False
        return str(relation).split(".")[-1].lower() == table.lower()

    @staticmethod
    def _cost_reduction(baseline_cost: float, improved_cost: float) -> float:
        if baseline_cost <= 0:
            return 0.0
        return round((baseline_cost - improved_cost) / baseline_cost * 100, 1)

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def _generate_proof(
        self,
        reduction: float,
        would_use: bool,
        before_scan: str,
        after_scan: str,
        trials: int,
        used_count: int,
        min_reduction: float,
        max_reduction: float,
    ) -> str:
        spread = (
            f" (range {min_reduction:.1f}%-{max_reduction:.1f}% across "
            f"{trials} sampled values)"
            if trials > 1
            else ""
        )
        if not would_use:
            return (
                f"Index created hypothetically, but the planner reached for it "
                f"in only {used_count} of {trials} sampled values"
                f"{spread}. Impact may be limited."
            )
        return (
            f"PostgreSQL planner confirmed: index changes the scan from "
            f"'{before_scan}' to '{after_scan}' for all {trials} sampled "
            f"values{spread}. Median cost reduction {reduction:.1f}% — "
            "measured before any changes were made to your database."
        )

    @staticmethod
    def _field(row: Any, *names: str) -> Any:
        """Read the first present column from an asyncpg Record / dict / tuple."""
        for name in names:
            try:
                value = row[name]
            except (KeyError, IndexError, TypeError):
                continue
            if value is not None:
                return value
        # Positional fallback: (indexoid, indexname).
        if isinstance(row, (tuple, list)) and row:
            return row[0]
        return None

    @staticmethod
    def _result(**overrides: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "validated": False,
            "reason": None,
            # Database's own words when a step failed — diagnostic only.
            "detail": None,
            "baseline_cost": None,
            "improved_cost": None,
            "cost_reduction_percent": None,
            "baseline_scan_type": None,
            "improved_scan_type": None,
            "planner_would_use_index": None,
            "trials_run": 0,
            "trials_using_index": None,
            "cost_reduction_min": None,
            "cost_reduction_max": None,
            "sample_values": None,
            "estimated_size_bytes": None,
            "proof_statement": None,
        }
        base.update(overrides)
        return base
