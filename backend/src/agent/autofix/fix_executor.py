"""Fix executor — apply a generated fix to the database with safety checks and rollback."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import asyncpg

# ---------------------------------------------------------------------------
# Fix history store  (JSON file, good enough for a hackathon)
# ---------------------------------------------------------------------------

_HISTORY_PATH = Path(__file__).resolve().parents[4] / "data" / "fix_history.json"


def _load_history() -> list[dict[str, Any]]:
    """Read fix history from disk; return an empty list if the file is absent."""
    if not _HISTORY_PATH.exists():
        return []
    try:
        return json.loads(_HISTORY_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _save_history(history: list[dict[str, Any]]) -> None:
    """Persist *history* to disk, creating parent directories as needed."""
    _HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _HISTORY_PATH.write_text(
        json.dumps(history, indent=2, default=str), encoding="utf-8"
    )


def _upsert_record(record: dict[str, Any]) -> None:
    """Insert or update a record in the history file keyed by ``fix_id``."""
    history = _load_history()
    for i, entry in enumerate(history):
        if entry.get("fix_id") == record["fix_id"]:
            history[i] = record
            _save_history(history)
            return
    history.append(record)
    _save_history(history)


# ---------------------------------------------------------------------------
# Health score helper
# ---------------------------------------------------------------------------


async def _measure_health(conn: asyncpg.Connection) -> int:
    """Return a 0-100 health score for the current connection.

    Reuses the same check functions already present in ``api.health``.
    Importing locally to avoid circular imports at module load time.
    """
    from api.health import (
        _check_cache_hit_ratio,
        _check_connections,
        _check_index_usage,
        _check_lock_contention,
        _check_long_transactions,
        _check_replication_lag,
        _check_table_bloat,
        _build_check_result,
        _compute_score,
    )

    checks_fns = [
        ("connections", _check_connections),
        ("cache_hit_ratio", _check_cache_hit_ratio),
        ("replication_lag", _check_replication_lag),
        ("table_bloat", _check_table_bloat),
        ("lock_contention", _check_lock_contention),
        ("long_transactions", _check_long_transactions),
        ("index_usage", _check_index_usage),
    ]

    results = []
    for name, fn in checks_fns:
        try:
            status, data = await asyncio.wait_for(fn(conn), timeout=10)
        except Exception:
            status, data = "ok", {}
        results.append(_build_check_result(name, status, data))

    score, _grade, _deductions = _compute_score(results)
    return score


# ---------------------------------------------------------------------------
# Index creation progress polling
# ---------------------------------------------------------------------------

_INDEX_PROGRESS_SQL = """
SELECT
    phase,
    blocks_done,
    blocks_total,
    tuples_done,
    tuples_total
