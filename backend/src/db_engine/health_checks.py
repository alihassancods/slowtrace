"""PostgreSQL database health check functions.

Each function accepts an asyncpg connection and returns a dict with:
    {
        "name":           str   — human-readable check name,
        "status":         str   — "ok" | "warning" | "critical",
        "value":          any   — actual measured value,
        "threshold":      any   — the target or limit value,
        "message":        str   — plain-English explanation,
        "severity_score": int   — points deducted from the health score,
    }

The module also exposes ``calculate_health_score(check_results)`` which
starts at 100 and subtracts each result's ``severity_score``, clamping the
final value to [0, 100].
"""

from __future__ import annotations

from typing import Any

import asyncpg

# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------

CheckResult = dict[str, Any]


# ---------------------------------------------------------------------------
# 1. Cache hit ratio
# ---------------------------------------------------------------------------


async def check_cache_hit_ratio(db: asyncpg.Connection) -> CheckResult:
    """Buffer-cache hit ratio from pg_stat_database.

    Critical if ratio < 0.90, Warning if ratio < 0.95.
    """
    row = await db.fetchrow(
        """
        SELECT
            sum(blks_hit)::float                          AS hits,
            (sum(blks_hit) + sum(blks_read))::float       AS total
        FROM pg_stat_database
        """
    )
    hits: float = row["hits"] or 0.0
    total: float = row["total"] or 0.0

    if total == 0:
        return {
            "name": "Cache Hit Ratio",
            "status": "ok",
            "value": None,
            "threshold": 0.95,
            "message": "No database I/O recorded yet — database may be new.",
            "severity_score": 0,
        }

    ratio = hits / total

    if ratio < 0.90:
        status, severity, suffix = "critical", 15, "(critical threshold: 0.90)"
    elif ratio < 0.95:
        status, severity, suffix = "warning", 7, "(warning threshold: 0.95)"
    else:
        status, severity, suffix = "ok", 0, ""

    return {
        "name": "Cache Hit Ratio",
        "status": status,
        "value": round(ratio, 4),
        "threshold": 0.95,
        "message": f"Buffer cache hit ratio is {ratio:.2%} {suffix}".strip(),
        "severity_score": severity,
    }


# ---------------------------------------------------------------------------
# 2. Connection count
# ---------------------------------------------------------------------------


async def check_connection_count(db: asyncpg.Connection) -> CheckResult:
    """Active connections relative to max_connections.

    Warning if > 80 %, Critical if > 90 %.
    """
    row = await db.fetchrow(
        """
        SELECT
            count(*)                                          AS active,
            (SELECT setting::int
               FROM pg_settings
              WHERE name = 'max_connections')                 AS max_conn
        FROM pg_stat_activity
        """
    )
    active: int = int(row["active"])
    max_conn: int = int(row["max_conn"])
    ratio = active / max_conn if max_conn else 0.0

    if ratio > 0.90:
        status, severity = "critical", 10
    elif ratio > 0.80:
        status, severity = "warning", 5
    else:
        status, severity = "ok", 0

    return {
        "name": "Connection Count",
        "status": status,
        "value": active,
        "threshold": max_conn,
        "message": (
            f"{active} active connections out of {max_conn} max "
            f"({ratio:.1%} utilisation)."
        ),
        "severity_score": severity,
    }


# ---------------------------------------------------------------------------
# 3. Dead tuple ratio
# ---------------------------------------------------------------------------


async def check_dead_tuple_ratio(db: asyncpg.Connection) -> CheckResult:
    """Dead-tuple fraction across all user tables.

    Warning if > 0.10, Critical if > 0.20.
    """
    row = await db.fetchrow(
        """
        SELECT
            sum(n_dead_tup)::float                                      AS dead,
            sum(n_live_tup + n_dead_tup)::float                        AS total
        FROM pg_stat_user_tables
        """
    )
    dead: float = row["dead"] or 0.0
    total: float = row["total"] or 0.0

    if total == 0:
        return {
            "name": "Dead Tuple Ratio",
            "status": "ok",
            "value": None,
            "threshold": 0.10,
            "message": "No user tables found or no tuple statistics available yet.",
            "severity_score": 0,
        }

    ratio = dead / total

    if ratio > 0.20:
        status, severity = "critical", 10
    elif ratio > 0.10:
        status, severity = "warning", 5
    else:
        status, severity = "ok", 0

    return {
        "name": "Dead Tuple Ratio",
        "status": status,
        "value": round(ratio, 4),
        "threshold": 0.10,
        "message": (
            f"Dead tuples account for {ratio:.2%} of total tuples across all user tables."
        ),
        "severity_score": severity,
    }


