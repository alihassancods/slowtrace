# Explain API — `backend/src/api/explain.py`

## Overview

[`backend/src/api/explain.py`](../../backend/src/api/explain.py) returns detailed statistics and an `EXPLAIN` plan for a **single query**, identified by its `queryid` from `pg_stat_statements`.

**Base prefix:** `/api/explain`

---

## Route

### `GET /api/explain/{connection_id}/{queryid}`

Returns stats and the `EXPLAIN (ANALYZE false, BUFFERS, FORMAT JSON)` plan for the query identified by `queryid`.

**Path parameters:**

| Parameter | Description |
|-----------|-------------|
| `connection_id` | URL-encoded PostgreSQL DSN |
| `queryid` | Integer query ID (as a string) from `pg_stat_statements.queryid` |

**Response:** `application/json` — a `QueryDetail` object.

---

## Pydantic models

### `ExplainError`

```python
class ExplainError(BaseModel):
    step: str
    message: str
```

### `QueryDetail`

```python
class QueryDetail(BaseModel):
    connection_id: str
    queryid: str
    query: str | None
    query_fingerprint: str | None
    calls: int | None
    mean_exec_time_ms: float | None
    total_exec_time_ms: float | None
    stddev_exec_time_ms: float | None
    rows_per_call: float | None
    shared_blks_hit: int | None
    shared_blks_read: int | None
    cache_hit_ratio: float | None
    score: float | None
    plan: list[Any] | None    # parsed EXPLAIN JSON tree
    errors: list[ExplainError]
```

All statistics fields are nullable — they will be `null` if the query was not found in `pg_stat_statements` or if the connection failed. `plan` is `null` when `EXPLAIN` could not be run.

---

## Step sequence

| Step | Description |
|------|-------------|
| 1 `connect` | Opens a connection using `_step_connect` from the Queries module |
| 2 `fetch_query` | Queries `pg_stat_statements` for the single row matching `queryid` |
| 3 `explain` | Runs `EXPLAIN (ANALYZE false, BUFFERS, FORMAT JSON)` on the query text |

Steps 2 and 3 run inside a `finally` block that always closes the connection.

---

## SQL — `_FETCH_SINGLE_SQL`

Identical to the bulk query in the Queries module but filtered by `queryid::text = $1 LIMIT 1`. Uses a 10-second timeout.

---

## Step implementations

### `_step_fetch_query(conn, queryid)`

Returns one of:
- `("ok", row_dict)` — row found and returned as a plain dict
- `("not_found", {})` — `queryid` not in `pg_stat_statements`
- `("fail", {"message": …})` — timeout, privilege error, or DB error

### `_step_explain(conn, query_text)`

Builds and executes:
```sql
EXPLAIN (ANALYZE false, BUFFERS, FORMAT JSON) <query_text>
```

- `ANALYZE false` — does **not** actually execute the query, only plans it
- `BUFFERS` — includes buffer-hit statistics in the plan
- `FORMAT JSON` — returns a structured JSON tree

Returns `("ok", plan_list)` or `("fail", None)` on timeout or any Postgres error.

The `plan_list` is the raw parsed JSON from Postgres, which is a list of plan objects. asyncpg may return it already parsed (as a Python list) or as a raw JSON string, both of which are handled.

---

## Scoring

The query's `score` is computed using the same [`_score_query`](api-queries.md#scoring-algorithm--_score_queryrow-max_total_ms) function from the Queries module, but `max_total_ms` is set to the query's own `total_exec_time_ms` (the maximum is itself, since there is only one query). This gives the absolute score without relative-to-peers normalisation.

---

## Shared utilities

The following are imported directly from [`api.queries`](api-queries.md):

- `_fingerprint` — query normalisation
- `_score_query` — badness score computation
- `_step_connect` — connection with timeout and error handling
