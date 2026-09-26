# Health API — `backend/src/api/health.py`

## Overview

[`backend/src/api/health.py`](../../backend/src/api/health.py) connects to a Postgres database, runs eight diagnostic checks, deducts points from a 100-point score for any degraded condition, assigns a letter grade, and returns the full result.

Two delivery modes are available: a streaming SSE endpoint for live progress and a one-shot JSON endpoint.

**Base prefix:** `/api/health`

---

## Routes

### `GET /api/health/{connection_id}/stream`

Streams each check result as a Server-Sent Event. The `connection_id` path segment is a URL-encoded DSN (e.g. `postgresql%3A%2F%2Fuser%3Apass%40host%2Fdb`).

**Response:** `text/event-stream`

Each event:
```json
{
  "step":   "<check_name>",
  "status": "ok" | "warning" | "fail",
  "data":   { … }
}
```

The final event has `"step": "result"` and `data` matching the `HealthReport` schema.

### `GET /api/health/{connection_id}`

One-shot endpoint. Drives the SSE stream internally and returns the final `HealthReport` as JSON.

---

## Scoring system

The database starts with **100 points**. Each failing check deducts a fixed number of points. Warnings deduct half the full deduction for that check.

| Check | Full deduction |
|-------|---------------|
| `connect` | 100 (immediate fail) |
| `connections` | 20 |
| `cache_hit_ratio` | 25 |
| `replication_lag` | 15 |
| `table_bloat` | 10 |
| `lock_contention` | 15 |
| `long_transactions` | 10 |
| `index_usage` | 15 |

Final score is clamped to `[0, 100]`.

### Grading

| Score range | Grade |
|-------------|-------|
| 90–100 | A |
| 75–89 | B |
| 60–74 | C |
| 40–59 | D |
| 0–39 | F |

---

## Pydantic models

### `CheckResult`

```python
class CheckResult(BaseModel):
    name: str
    status: str          # "ok" | "warning" | "fail"
    value: float | None
    unit: str | None
    threshold: float | None
    message: str
    deduction: int
```

One instance per check.

### `Deduction`

```python
class Deduction(BaseModel):
    check: str
    points: int
    reason: str
```

Records only checks that actually deducted points.

### `HealthError`

```python
class HealthError(BaseModel):
    check: str
    message: str
```

Records checks that raised unexpected exceptions.

### `HealthReport`

```python
class HealthReport(BaseModel):
    connection_id: str
    score: int | None        # None if connection failed
    grade: str | None        # None if connection failed
    checks: list[CheckResult]
    deductions: list[Deduction]
    errors: list[HealthError]
```

---

## Check implementations

All checks accept an `asyncpg.Connection` and return `CheckResult`.

### `_check_connect`

Attempts `asyncpg.connect(dsn=dsn, timeout=10)`. On failure the score becomes `None` and the stream emits the result immediately.

### `_check_connections`

```sql
SELECT count(*) FROM pg_stat_activity;
SELECT setting FROM pg_settings WHERE name = 'max_connections';
```

Deducts 10 pts at 70 % usage (warning), 20 pts at 90 % (fail).

### `_check_cache_hit_ratio`

```sql
SELECT sum(blks_hit) / (sum(blks_hit) + sum(blks_read)) FROM pg_stat_database
```

Threshold: < 95 % = warning (12 pts), < 80 % = fail (25 pts).

### `_check_replication_lag`

Queries `pg_stat_replication` for `write_lag` / `flush_lag` / `replay_lag`. Returns `ok` if no replicas are configured. Deducts 7 pts above 30 s (warning), 15 pts above 5 min (fail).

### `_check_table_bloat`

```sql
SELECT relname, n_dead_tup, n_live_tup,
       n_dead_tup::float / NULLIF(n_live_tup, 0) AS bloat_ratio
FROM pg_stat_user_tables
WHERE n_live_tup > 1000
ORDER BY bloat_ratio DESC NULLS LAST LIMIT 5
```

Reports the worst table. Deducts 5 pts at > 20 % bloat (warning), 10 pts at > 50 % (fail).

### `_check_lock_contention`

Counts rows in `pg_locks` where `NOT granted`. Deducts 7 pts for any blocked lock (warning), 15 pts for 5 or more (fail).

### `_check_long_transactions`

```sql
SELECT count(*) FROM pg_stat_activity
WHERE state != 'idle' AND now() - xact_start > interval '5 minutes'
```

Deducts 5 pts for 1–2 long transactions (warning), 10 pts for 3+ (fail).

### `_check_index_usage`

```sql
SELECT relname, seq_scan, idx_scan
FROM pg_stat_user_tables
WHERE seq_scan > idx_scan AND seq_scan > 100
```

Tables with more sequential scans than index scans likely lack appropriate indexes. Deducts 7 pts for 1–2 tables (warning), 15 pts for 3+ (fail).

---

## Timeout handling

Each check runs inside `_with_timeout(coro, seconds=10)`. If the coroutine times out, the check is recorded as `"fail"` with an appropriate message instead of crashing the stream.

---

## SSE generator — `_run_health_stream(dsn)`

An async generator that:
1. Emits one SSE `data:` line per check as it completes
2. Computes the final score and grade
3. Emits a `result` event carrying the full `HealthReport`