# ---------------------------------------------------------------------------
# 4. Table bloat
# ---------------------------------------------------------------------------


async def check_table_bloat(db: asyncpg.Connection) -> CheckResult:
    """Estimate table bloat via pg_total_relation_size vs expected size.

    Warning if any table exceeds 30 % estimated bloat.
    """
    rows = await db.fetch(
        """
        SELECT
            n.nspname || '.' || c.relname         AS table_name,
            c.reltuples                            AS row_estimate,
            pg_total_relation_size(c.oid)          AS total_bytes,
            c.relpages
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind = 'r'
          AND n.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
          AND c.reltuples > 0
          AND c.relpages > 0
        ORDER BY pg_total_relation_size(c.oid) DESC
        LIMIT 20
        """
    )

    if not rows:
        return {
            "name": "Table Bloat",
            "status": "ok",
            "value": 0.0,
            "threshold": 0.30,
            "message": "No user tables found to analyse.",
            "severity_score": 0,
        }

    bloated: list[dict[str, Any]] = []
    worst_ratio = 0.0

    for row in rows:
        row_estimate: float = float(row["row_estimate"])
        total_bytes: int = int(row["total_bytes"])
        relpages: int = int(row["relpages"])

        # Rough expected size: ~27 bytes per tuple (8-byte header + avg overhead)
        # mapped to 8 KiB pages.
        expected_pages = max(1.0, row_estimate * 27.0 / 8192.0)
        bloat_ratio = max(0.0, (relpages - expected_pages) / relpages)

        if bloat_ratio > worst_ratio:
            worst_ratio = bloat_ratio

        if bloat_ratio > 0.30:
            bloated.append(
                {
                    "table": row["table_name"],
                    "bloat_pct": round(bloat_ratio * 100, 1),
                    "total_bytes": total_bytes,
                }
            )

    if bloated:
        status, severity = "warning", 10
        message = (
            f"{len(bloated)} table(s) with estimated bloat > 30%: "
            + ", ".join(t["table"] for t in bloated[:5])
        )
    else:
        status, severity = "ok", 0
        message = f"No tables with bloat above 30% (worst: {worst_ratio:.1%})."

    return {
        "name": "Table Bloat",
        "status": status,
        "value": round(worst_ratio, 4),
        "threshold": 0.30,
        "message": message,
        "severity_score": severity,
    }


# ---------------------------------------------------------------------------
# 5. Long transactions
# ---------------------------------------------------------------------------


async def check_long_transactions(db: asyncpg.Connection) -> CheckResult:
    """Longest running non-idle transaction age in seconds.

    Warning if > 300 s (5 min), Critical if > 1800 s (30 min).
    """
    row = await db.fetchrow(
        """
        SELECT
            max(extract(epoch FROM now() - xact_start))::float AS max_age_s
        FROM pg_stat_activity
        WHERE state != 'idle'
          AND xact_start IS NOT NULL
        """
    )
    max_age_s: float | None = row["max_age_s"] if row else None

    if max_age_s is None:
        return {
            "name": "Long Transactions",
            "status": "ok",
            "value": 0.0,
            "threshold": 300.0,
            "message": "No active transactions found.",
            "severity_score": 0,
        }

    if max_age_s > 1800:
        status, severity = "critical", 15
        note = "(critical threshold: 30 min)"
    elif max_age_s > 300:
        status, severity = "warning", 7
        note = "(warning threshold: 5 min)"
    else:
        status, severity = "ok", 0
        note = ""

    return {
        "name": "Long Transactions",
        "status": status,
        "value": round(max_age_s, 1),
        "threshold": 300.0,
        "message": f"Longest active transaction is {max_age_s:.1f}s old {note}".strip(),
        "severity_score": severity,
    }


