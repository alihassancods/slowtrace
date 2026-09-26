"""AI-powered plain-English explanations for slow queries and their fixes.

Uses the DeepSeek API (OpenAI-compatible) via httpx.
Falls back to pre-written explanations when the API is unavailable.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# DeepSeek API config
# ---------------------------------------------------------------------------

_API_URL = "https://api.deepseek.com/chat/completions"
_DEFAULT_MODEL = "deepseek-v4-flash"
# The default model is a reasoning model: it spends part of the budget on hidden
# chain-of-thought before emitting the answer. A small cap (200) makes it return
# an empty `content` with finish_reason="length", which silently degraded every
# explanation to the canned fallback. Leave room for reasoning + the answer.
_MAX_TOKENS = 1024
_TIMEOUT = 30.0  # seconds


def _api_key() -> str | None:
    return os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")


def _model() -> str:
    return os.environ.get("DEEPSEEK_MODEL") or _DEFAULT_MODEL


# ---------------------------------------------------------------------------
# Fallback explanations (used when the API call fails)
# ---------------------------------------------------------------------------

_PROBLEM_FALLBACKS: dict[str, str] = {
    "missing_index": (
        "PostgreSQL is performing a sequential scan — reading every single row in the "
        "table to find the ones matching your WHERE clause. Without an index on the "
        "filter column, the database has no shortcut and must examine all rows, which "
        "gets exponentially slower as the table grows."
    ),
    "select_star": (
        "Your query fetches every column in the table, including ones your application "
        "never uses. This increases the amount of data transferred over the network and "
        "the number of disk pages PostgreSQL must read, adding unnecessary overhead on "
        "every single call."
    ),
    "n_plus_one": (
        "This query is being called an unusually high number of times — a classic "
        "N+1 pattern where the application fires one query per item in a list instead "
        "of fetching all items in a single batched query. The cumulative overhead of "
        "thousands of round-trips to the database dominates the total execution time."
    ),
    "missing_limit": (
        "The query returns a very large number of rows on every execution. Without a "
        "LIMIT clause, PostgreSQL must materialise the entire result set, consuming "
        "significant memory and I/O even when the application only needs the first "
        "few records."
    ),
    "seq_scan": (
        "PostgreSQL is scanning the entire table row-by-row (a Sequential Scan) rather "
        "than jumping directly to matching rows via an index. On a large table this "
        "reads far more data from disk than necessary, making the query slow even when "
        "only a handful of rows match the filter."
    ),
}

_FIX_FALLBACKS: dict[str, str] = {
    "missing_index": (
        "The new index lets PostgreSQL jump directly to rows that match the WHERE "
        "clause using a B-tree lookup instead of reading the whole table. This reduces "
        "the rows examined from potentially millions to only those that satisfy the "
        "predicate, cutting execution time by up to 100×."
    ),
    "select_star": (
        "By listing only the columns the application actually uses, PostgreSQL fetches "
        "fewer disk pages and sends less data across the network on every call. This "
        "reduces both I/O and memory pressure, typically speeding the query up by "
        "20–30%."
    ),
    "n_plus_one": (
        "Batching the queries into a single statement with an IN or JOIN clause "
        "eliminates thousands of database round-trips, replacing them with one network "
        "call and one query execution. This alone can reduce total latency by orders "
        "of magnitude."
    ),
    "missing_limit": (
        "Adding a LIMIT clause tells PostgreSQL it can stop scanning as soon as it "
        "has found enough rows, enabling early-exit optimisations in the query plan. "
        "This reduces both I/O and memory usage proportionally to how many rows you "
        "actually need."
    ),
    "seq_scan": (
        "Creating an index on the filter column allows PostgreSQL to switch from a "
        "Sequential Scan to an Index Scan, reading only the index pages and then "
        "fetching the handful of matching heap rows — instead of scanning every page "
        "of the table from start to finish."
    ),
}

_GENERIC_PROBLEM_FALLBACK = (
    "The query is slow because PostgreSQL cannot efficiently locate the matching rows "
    "and must do more work than necessary on every execution."
)

_GENERIC_FIX_FALLBACK = (
    "This fix restructures how PostgreSQL accesses the data, reducing the amount of "
    "work it must do per query execution."
)


# ---------------------------------------------------------------------------
# Internal HTTP helper
# ---------------------------------------------------------------------------


async def _chat(system: str, user: str) -> str | None:
    """Send a single chat completion request; return the text or None on error."""
    key = _api_key()
    if not key:
        return None

    payload = {
        "model": _model(),
        "max_tokens": _MAX_TOKENS,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(_API_URL, json=payload, headers=headers)
            response.raise_for_status()
            data: dict[str, Any] = response.json()
            message = data["choices"][0]["message"]
            # A reasoning model can return null/empty content when it runs out of
            # budget; treat that as "no answer" so the caller uses its fallback.
            content = (message.get("content") or "").strip()
            return content or None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# AIExplainer
# ---------------------------------------------------------------------------


class AIExplainer:
    """Generate plain-English explanations for slow queries and their fixes."""

    async def explain_problem(
        self,
        query: str,
        problem: dict[str, Any],
        query_stats: dict[str, Any],
    ) -> str:
        """Explain why *query* is slow in 2-3 plain-English sentences.

        Falls back to a pre-written explanation when the API is unavailable.

        Args:
            query:       Raw SQL query string.
            problem:     Dict returned by ``FixGenerator.detect_problem``.
            query_stats: Stats dict (``mean_exec_time_ms``, ``calls``, …).

        Returns:
            A plain-English string suitable for display to a developer.
        """
        problem_type: str = problem.get("type", "unknown")
        mean_exec_ms: float = float(query_stats.get("mean_exec_time_ms", 0))
        calls: int = int(query_stats.get("calls", 0))

        system = (
            "You are a database performance expert explaining to a developer "
            "why their query is slow. Be concise, specific, and avoid jargon. "
            "Reply in plain English only — no markdown, no bullet points."
        )
        user = (
            f"Query: {query}\n"
            f"Problem type: {problem_type}\n"
            f"Average execution time: {mean_exec_ms:.1f}ms\n"
            f"Called {calls} times per day\n\n"
            "In 2-3 sentences, explain why this query is slow in plain English. "
            "No jargon. Be specific about what PostgreSQL is doing wrong."
        )

        result = await _chat(system, user)
        if result:
            return result

        # Fallback
        return _PROBLEM_FALLBACKS.get(problem_type, _GENERIC_PROBLEM_FALLBACK)

    async def explain_fix(
        self,
        fix_sql: str,
        problem: dict[str, Any],
    ) -> str:
        """Explain in 2 sentences why *fix_sql* will make the query faster.

        Falls back to a pre-written explanation when the API is unavailable.

        Args:
            fix_sql: The SQL (or code guidance) returned by ``FixGenerator.generate_fix_sql``.
            problem: Dict returned by ``FixGenerator.detect_problem``.

        Returns:
            A plain-English string suitable for display alongside the fix SQL.
        """
        problem_type: str = problem.get("type", "unknown")

        system = (
            "You are a database performance expert. "
            "Reply in plain English only — no markdown, no bullet points."
        )
        user = (
            f"Explain in 2 sentences why this SQL fix will make the query faster. "
            f"Focus on what changes in how PostgreSQL finds the data.\n"
            f"Fix: {fix_sql}"
        )

        result = await _chat(system, user)
        if result:
            return result

        # Fallback
        return _FIX_FALLBACKS.get(problem_type, _GENERIC_FIX_FALLBACK)
