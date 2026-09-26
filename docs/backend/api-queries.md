# Queries API — `backend/src/api/queries.py`

## Overview

[`backend/src/api/queries.py`](../../backend/src/api/queries.py) implements slow-query analysis. It reads up to 200 rows from `pg_stat_statements`, normalises each query into a fingerprint, and computes a **badness score** in `[0, 100]`.

Two delivery modes:
- **SSE stream** for live progress feedback (used by the Scan page)
- **One-shot JSON** that consumes the stream internally

**Base prefix:** `/api/queries`

---

## Routes

### `GET /api/queries/{connection_id}/stream`

Streams each analysis step as an SSE event.

**Response:** `text/event-stream`

Events in order:
```
connect → check_extension → check_permissions → fetch_queries → result
```

### `GET /api/queries/{connection_id}`

One-shot endpoint. Drives the stream internally and returns the final `QueryReport`.

---

## Pydantic models

### `SlowQuery`

```python
class SlowQuery(BaseModel):
    queryid: str
    query: str
    query_fingerprint: str
    calls: int
    mean_exec_time_ms: float
    total_exec_time_ms: float
    stddev_exec_time_ms: float
    rows_per_call: float
    shared_blks_hit: int
    shared_blks_read: int
    cache_hit_ratio: float
    score: float
```

`query_fingerprint` is the normalised form of `query` (no literals, lowercase). `score` is the badness score — higher means higher priority to fix.

### `ScanError`

```python
class ScanError(BaseModel):
    step: str
    message: str
```

### `QueryReport`

```python
class QueryReport(BaseModel):
    connection_id: str
    total_queries: int
    queries: list[SlowQuery]
    errors: list[ScanError]
```

`queries` is sorted **descending by score**. `errors` accumulates non-fatal warnings (e.g. extension missing) in addition to fatal failures.

---

## Query fingerprinting — `_fingerprint(query)`

Normalises a SQL string to a canonical, literal-free form used for grouping and display:

1. Strip `/* block comments */`
2. Replace `'string literals'` with `?`
3. Replace numeric literals (integers and decimals) with `?`
4. Collapse all whitespace to a single space
5. Strip leading/trailing whitespace
6. Lowercase the result

Example:
```
SELECT * FROM orders WHERE id = 99 AND name = 'bob'
→ select * from orders where id = ? and name = ?
```

---

## Scoring algorithm — `_score_query(row, max_total_ms)`

Returns a float in `[0, 100]`. Four independent signals are summed:

| Signal | Max pts | Formula |
|--------|---------|---------|
| Mean execution time | 40 | `min(mean_ms / 1000, 1.0) × 40` — saturates at 1 s |
| Total execution time share | 30 | `(total_ms / max_total_ms) × 30` |
| Cache miss rate | 20 | `(1 - cache_hit / 100) × 20` |
| Execution time variability | 10 | `min(stddev_ms / mean_ms, 1.0) × 10` |

`max_total_ms` is the highest `total_exec_time_ms` across **all fetched rows** in the same scan, which normalises the total-time signal relative to the busiest query.

---

## SQL query

```sql
SELECT
    queryid::text,
    query,
    calls,
    mean_exec_time                       AS mean_exec_time_ms,
    total_exec_time                      AS total_exec_time_ms,
    stddev_exec_time                     AS stddev_exec_time_ms,
    rows / NULLIF(calls, 0)              AS rows_per_call,
    shared_blks_hit,
    shared_blks_read,
    CASE
        WHEN (shared_blks_hit + shared_blks_read) = 0 THEN 100.0
        ELSE shared_blks_hit::float * 100.0
             / (shared_blks_hit + shared_blks_read)
    END                                  AS cache_hit_ratio
FROM pg_stat_statements
WHERE calls > 0
ORDER BY total_exec_time DESC
LIMIT 200
```

The `LIMIT 200` caps memory use. The 30-second `asyncio.wait_for` timeout prevents long-running pg_stat_statements scans from blocking indefinitely.

---

## Step implementations

### `_step_connect(dsn)`

Opens a raw connection with a 5-second timeout. Returns `(conn, status, data)`.

Handles: `TimeoutError`, `InvalidPasswordError`, `InvalidCatalogNameError`, `OSError`, `PostgresError`.

### `_step_check_extension(conn)`

Checks `pg_extension` for `pg_stat_statements`. Returns `"ok"` / `"warning"` (with fix hint) / `"fail"`.

### `_step_check_permissions(conn)`

Attempts `SELECT * FROM pg_stat_statements LIMIT 1`. Returns `"ok"` / `"warning"` / `"fail"`.

### `_step_fetch_queries(conn)`

Runs the SQL above with a 30-second timeout. Fingerprints and scores every row, sorts descending by score, and returns `(status, data, queries)`.

---

## SSE generator — `_run_query_stream(dsn)`

Yields formatted `data: <json>\n\n` lines for each step. A `result` event is always the last event emitted, even on failure. This contract is relied upon by the one-shot JSON endpoint and the dashboard aggregator.
