# Module 3 — Slow Query Analysis: Implementation Plan

## Top-Level Overview

Add a `/api/queries/{connection_id}` surface to SlowTrace that reads from
`pg_stat_statements`, fingerprints queries, computes per-query performance
metrics, and returns a ranked list of slow queries. A companion SSE stream
endpoint emits progress events as queries are fetched and ranked.

The module follows the exact conventions of Module 1 (`connections.py`) and
Module 2 (`health.py`):

- Router in `backend/src/api/queries.py`
- Exported in `backend/src/api/__init__.py`
- Registered in `backend/app/main.py`
- Raw SQL through `asyncpg`; no ORM
- `AsyncGenerator[str, None]` + `StreamingResponse` for SSE
- URL-decoded `connection_id` path parameter (same DSN pattern)

On the frontend, `ScanPage.tsx` is currently a stub. This module wires it up
with a DSN input, a live SSE progress bar, and a ranked query table.

---

## File Tree

```
backend/
└── src/
    └── api/
        ├── __init__.py          ← add queries router export
        ├── connections.py       (unchanged)
        ├── health.py            (unchanged)
        └── queries.py           ← NEW

backend/
└── tests/
    └── test_queries.py          ← NEW

frontend/
└── src/
    ├── types/
    │   └── queries.ts           ← NEW — SlowQuery, QueryReport types
    ├── api/
    │   └── queries.ts           ← NEW — fetchQueries(), streamQueries()
    ├── hooks/
    │   └── useQueryScan.ts      ← NEW — SSE hook managing state + events
    ├── components/
    │   ├── QueryTable.tsx        ← NEW — ranked query results table
    │   └── ScanProgress.tsx     ← NEW — step-by-step SSE progress widget
    └── pages/
        └── ScanPage.tsx         ← REPLACE stub with full scan UI
```

`backend/app/main.py` — one-line addition to include the queries router.

---

## API Contract

### `GET /api/queries/{connection_id}`

One-shot JSON response — runs all analysis and returns results.

**Path parameter**
- `connection_id` — URL-encoded DSN string

**Success response `200 OK`**
```json
{
  "connection_id": "postgresql://...",
  "total_queries": 142,
  "queries": [
    {
      "queryid": "8f3a1c2d",
      "query": "SELECT * FROM orders WHERE ...",
      "query_fingerprint": "select * from orders where ...",
      "calls": 5820,
      "mean_exec_time_ms": 312.4,
      "total_exec_time_ms": 1818168.0,
      "stddev_exec_time_ms": 88.1,
      "rows_per_call": 1.2,
      "shared_blks_hit": 9200,
      "shared_blks_read": 340,
      "cache_hit_ratio": 96.4,
      "score": 87.3
    }
  ],
  "errors": []
}
```

**Error response `200 OK`** (connection or extension failure)
```json
{
  "connection_id": "postgresql://...",
  "total_queries": 0,
  "queries": [],
  "errors": [
    {"step": "connect", "message": "Authentication failed: wrong password."}
  ]
}
```

---

### `GET /api/queries/{connection_id}/stream`

SSE stream. Events are emitted as each phase completes.

**Event format**
```
data: {"step": "<step_name>", "status": "ok|warning|fail", "data": {...}}\n\n
```

**Steps streamed in order**
1. `connect` — open asyncpg connection
2. `check_extension` — verify `pg_stat_statements` is installed
3. `check_permissions` — verify SELECT on `pg_stat_statements`
4. `fetch_queries` — execute the main ranking query; `data.count` = row count
5. `result` — final payload (same schema as JSON endpoint)

---

## Query Fingerprinting

A query fingerprint is a normalised, lowercase representation of a query used
for deduplication and display. It is computed purely in Python — no DB round
trip:

1. Strip leading/trailing whitespace; collapse internal whitespace to single
   spaces.
2. Lowercase the string.
3. Replace all literal values with `?`:
   - Quoted strings: `'...'` → `?`
   - Numeric literals (integers and decimals): `\b\d+(\.\d+)?\b` → `?`
