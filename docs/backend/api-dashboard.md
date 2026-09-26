# Dashboard API — `backend/src/api/dashboard.py`

## Overview

[`backend/src/api/dashboard.py`](../../backend/src/api/dashboard.py) provides a single aggregated endpoint that fans out to the Health and Queries modules in parallel and combines their results into one `DashboardReport`.

**Base prefix:** `/api/dashboard`

---

## Route

### `GET /api/dashboard/{connection_id}`

Runs the health checks and the slow-query scan **concurrently** using `asyncio.gather`, then aggregates the results.

**Path parameters:**

| Parameter | Description |
|-----------|-------------|
| `connection_id` | URL-encoded PostgreSQL DSN |

**Response:** `application/json` — a `DashboardReport` object.

The response is always `200 OK`. Partial failures (e.g. health failed but queries succeeded) are reported in `DashboardReport.errors`; the successful sub-report is still included.

---

## Pydantic models

### `DashboardError`

```python
class DashboardError(BaseModel):
    step: str     # "health" | "queries"
    message: str
```

### `TopQuery`

A slimmed-down view of a `SlowQuery` surfaced in the dashboard (top 10 by score):

```python
class TopQuery(BaseModel):
    queryid: str
    query_fingerprint: str
    calls: int
    mean_exec_time_ms: float
    total_exec_time_ms: float
    cache_hit_ratio: float
    score: float
```

### `QuerySummary`

```python
class QuerySummary(BaseModel):
    total_queries: int
    top_queries: list[TopQuery]   # top 10 by score
    avg_score: float
    high_priority_count: int      # queries with score >= 70
    total_exec_time_ms: float
    errors: list[DashboardError]
```

### `DashboardReport`

```python
class DashboardReport(BaseModel):
    connection_id: str
    health: HealthReport | None    # None if health check failed
    queries: QuerySummary | None   # None if query scan failed
    errors: list[DashboardError]
```

---

## Parallelism

The module uses `asyncio.gather(..., return_exceptions=True)` so that a failure in one sub-task does not cancel the other:

```python
health_result, query_result = await asyncio.gather(
    _run_health(dsn),
    _run_queries(dsn),
    return_exceptions=True,
)
```

If `health_result` is an `Exception`, a `DashboardError(step="health", …)` is appended and `health` is set to `None`. The same applies to `query_result`.

---

## Stream consumers

The dashboard does not re-implement health or query logic. It drives the SSE generators exported by the Health and Queries modules and reads only the final `result` event:

### `_run_health(dsn) → HealthReport`

Iterates `_run_health_stream(dsn)` until a `"step": "result"` event is found, then constructs and returns a `HealthReport` from it.

### `_run_queries(dsn) → QueryReport`

Iterates `_run_query_stream(dsn)` until a `"step": "result"` event is found, then constructs and returns a `QueryReport` from it.

---

## Aggregation — `_build_query_summary(report)`

Converts a full `QueryReport` into the lighter `QuerySummary`:

- Takes the top 10 queries by score (already sorted in the Queries module)
- Computes `avg_score` across **all** queries (not just top 10)
- Counts `high_priority_count` as queries with `score >= 70`
- Sums `total_exec_time_ms` across all queries
- Forwards scan errors as `DashboardError` objects