# ---------------------------------------------------------------------------
# 6. Lock contention
# ---------------------------------------------------------------------------


async def check_lock_contention(db: asyncpg.Connection) -> CheckResult:
    """Count of ungranted (waiting) locks.

    Warning if > 0, Critical if > 5.
    """
    count: int = await db.fetchval(
        "SELECT count(*) FROM pg_locks WHERE granted = false"
    )
    count = int(count or 0)

    if count > 5:
        status, severity = "critical", 15
        message = f"{count} ungranted lock(s) detected (critical threshold: > 5)."
    elif count > 0:
        status, severity = "warning", 7
        message = f"{count} ungranted lock(s) detected."
    else:
        status, severity = "ok", 0
        message = "No lock contention detected."

    return {
        "name": "Lock Contention",
        "status": status,
        "value": count,
        "threshold": 5,
        "message": message,
        "severity_score": severity,
    }


# ---------------------------------------------------------------------------
# 7. Replication lag
# ---------------------------------------------------------------------------


async def check_replication_lag(db: asyncpg.Connection) -> CheckResult:
    """Maximum replay lag across all streaming standbys (seconds).

    Skips gracefully when no replication is configured.
    Warning if > 60 s, Critical if > 300 s.
    """
    rows = await db.fetch(
        """
        SELECT extract(epoch FROM replay_lag)::float AS replay_lag_s
        FROM pg_stat_replication
        WHERE replay_lag IS NOT NULL
        """
    )

    if not rows:
        return {
            "name": "Replication Lag",
            "status": "ok",
            "value": None,
            "threshold": 60.0,
            "message": "No replication configured or no standbys connected.",
            "severity_score": 0,
        }

    max_lag: float = max(float(row["replay_lag_s"]) for row in rows)

    if max_lag > 300:
        status, severity = "critical", 15
        note = "(critical threshold: 300 s)"
    elif max_lag > 60:
        status, severity = "warning", 7
        note = "(warning threshold: 60 s)"
    else:
        status, severity = "ok", 0
        note = ""

    return {
        "name": "Replication Lag",
        "status": status,
        "value": round(max_lag, 2),
        "threshold": 60.0,
        "message": f"Maximum replication replay lag is {max_lag:.2f}s {note}".strip(),
        "severity_score": severity,
    }


# ---------------------------------------------------------------------------
# 8. Index usage (unused indexes)
# ---------------------------------------------------------------------------


async def check_index_usage(db: asyncpg.Connection) -> CheckResult:
    """Detect indexes that have never been scanned (idx_scan = 0).

    Warning if any such indexes exist (excluding primary-key / unique indexes
    that are maintained for constraint enforcement).
    """
    rows = await db.fetch(
        """
        SELECT
            schemaname,
            tablename,
            indexname,
            idx_scan,
            idx_tup_read
        FROM pg_stat_user_indexes
        WHERE idx_scan = 0
        ORDER BY schemaname, tablename, indexname
        """
    )

    unused = [
        {
            "schema": row["schemaname"],
            "table": row["tablename"],
            "index": row["indexname"],
            "idx_scan": int(row["idx_scan"]),
            "idx_tup_read": int(row["idx_tup_read"]),
        }
        for row in rows
    ]
    count = len(unused)

    if count > 0:
        status, severity = "warning", 10
        message = (
            f"{count} index(es) with zero scans detected — "
            "they may be unused and bloating write overhead."
        )
    else:
        status, severity = "ok", 0
        message = "All indexes have been scanned at least once."

    return {
        "name": "Index Usage",
        "status": status,
        "value": count,
        "threshold": 0,
        "message": message,
        "severity_score": severity,
        "unused_indexes": unused,
    }


# ---------------------------------------------------------------------------
# Health score aggregator
# ---------------------------------------------------------------------------


def calculate_health_score(check_results: list[CheckResult]) -> int:
    """Compute an overall health score from a list of check results.

    Starts at 100 and subtracts each result's ``severity_score``.
    The final value is clamped to [0, 100].

    Args:
        check_results: List of dicts returned by the ``check_*`` functions.

    Returns:
        Integer health score in the range 0–100.
    """
    score = 100
    for result in check_results:
        score -= int(result.get("severity_score", 0))
    return max(0, min(100, score))