4. Strip the `/* ... */` comments that `pg_stat_statements` sometimes injects.

The result is stored in `query_fingerprint` on each `SlowQuery` object.

---

## Scoring Algorithm

Each query receives a `score` in [0, 100] where **higher = worse** (higher
priority to fix). The score combines four signals:

| Signal                | Weight | Formula |
|-----------------------|--------|---------|
| Mean exec time        | 40 %   | `min(mean_ms / 1000, 1) * 40` — capped at 1 s |
| Total exec time share | 30 %   | `(query_total / max_total_in_set) * 30` |
| Cache miss rate       | 20 %   | `(1 - cache_hit_ratio/100) * 20` |
| Std-dev variability   | 10 %   | `min(stddev_ms / mean_ms, 1) * 10` (0 if mean=0) |

`score = sum of weighted signals`, rounded to 1 decimal place.

---

## SQL Query

The main `pg_stat_statements` query (executed once per request):

```sql
SELECT
    queryid::text                        AS queryid,
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

`queryid` is cast to `text` because asyncpg maps `int8` to Python `int` and
the frontend uses it as a string key.

---

## Error Handling

| Scenario                             | Behaviour                                                       |
|--------------------------------------|-----------------------------------------------------------------|
| Connection failure                   | Emit `connect` fail event; `result` with empty `queries`        |
| `pg_stat_statements` not installed   | Emit `check_extension` warning; `result` with empty `queries`   |
| Insufficient privileges              | Emit `check_permissions` warning; include `GRANT` hint          |
| Zero rows returned                   | Valid response: `queries: []`, `total_queries: 0`               |
| Any unexpected `PostgresError`       | Step status `"fail"`; full message in `data.message`            |
| Timeout (fetch step, 30 s)          | Step status `"fail"`; `"message": "Query fetch timed out"`      |

---

## Frontend — Component Design

### `ScanPage.tsx`

Three UI states managed by `useQueryScan` hook:

1. **Idle** — DSN input field + "Scan" button
2. **Scanning** — `ScanProgress` widget showing live SSE step events
3. **Done** — `QueryTable` showing ranked slow queries

### `ScanProgress.tsx`

Receives the live step events array. Renders a vertical stepper:
- `connect`, `check_extension`, `check_permissions`, `fetch_queries`, `result`
- Each step shows: spinner (in progress) / ✓ (ok/warning) / ✗ (fail)
- Warning steps show the `data.message` inline

### `QueryTable.tsx`

Receives `SlowQuery[]`. Renders a sortable table with columns:
- Query (fingerprint, truncated to 80 chars, monospace)
- Calls
- Mean time (ms)
- Total time (ms)
- Cache hit %
- Score (coloured badge: red ≥70, yellow ≥40, green <40)

Clicking a row navigates to `/dashboard/query/:queryid` (Module 4).

### `useQueryScan.ts` hook

Manages three pieces of state:

```ts
type ScanState = 'idle' | 'scanning' | 'done' | 'error'
```

- Opens an `EventSource` to `/api/queries/{encoded_dsn}/stream`
- Appends each parsed event to a `steps` array
- On `result` event: sets `queries` from `data.queries`, transitions to `done`
- On `error` event or SSE error: transitions to `error`

---

## Sub-Tasks

---

### Sub-Task 1 — Backend router scaffold + connect/extension/permissions steps

**Status**: `[ ] pending`

**Intent**
Create `backend/src/api/queries.py` with the router, Pydantic models, SSE
helper, and the first three pipeline steps (`connect`, `check_extension`,
`check_permissions`). Wire into `__init__.py` and `main.py`.

**Expected Outcomes**
- `GET /api/queries/{connection_id}` returns `200 OK` JSON (empty queries)
- `GET /api/queries/{connection_id}/stream` returns `text/event-stream`
- A bad DSN yields a `connect` fail event and empty `queries`
- Missing extension yields a `check_extension` warning event
- All existing tests still pass

**Todo List**
1. Create `backend/src/api/queries.py`:
   - `router = APIRouter(prefix="/api/queries")`
   - Pydantic models: `SlowQuery`, `ScanError`, `QueryReport`
   - `_event()` SSE helper (same as connections.py / health.py)
   - `_step_connect()` — thin wrapper around `asyncpg.connect` with 5 s timeout
   - `_step_check_extension()` — query `pg_extension` for `pg_stat_statements`
   - `_step_check_permissions()` — `SELECT ... LIMIT 1` from `pg_stat_statements`
   - `_run_query_stream()` async generator skeleton (connect + ext + perm steps)
   - `GET /{connection_id:path}/stream` route
   - `GET /{connection_id:path}` route (returns stub `QueryReport`)
2. Add `from .queries import router as queries_router` to
   `backend/src/api/__init__.py` and `backend/app/main.py`

**Relevant Context**
- `backend/src/api/connections.py` — `_step_connect`, `_step_pg_stat_statements`,
  `_step_verify_permissions` are the direct models; reuse the same error handling
- `backend/src/api/health.py:73-76` — `_event()` helper pattern
- `backend/app/main.py:12-18` — router registration pattern

---

### Sub-Task 2 — Query fetch step + fingerprinting + scoring

**Status**: `[ ] pending`

**Intent**
Implement `_step_fetch_queries()` that runs the `pg_stat_statements` SQL,
`_fingerprint()` for query normalisation, and `_score_query()` for the
badness score. Wire all into the generator and JSON endpoint.

**Expected Outcomes**
- `fetch_queries` event carries `{"count": N}` in `data`
- `result` event carries the full `QueryReport` with scored, sorted queries
- Fingerprints correctly strip literals
- Scores rank queries by badness (higher = worse)
- Queries are sorted by `score` descending in the response

**Todo List**
1. Implement `_fingerprint(query: str) -> str` — normalise and strip literals
2. Implement `_score_query(row: dict, max_total_ms: float) -> float` — weighted
   formula from the Scoring Algorithm section
3. Implement `_step_fetch_queries(conn)` — run the SQL, apply fingerprint +
   score to each row, return `(status, list[SlowQuery])`; wrap in 30 s timeout
4. Wire `_step_fetch_queries` into `_run_query_stream()` — emit `fetch_queries`
   event then `result` event with full `QueryReport`
5. Wire into the one-shot JSON endpoint

**Relevant Context**
- Scoring Algorithm section above
- Query Fingerprinting section above
- SQL Query section above — exact SQL to execute

---

### Sub-Task 3 — Backend tests

**Status**: `[ ] pending`

**Intent**
Write `backend/tests/test_queries.py` covering the pure functions, step
functions (mocked DB), and HTTP endpoints.

**Expected Outcomes**
- `_fingerprint` unit tests: literals stripped, comments stripped, whitespace
  collapsed
- `_score_query` unit tests: all-zero input = 0; high mean time = high score
- Step function tests using `AsyncMock` for the asyncpg connection
- Endpoint tests via `httpx.AsyncClient` — happy path + connect fail +
  extension missing
- SSE stream test: collect all events, assert `result` is last, assert shape

**Todo List**
1. Unit tests for `_fingerprint` (5 cases)
2. Unit tests for `_score_query` (boundary cases)
3. Mock-connection tests for `_step_check_extension` (installed / missing)
4. Mock-connection tests for `_step_check_permissions` (ok / insufficient)
5. Mock-connection tests for `_step_fetch_queries` (rows / empty / timeout)
6. `httpx.AsyncClient` endpoint test: connect fail → `errors` populated
7. `httpx.AsyncClient` endpoint test: happy path → `queries` populated + sorted
8. SSE stream test: assert 5 events in order
9. Run `pytest` — all pass

**Relevant Context**
- `backend/tests/test_health.py` — `_collect_sse` helper and AsyncMock patterns
  to reuse directly
- `backend/pyproject.toml` — `asyncio_mode = "auto"` already set

---

### Sub-Task 4 — Frontend types, API client, and hook

**Status**: `[ ] pending`

**Intent**
Create the TypeScript type definitions, the SSE API client, and the
`useQueryScan` hook that manages scan state. No UI yet — this sub-task ends
with the hook ready to be consumed.

**Expected Outcomes**
- `SlowQuery` and `QueryReport` types match the backend JSON schema
- `streamQueries(dsn, onEvent, onDone, onError)` opens an `EventSource` and
  dispatches parsed events
- `useQueryScan()` hook exposes `{ state, steps, queries, start, reset }`
- Hook transitions: `idle → scanning → done | error`

**Todo List**
1. Create `frontend/src/types/queries.ts` with `SlowQuery`, `ScanStep`,
   `QueryReport` interfaces
2. Create `frontend/src/api/queries.ts` with `streamQueries()` that opens
   `EventSource` to `/api/queries/{encodeURIComponent(dsn)}/stream`
3. Create `frontend/src/hooks/useQueryScan.ts` — hook with the state machine
   described in the Frontend section above

**Relevant Context**
- `frontend/src/App.tsx` — route `/dashboard/query/:id` is where clicking a
  query row should navigate (Module 4 concern; just wire the `queryid`)
- `frontend/vite.config.ts` — proxy already configured; use relative `/api`
  path in the API client

---

### Sub-Task 5 — Frontend components and ScanPage

**Status**: `[ ] pending`

**Intent**
Build `ScanProgress`, `QueryTable`, and replace the `ScanPage` stub with the
full scan UI that ties together the hook and components.

**Expected Outcomes**
- `ScanPage` shows idle DSN input, then live scan progress, then results
- `ScanProgress` renders the 5-step stepper with correct status icons
- `QueryTable` renders scored rows, sorted by score descending
- Clicking a query row navigates to `/dashboard/query/:queryid`
- `npm run lint` and `npm run build` pass

**Todo List**
1. Create `frontend/src/components/ScanProgress.tsx`
2. Create `frontend/src/components/QueryTable.tsx`
3. Replace `frontend/src/pages/ScanPage.tsx` with full implementation using
   `useQueryScan`, `ScanProgress`, and `QueryTable`
4. Run `npm run lint` and `npm run build` — fix any TypeScript/lint errors

**Relevant Context**
- `frontend/src/App.tsx:49` — `<Route path="/dashboard/query/:id">` is the
  navigation target when a row is clicked
- Tailwind dark theme: background `#0f172a`, surfaces `slate-800`, accents
  `indigo-500` (follow existing pages for colour tokens)
