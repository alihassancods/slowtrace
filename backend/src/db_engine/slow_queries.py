"""Slow query detection using pg_stat_statements."""

from __future__ import annotations

from typing import Any

from src.db_engine.connector import DBConnector

# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------

SlowQuery = dict[str, Any]

# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

_SLOW_QUERY_SQL = """
SELECT
  queryid,
  query,
  calls,
  mean_exec_time,
  total_exec_time,
  ROUND((total_exec_time / 1000 / 60)::numeric, 2)
    AS minutes_wasted,
  stddev_exec_time,
  rows,
  ROUND((rows::numeric / NULLIF(calls, 0)), 1)
    AS avg_rows_returned,
  shared_blks_hit,
  shared_blks_read,
  ROUND(
    shared_blks_hit::numeric /
    NULLIF(shared_blks_hit + shared_blks_read, 0) * 100, 1
  ) AS cache_hit_ratio
FROM pg_stat_statements
WHERE
  query NOT LIKE '%pg_stat%'
  AND query NOT LIKE '%information_schema%'
  AND mean_exec_time > 100
  AND calls > 10
ORDER BY total_exec_time DESC
LIMIT $1
"""

# ---------------------------------------------------------------------------
# Main query function
# ---------------------------------------------------------------------------


async def get_slow_queries(db: DBConnector, limit: int = 10) -> list[SlowQuery]:
    """Return the top slow queries from pg_stat_statements.

    Args:
        db:    An open :class:`DBConnector` instance.
        limit: Maximum number of rows to return (default 10).

    Returns:
        List of dicts with keys: queryid, query, calls, mean_exec_time,
        total_exec_time, minutes_wasted, stddev_exec_time, rows,
        avg_rows_returned, shared_blks_hit, shared_blks_read,
        cache_hit_ratio.
    """
    rows = await db.fetch(_SLOW_QUERY_SQL, limit)
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def calculate_total_wasted(queries: list[SlowQuery]) -> float:
    """Sum minutes_wasted across all queries.

    Args:
        queries: List of dicts as returned by :func:`get_slow_queries`.

    Returns:
        Total minutes wasted as a float.
    """
    return sum(float(q.get("minutes_wasted") or 0) for q in queries)


def humanize_time_wasted(minutes: float) -> str:
    """Convert a minute count into an emotional description.

    A standup meeting is assumed to be 3 minutes.

    Args:
        minutes: Total wasted compute time in minutes.

    Returns:
        A plain-English string, e.g.
        "87 minutes — that's 29 standup meetings worth of wasted compute"
    """
    standup_minutes = 3
    standups = int(minutes / standup_minutes)
    rounded = round(minutes)
    return (
        f"{rounded} minutes — that's {standups} standup meetings worth "
        "of wasted compute"
    )


def classify_severity(mean_exec_time_ms: float) -> str:
    """Classify a query by its mean execution time.

    Args:
        mean_exec_time_ms: Mean execution time in milliseconds.

    Returns:
        ``"critical"`` if > 2000 ms,
        ``"warning"`` if > 500 ms,
        ``"slow"`` if > 100 ms.
    """
    if mean_exec_time_ms > 2000:
        return "critical"
    if mean_exec_time_ms > 500:
        return "warning"
    return "slow"
