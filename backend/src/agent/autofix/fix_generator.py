"""AI fix generator — rule-based problem detection and fix SQL generation."""

from __future__ import annotations

import re
from typing import Any


class FixGenerator:
    """Detect query problems and generate fix SQL with rollback and impact estimates."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect_problem(self, query: str, query_stats: dict[str, Any]) -> dict[str, Any]:
        """Detect the most significant problem in *query* given its runtime stats.

        Patterns are evaluated in priority order; the first match is returned.
        Returns an empty dict ``{}`` when no problem is detected.

        Args:
            query:       Raw SQL query string (from pg_stat_statements).
            query_stats: Dict with keys such as ``calls``, ``mean_exec_time_ms``,
                         ``avg_rows_returned``, ``table_row_counts`` (optional).

        Returns:
            A problem dict with at minimum ``{"type": str, ...}`` or ``{}``.
        """
        calls_per_day: float = float(query_stats.get("calls", 0))
        mean_exec_ms: float = float(query_stats.get("mean_exec_time_ms", 0))
        avg_rows: float = float(query_stats.get("avg_rows_returned", 0))
        table_row_counts: dict[str, int] = query_stats.get("table_row_counts", {})
        existing_indexes: list[str] = query_stats.get("existing_indexes", [])

        # The table a statement reads/writes, and the table that owns the WHERE
        # columns. These differ for joins: `... FROM orders o JOIN users u
        # WHERE u.email = $1` must index users(email), not orders(email).
        from_table = _extract_target_table(query)
        filter_table, where_cols = _resolve_filter_table(query)

        # Pattern 1 — Missing index
        if (
            filter_table
            and where_cols
            and not _has_index(filter_table, where_cols, existing_indexes)
        ):
            return {
                "type": "missing_index",
                "table": filter_table,
                "columns": where_cols,
            }

        # Pattern 2 — SELECT *
        if re.search(r"SELECT\s+\*", query, re.IGNORECASE):
            return {
                "type": "select_star",
                "table": from_table or filter_table,
            }

        # Pattern 3 — N+1 query
        if calls_per_day > 50_000:
            return {
                "type": "n_plus_one",
                "calls_per_day": calls_per_day,
            }

        # Pattern 4 — Missing LIMIT
        has_limit = re.search(r"\bLIMIT\b", query, re.IGNORECASE) is not None
        has_count = re.search(r"\bCOUNT\s*\(", query, re.IGNORECASE) is not None
        if not has_limit and not has_count and avg_rows > 1000:
            return {
                "type": "missing_limit",
                "avg_rows_returned": avg_rows,
            }

        # Pattern 5 — Sequential scan on large table
        if from_table:
            row_count: int = table_row_counts.get(from_table, 0)
            if row_count > 10_000:
                return {
                    "type": "seq_scan",
                    "table": from_table,
                    "table_row_count": row_count,
                    "columns": where_cols,
                }

        return {}

    def target_table(self, query: str) -> str | None:
        """Return the table most likely to benefit from an index for *query*.

        Prefers the table owning the WHERE columns; falls back to the table the
        statement reads from or writes to. Used to introspect the live database.
        """
        table, _ = _resolve_filter_table(query)
        return table or _extract_target_table(query)

    def generate_fix_sql(
        self, problem: dict[str, Any], schema: dict[str, Any]
    ) -> dict[str, Any]:
        """Return fix SQL, rollback SQL, and execution metadata for *problem*.

        Args:
            problem: Dict returned by :meth:`detect_problem`.
            schema:  Optional schema metadata (e.g. actual column lists from the DB).

        Returns:
            Dict with keys: ``fix_sql``, ``rollback_sql``, ``risk_level``,
            ``is_blocking``, ``estimated_duration``.
        """
        ptype = problem.get("type", "")

        if ptype in ("missing_index", "seq_scan"):
            table = problem.get("table", "<table>")
            columns: list[str] = problem.get("columns", [])
            col_str = ", ".join(columns) if columns else "<column>"
            col_slug = "_".join(columns) if columns else "col"
            index_name = f"idx_{table}_{col_slug}"

            return {
                "fix_sql": (
                    f"CREATE INDEX CONCURRENTLY {index_name}\n"
                    f"  ON {table}({col_str});"
                ),
                "rollback_sql": f"DROP INDEX CONCURRENTLY {index_name};",
                "risk_level": "low",
                "is_blocking": False,
                "estimated_duration": "30-60 seconds",
            }

        if ptype == "select_star":
            table = problem.get("table") or "<table>"
            # Use schema-provided columns when available, otherwise guide the user.
            actual_cols: list[str] = schema.get("columns", {}).get(table, [])
            if actual_cols:
                col_list = ", ".join(actual_cols)
                fix_sql = (
                    f"-- Replace SELECT * with only the columns you need:\n"
                    f"-- SELECT {col_list}\n"
                    f"-- FROM {table} ..."
                )
            else:
                fix_sql = (
                    "-- Replace SELECT * with an explicit column list.\n"
                    "-- Inspect the table with:\n"
                    f"SELECT column_name FROM information_schema.columns\n"
                    f"WHERE table_name = '{table}'\n"
                    "ORDER BY ordinal_position;"
                )
            return {
                "fix_sql": fix_sql,
                "rollback_sql": "-- No rollback needed; this is a code change.",
                "risk_level": "low",
                "is_blocking": False,
                "estimated_duration": "< 1 second",
            }

        if ptype == "n_plus_one":
            return {
                "fix_sql": (
                    "-- N+1 pattern detected: this query is called too frequently.\n"
                    "-- Consolidate into a single batched query using IN or JOIN,\n"
                    "-- or add an application-level cache.\n"
                    "-- Example batch rewrite:\n"
                    "-- SELECT * FROM <table> WHERE id = ANY($1::int[]);"
                ),
                "rollback_sql": "-- No rollback needed; this is a code change.",
                "risk_level": "low",
                "is_blocking": False,
                "estimated_duration": "< 1 second",
            }

        if ptype == "missing_limit":
            return {
                "fix_sql": (
                    "-- Add LIMIT to your query in application code.\n"
                    "-- Example:\n"
                    "-- SELECT ... FROM <table> WHERE ... LIMIT 100;"
                ),
                "rollback_sql": "-- No rollback needed; this is a code change.",
                "risk_level": "low",
                "is_blocking": False,
                "estimated_duration": "< 1 second",
            }

        # Unknown / no problem
        return {
            "fix_sql": "-- No fix available for this problem type.",
            "rollback_sql": "",
            "risk_level": "low",
            "is_blocking": False,
            "estimated_duration": "< 1 second",
        }

    def calculate_expected_impact(
        self, problem: dict[str, Any], query_stats: dict[str, Any]
    ) -> dict[str, Any]:
        """Estimate performance improvement after applying the fix.

        Args:
            problem:     Dict returned by :meth:`detect_problem`.
            query_stats: Same stats dict passed to :meth:`detect_problem`.

        Returns:
            Dict with keys: ``before_ms``, ``after_ms``, ``speedup_factor``,
            ``time_saved_per_day_minutes``.
        """
        before_ms: float = float(query_stats.get("mean_exec_time_ms", 0))
        calls_per_day: float = float(query_stats.get("calls", 1))
        ptype = problem.get("type", "")

        if ptype in ("missing_index", "seq_scan"):
            after_ms = before_ms / 100
        elif ptype == "select_star":
            after_ms = before_ms * 0.7
        elif ptype == "missing_limit":
            after_ms = before_ms / 10
        else:
            after_ms = before_ms

        speedup_factor = before_ms / after_ms if after_ms > 0 else 1.0
        saved_ms_per_call = before_ms - after_ms
        time_saved_per_day_minutes = (saved_ms_per_call * calls_per_day) / 1000 / 60

        return {
            "before_ms": before_ms,
            "after_ms": round(after_ms, 3),
            "speedup_factor": round(speedup_factor, 2),
            "time_saved_per_day_minutes": round(time_saved_per_day_minutes, 2),
        }


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _normalise(query: str) -> str:
    """Strip identifier quoting so `"orders"."customer_id"` parses like ``orders.customer_id``.

    PostgreSQL string literals use single quotes, so removing double quotes and
    backticks cannot change a literal's contents.
    """
    return query.replace('"', "").replace("`", "")


def _extract_from_table(query: str) -> str | None:
    """Return the first table name from the FROM clause."""
    match = re.search(r"\bFROM\s+(?:ONLY\s+)?([\w.]+)", _normalise(query), re.IGNORECASE)
    if match:
        # Strip schema prefix (e.g. "public.users" → "users")
        return match.group(1).split(".")[-1]
    return None


def _extract_write_table(query: str) -> str | None:
    """Return the target table of an UPDATE or INSERT statement."""
    normalised = _normalise(query)
    match = re.search(
        r"\bUPDATE\s+(?:ONLY\s+)?([\w.]+)", normalised, re.IGNORECASE
    ) or re.search(r"\bINSERT\s+INTO\s+([\w.]+)", normalised, re.IGNORECASE)
    if match:
        return match.group(1).split(".")[-1]
    return None


def _extract_target_table(query: str) -> str | None:
    """Return the table the statement reads from or writes to."""
    return _extract_from_table(query) or _extract_write_table(query)


def _extract_where_columns(query: str) -> list[str]:
    """Return column names referenced in the WHERE clause.

    Only handles simple ``col = ...`` / ``col > ...`` / ``col IN ...`` patterns.
    """
    where_match = re.search(r"\bWHERE\b(.+?)(?:\bORDER\b|\bGROUP\b|\bLIMIT\b|\bHAVING\b|$)", _normalise(query), re.IGNORECASE | re.DOTALL)
    if not where_match:
        return []
    where_clause = where_match.group(1)
    # Match bare column names (no table prefix) before comparison operators
    cols = re.findall(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:=|>|<|>=|<=|!=|<>|\bIN\b|\bLIKE\b|\bIS\b)", where_clause, re.IGNORECASE)
    # Remove SQL keywords that may be captured
    keywords = {"AND", "OR", "NOT", "NULL", "TRUE", "FALSE", "IS", "IN", "LIKE", "BETWEEN"}
    return [c for c in dict.fromkeys(cols) if c.upper() not in keywords]


def _extract_where_conditions(query: str) -> list[tuple[str | None, str]]:
    """Return ``(qualifier, column)`` pairs for WHERE-clause comparisons.

    The qualifier is the table name or alias in ``u.email = $1``; it is ``None``
    for an unqualified ``email = $1``. Quoted identifiers are handled.
    """
    where_match = re.search(
        r"\bWHERE\b(.+?)(?:\bORDER\b|\bGROUP\b|\bLIMIT\b|\bHAVING\b|$)",
        _normalise(query),
        re.IGNORECASE | re.DOTALL,
    )
    if not where_match:
        return []
    clause = where_match.group(1)
    found = re.findall(
        r"(?:([a-zA-Z_]\w*)\.)?([a-zA-Z_]\w*)\s*"
        r"(?:=|>|<|>=|<=|!=|<>|\bIN\b|\bLIKE\b|\bIS\b)",
        clause,
        re.IGNORECASE,
    )
    keywords = {"AND", "OR", "NOT", "NULL", "TRUE", "FALSE", "IS", "IN", "LIKE", "BETWEEN"}
    conditions: list[tuple[str | None, str]] = []
    seen: set[tuple[str | None, str]] = set()
    for qualifier, column in found:
        if column.upper() in keywords:
            continue
        key = (qualifier.lower() if qualifier else None, column.lower())
        if key in seen:
            continue
        seen.add(key)
        conditions.append((qualifier or None, column))
    return conditions


def _table_alias_map(query: str) -> dict[str, str]:
    """Map lowercased aliases (and table names) to their table name.

    ``FROM orders o JOIN users u`` → ``{"orders": "orders", "o": "orders",
    "users": "users", "u": "users"}``.
    """
    normalised = _normalise(query)
    mapping: dict[str, str] = {}
    keywords = {
        "ON", "WHERE", "GROUP", "ORDER", "LIMIT", "HAVING", "INNER", "LEFT",
        "RIGHT", "FULL", "OUTER", "CROSS", "JOIN", "USING", "UNION", "AS",
        "SELECT", "SET", "VALUES", "RETURNING", "FOR", "OFFSET",
    }
    pattern = re.compile(
        r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w.]*)(?:\s+(?:AS\s+)?([A-Za-z_]\w*))?",
        re.IGNORECASE,
    )
    for match in pattern.finditer(normalised):
        table = match.group(1).split(".")[-1]
        mapping.setdefault(table.lower(), table)
        alias = match.group(2)
        if alias and alias.upper() not in keywords:
            mapping.setdefault(alias.lower(), table)
    return mapping


def _resolve_filter_table(query: str) -> tuple[str | None, list[str]]:
    """Return the table that owns the WHERE columns, plus those columns.

    Resolves alias-qualified columns so a join filters the right table, e.g.
    ``... FROM orders o JOIN users u WHERE u.email = $1`` → ``("users", ["email"])``.
    Unqualified columns fall back to the statement's primary table.
    """
    conditions = _extract_where_conditions(query)
    if not conditions:
        return None, []

    aliases = _table_alias_map(query)
    default_table = _extract_target_table(query)

    groups: dict[str, list[str]] = {}
    order: list[str] = []
    for qualifier, column in conditions:
        if qualifier:
            table = aliases.get(qualifier.lower())
            if not table:
                continue
        else:
            table = default_table
            if not table:
                continue
        if table not in groups:
            groups[table] = []
            order.append(table)
        groups[table].append(column)

    if not groups:
        return None, []

    best = max(order, key=lambda t: len(groups[t]))
    return best, groups[best]


def _has_index(table: str, columns: list[str], existing_indexes: list[str]) -> bool:
    """Return True if any of the existing index names suggests coverage.

    ``existing_indexes`` is expected to be a list of index name strings such as
    ``["idx_users_email", "idx_orders_created_at"]``.
    """
    if not columns:
        return False
    table_lower = table.lower()
    for idx in existing_indexes:
        idx_lower = idx.lower()
        if table_lower in idx_lower and any(c.lower() in idx_lower for c in columns):
            return True
    return False