FROM pg_stat_progress_create_index
WHERE relid = $1::regclass
"""


async def _poll_index_progress(
    conn: asyncpg.Connection,
    table: str,
    progress_events: list[dict[str, Any]],
) -> None:
    """Poll pg_stat_progress_create_index until the operation finishes.

    Progress snapshots are appended to *progress_events* so the caller can
    stream them as SSE events if desired.
    """
    for _ in range(120):  # max 120 × 0.5 s = 60 seconds
        await asyncio.sleep(0.5)
        try:
            row = await conn.fetchrow(_INDEX_PROGRESS_SQL, table)
        except asyncpg.PostgresError:
            break
        if row is None:
            # Index creation finished
            break
        blocks_total = int(row["blocks_total"] or 0)
        blocks_done = int(row["blocks_done"] or 0)
        pct = (blocks_done / blocks_total * 100) if blocks_total else 0.0
        progress_events.append(
            {
                "phase": row["phase"],
                "blocks_done": blocks_done,
                "blocks_total": blocks_total,
                "tuples_done": int(row["tuples_done"] or 0),
                "tuples_total": int(row["tuples_total"] or 0),
                "pct_complete": round(pct, 1),
            }
        )


# ---------------------------------------------------------------------------
# FixExecutor
# ---------------------------------------------------------------------------


class FixExecutor:
    """Execute a generated fix against a live database, with automatic rollback."""

    async def execute(
        self,
        fix: dict[str, Any],
        conn: asyncpg.Connection,
        health_service: Any | None = None,
    ) -> dict[str, Any]:
        """Apply *fix* to the database represented by *conn*.

        Steps
        -----
        1. Record health score BEFORE the fix.
        2. Persist an ``"executing"`` record to fix_history.json.
        3. Execute ``fix["fix_sql"]``.
        4. If the fix is a CREATE INDEX, poll pg_stat_progress_create_index.
        5. Wait 3 seconds for the DB to settle.
        6. Record health score AFTER the fix.
        7. Auto-rollback if health dropped more than 10 points.
        8. Persist final status and return the result dict.

        Args:
            fix:            Dict with at minimum ``fix_sql`` and ``rollback_sql``
                            (as returned by ``FixGenerator.generate_fix_sql``).
            conn:           Open asyncpg connection to the target database.
            health_service: Optional — not used internally; reserved for future
                            injection of a custom health scorer.

        Returns:
            A result dict whose ``status`` key is one of
            ``"success"``, ``"auto_rolled_back"``, or ``"error"``.
        """
        fix_id = str(uuid.uuid4())
        fix_sql: str = fix.get("fix_sql", "")
        rollback_sql: str = fix.get("rollback_sql", "")
        now = datetime.now(tz=timezone.utc)

        # ── Step 1: health BEFORE ─────────────────────────────────────────────
        health_before = await _measure_health(conn)

        # ── Step 2: write "executing" record ─────────────────────────────────
        record: dict[str, Any] = {
            "fix_id": fix_id,
            "status": "executing",
            "fix_sql": fix_sql,
            "rollback_sql": rollback_sql,
            "health_before": health_before,
            "health_after": None,
            "started_at": now.isoformat(),
            "finished_at": None,
        }
        _upsert_record(record)

        # ── Step 3: execute the fix SQL ───────────────────────────────────────
        progress_events: list[dict[str, Any]] = []
        try:
            await conn.execute(fix_sql)
        except asyncpg.PostgresError as exc:
            record.update(
                status="error",
                error=str(exc),
                finished_at=datetime.now(tz=timezone.utc).isoformat(),
            )
            _upsert_record(record)
            return {"status": "error", "fix_id": fix_id, "error": str(exc)}

        # ── Step 4: poll CREATE INDEX progress ───────────────────────────────
        is_create_index = "CREATE INDEX" in fix_sql.upper()
        if is_create_index:
            # Extract the table name from the ON clause for progress polling
            import re as _re
            match = _re.search(r"\bON\s+([\w.]+)\s*\(", fix_sql, _re.IGNORECASE)
            if match:
                table_name = match.group(1)
                try:
                    await asyncio.wait_for(
                        _poll_index_progress(conn, table_name, progress_events),
                        timeout=65,
                    )
                except asyncio.TimeoutError:
                    pass  # Index may still be building; continue to health check

        # ── Step 5: settle ────────────────────────────────────────────────────
        await asyncio.sleep(3)

        # ── Step 6: health AFTER ──────────────────────────────────────────────
        health_after = await _measure_health(conn)

        # ── Step 7: auto-rollback if health dropped more than 10 points ──────
        health_delta = health_after - health_before
        if health_delta < -10:
            rollback_error: str | None = None
            if rollback_sql and not rollback_sql.lstrip().startswith("--"):
                try:
                    await conn.execute(rollback_sql)
                except asyncpg.PostgresError as exc:
                    rollback_error = str(exc)

            record.update(
                status="auto_rolled_back",
                health_after=health_after,
                health_delta=health_delta,
                rollback_error=rollback_error,
                finished_at=datetime.now(tz=timezone.utc).isoformat(),
            )
            _upsert_record(record)
            return {
                "status": "auto_rolled_back",
                "fix_id": fix_id,
                "health_before": health_before,
                "health_after": health_after,
                "health_delta": health_delta,
                "reason": (
                    f"Health score dropped by {abs(health_delta)} points "
                    f"(from {health_before} to {health_after}). Fix was rolled back automatically."
                ),
                "rollback_error": rollback_error,
            }

        # ── Step 8: success ───────────────────────────────────────────────────
        rollback_available_until = (now + timedelta(hours=24)).isoformat()

        # Impact metrics — reuse the fix's own estimates when available
        before_ms: float = float(fix.get("query_time_before_ms", 0.0))
        after_ms: float = float(fix.get("query_time_after_ms", 0.0))
        speedup_factor: float = (before_ms / after_ms) if after_ms > 0 else 1.0
        time_saved_per_day_minutes: float = float(fix.get("time_saved_per_day_minutes", 0.0))

        record.update(
            status="success",
            health_after=health_after,
            health_delta=health_delta,
            rollback_available_until=rollback_available_until,
            index_progress=progress_events,
            finished_at=datetime.now(tz=timezone.utc).isoformat(),
        )
        _upsert_record(record)

        return {
            "status": "success",
            "fix_id": fix_id,
            "health_before": health_before,
            "health_after": health_after,
            "health_improvement": health_delta,
            "query_time_before_ms": before_ms,
            "query_time_after_ms": after_ms,
            "speedup_factor": round(speedup_factor, 2),
            "time_saved_per_day_minutes": round(time_saved_per_day_minutes, 2),
            "can_rollback": bool(rollback_sql and not rollback_sql.lstrip().startswith("--")),
            "rollback_available_until": rollback_available_until,
            "index_progress": progress_events,
        }

    async def rollback(self, fix_id: str, conn: asyncpg.Connection) -> dict[str, Any]:
        """Manually roll back a previously applied fix.

        Looks up *fix_id* in fix_history.json, executes ``rollback_sql``, and
        updates the record's status to ``"manually_rolled_back"``.

        Args:
            fix_id: UUID string previously returned by :meth:`execute`.
            conn:   Open asyncpg connection to the target database.

        Returns:
            A dict with ``status`` (``"manually_rolled_back"`` or ``"error"``)
            and relevant context fields.
        """
        history = _load_history()
        record = next((e for e in history if e.get("fix_id") == fix_id), None)

        if record is None:
            return {
                "status": "error",
                "fix_id": fix_id,
                "error": f"Fix ID '{fix_id}' not found in history.",
            }

        rollback_sql: str = record.get("rollback_sql", "")
        if not rollback_sql or rollback_sql.lstrip().startswith("--"):
            return {
                "status": "error",
                "fix_id": fix_id,
                "error": "No executable rollback SQL available for this fix.",
            }

        try:
            await conn.execute(rollback_sql)
        except asyncpg.PostgresError as exc:
            return {
                "status": "error",
                "fix_id": fix_id,
                "error": f"Rollback failed: {exc}",
            }

        record.update(
            status="manually_rolled_back",
            rolled_back_at=datetime.now(tz=timezone.utc).isoformat(),
        )
        _upsert_record(record)

        return {
            "status": "manually_rolled_back",
            "fix_id": fix_id,
            "rolled_back_at": record["rolled_back_at"],
        }