- `frontend/src/pages/ScanPage.tsx` — currently a 8-line stub; replace entirely

---

## Implementation Notes

1. **`queryid` as string** — `pg_stat_statements.queryid` is `bigint`; cast
   to `text` in SQL and keep as `str` in Python and TypeScript to avoid
   precision loss in JSON.
2. **LIMIT 200** — cap the result set; a production database can have thousands
   of distinct queries. Top 200 by `total_exec_time` is the actionable set.
3. **`pg_stat_statements` may be absent** — the module emits a warning and
   returns empty results rather than an error, consistent with Module 1.
4. **No persistent storage** — identical to Module 2; connection_id is the raw
   DSN decoded per request.
5. **Score direction** — higher score = more urgent to fix. This is the
   opposite of the health score (Module 2) where higher = better.
6. **Frontend EventSource** — the native `EventSource` API does not support
   custom headers; ensure the DSN is URL-encoded as a path segment, not a
   query parameter, so no special CORS headers are needed.
7. **URL encoding** — `connection_id` in the path must be URL-decoded before
   passing to asyncpg; use `urllib.parse.unquote` (already used in health.py).

---

## Completed / Remaining Modules

### COMPLETED MODULES
- **Module 1** — Connection Testing (SSE-based, 4-step connection wizard)
- **Module 2** — Health Monitoring (7 checks, weighted score, SSE + JSON)

### THIS PLAN
- **Module 3** — Slow Query Analysis ← *this plan*

### REMAINING MODULES
- **Module 4** — Query Detail + EXPLAIN output
- **Module 5** — Fix Wizard (guided remediation per query)
- **Module 6** — Dashboard (aggregated metrics, trends over time)
