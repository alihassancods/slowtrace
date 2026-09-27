# Module 4 — Query Detail + EXPLAIN Output: Implementation Plan

## Top-Level Overview

Add a `/api/explain/{connection_id}/{queryid}` surface to SlowTrace that
re-fetches the full metadata for a single query from `pg_stat_statements` and
runs `EXPLAIN (ANALYZE false, BUFFERS, FORMAT JSON)` against it, returning the
plan tree and query stats in a single JSON response.

On the frontend, `QueryDetailPage.tsx` is currently a 7-line stub. This module
replaces it with a detail view: query text, performance stats panel, and a
rendered EXPLAIN plan tree.

The module follows the exact conventions of Modules 1–3:

- Router in `backend/src/api/explain.py`
- Exported in `backend/src/api/__init__.py`
- Registered in `backend/app/main.py`
- Raw SQL through `asyncpg`; no ORM
- URL-decoded `connection_id` path parameter (same DSN pattern)
- No persistent storage — all data fetched live per request

---

## File Tree

```
backend/
└── src/
    └── api/
        ├── __init__.py          ← add explain router export
        ├── connections.py       (unchanged)
        ├── health.py            (unchanged)
        ├── queries.py           (unchanged)
        └── explain.py           ← NEW

backend/
└── tests/
    └── test_explain.py          ← NEW

frontend/
└── src/
    ├── types/
    │   └── explain.ts           ← NEW — QueryDetail, PlanNode types
    ├── api/
    │   └── explain.ts           ← NEW — fetchQueryDetail()
    ├── components/
    │   └── PlanTree.tsx         ← NEW — recursive EXPLAIN plan renderer
    └── pages/
        └── QueryDetailPage.tsx  ← REPLACE stub with full detail UI
```

`backend/app/main.py` — one-line addition to include the explain router.

---

## API Contract

### `GET /api/explain/{connection_id}/{queryid}`

One-shot JSON response. Fetches per-query stats from `pg_stat_statements` and
runs EXPLAIN on the query text.

**Path parameters**
- `connection_id` — URL-encoded DSN string (`postgresql://user:pass@host:5432/db`)
- `queryid` — the `queryid::text` value as returned by Module 3 (`SlowQuery.queryid`)

**Success response `200 OK`**
```json
{
  "connection_id": "postgresql://...",
  "queryid": "8f3a1c2d",
  "query": "SELECT * FROM orders WHERE customer_id = $1",
  "query_fingerprint": "select * from orders where customer_id = ?",
  "calls": 5820,
  "mean_exec_time_ms": 312.4,
  "total_exec_time_ms": 1818168.0,
  "stddev_exec_time_ms": 88.1,
  "rows_per_call": 1.2,
  "shared_blks_hit": 9200,
  "shared_blks_read": 340,
  "cache_hit_ratio": 96.4,
  "score": 87.3,
  "plan": [{ ... }],
  "errors": []
}
```

`plan` is the raw JSON array returned by PostgreSQL's
`EXPLAIN (FORMAT JSON)` — a single-element array containing the plan tree root.
The frontend traverses it recursively.

**Query not found `200 OK`** (queryid not in pg_stat_statements)
```json
{
  "connection_id": "postgresql://...",
  "queryid": "8f3a1c2d",
  "query": null,
  "query_fingerprint": null,
  "calls": null,
  ...all numeric fields null...,
  "plan": null,
  "errors": [{"step": "fetch_query", "message": "Query ID not found in pg_stat_statements."}]
}
```

**Error response `200 OK`** (connection failed)
```json
{
  "connection_id": "postgresql://...",
  "queryid": "8f3a1c2d",
  "query": null,
  ...all fields null...,
  "plan": null,
  "errors": [{"step": "connect", "message": "Authentication failed: wrong password."}]
}
```

---

## SQL Queries

### Fetch single query stats from pg_stat_statements

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
WHERE queryid::text = $1
LIMIT 1
```

### EXPLAIN the query

```sql
EXPLAIN (ANALYZE false, BUFFERS, FORMAT JSON) <query_text>
```

`ANALYZE false` means the plan is generated without actually executing the
query — safe on production databases, no side effects, no row mutations.
`BUFFERS` surfaces `Shared Hit Blocks` / `Shared Read Blocks` in the plan
nodes, which are directly useful for diagnosing cache misses.

The result is a single-element JSON array; store it as-is under `plan`.

---

## Query Fingerprinting

Re-use `_fingerprint()` already defined in `backend/src/api/queries.py`.
Import it directly — do not duplicate it.

```python
from api.queries import _fingerprint, _score_query
```

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| Connection failure | `connect` error; all fields `null` |
| `queryid` not in `pg_stat_statements` | `fetch_query` error; `plan: null` |
| `pg_stat_statements` not installed | `fetch_query` warning; plan attempted anyway with query text from `errors[0].message` |
| `InsufficientPrivilegeError` on stats fetch | `fetch_query` warning; include GRANT hint |
| `EXPLAIN` fails (syntax, permission) | `explain` error; stats fields still populated if stats fetch succeeded |
| Timeout (per-step 10 s) | step error; `"message": "Step timed out"` |
| Any unexpected `PostgresError` | step error; full message |

---

## Frontend — Component Design

### `QueryDetailPage.tsx`

Reads `:id` (the `queryid`) from the URL via `useParams`. Also reads the DSN
from `localStorage` under the key `"slowtrace_dsn"` — `ScanPage` must write
the DSN to `localStorage` when a scan is started so this page can re-use it
without asking the user again.

Three UI states:

1. **Loading** — spinner while `fetchQueryDetail` is in flight
2. **Loaded** — stats panel + plan tree
3. **Error** — error message with a back link

Layout (top to bottom):
- Back link → `/scan`
- Query fingerprint in a monospace code block
- Stats grid (6 tiles): Calls · Mean (ms) · Total (ms) · Cache Hit % · Std Dev · Score badge
- EXPLAIN plan section: `<PlanTree plan={detail.plan} />`

### `PlanTree.tsx`

Receives `plan: object[] | null`.

If `plan` is `null` or the array is empty, renders:
```
EXPLAIN not available
```

Otherwise recursively renders each plan node. A plan node has at minimum:
- `"Node Type"` — string label
- `"Startup Cost"` — float
- `"Total Cost"` — float
- `"Plan Rows"` — int
- `"Actual Rows"` — int (only when ANALYZE true; absent here)
- `"Plans"` — optional child nodes array (recurse)

Each node is rendered as an indented card:
```
┌─ [Seq Scan]  on orders
│  Cost: 0.00..4821.00   Rows: 100000
│  Shared Hit: 3200   Shared Read: 1800
└─ ...children...
```

Indentation increases with each level of nesting. No external libraries needed
— pure JSX recursion.

### `useQueryDetail.ts` hook

```ts
type DetailState = 'idle' | 'loading' | 'loaded' | 'error'

interface UseQueryDetailResult {
  state: DetailState
  detail: QueryDetail | null
  load: (dsn: string, queryid: string) => void
}
```

Calls `fetchQueryDetail(dsn, queryid)` on mount (when `dsn` and `queryid` are
available). Stores the result in state.

### DSN persistence in `ScanPage.tsx`

Add one line to `ScanPage`'s `handleScan`:

```ts
localStorage.setItem('slowtrace_dsn', dsn.trim())
```

This is the only change to existing files outside the new module.

---

## Sub-Tasks

---

### Sub-Task 1 — Backend router scaffold

**Status**: `[ ] pending`

**Intent**
Create `backend/src/api/explain.py` with the router, Pydantic models, and the
`connect` step. Wire into `__init__.py` and `main.py`. The endpoint returns an
empty/null `QueryDetail` until steps are added.

**Expected Outcomes**
- `GET /api/explain/{connection_id}/{queryid}` returns `200` JSON
- A bad DSN yields a `connect` error and all-null fields
- All existing tests still pass

**Todo List**
1. Create `backend/src/api/explain.py`:
   - `router = APIRouter(prefix="/api/explain")`
   - Pydantic model: `ExplainError`, `QueryDetail`
   - `_step_connect()` — thin wrapper reusing the same pattern as `queries.py`
   - `GET /{connection_id:path}/{queryid}` route — skeleton returning stub
     `QueryDetail`
2. Add `from .explain import router as explain_router` to
   `backend/src/api/__init__.py` and `backend/app/main.py`

**Relevant Context**
- `backend/src/api/queries.py:135-149` — `_step_connect` to copy verbatim
- `backend/app/main.py:12-20` — router registration pattern

---

### Sub-Task 2 — Fetch query stats + EXPLAIN steps

**Status**: `[ ] pending`

**Intent**
Implement `_step_fetch_query(conn, queryid)` that reads the single-row stats
from `pg_stat_statements`, and `_step_explain(conn, query_text)` that runs
`EXPLAIN (ANALYZE false, BUFFERS, FORMAT JSON)`. Wire both into the route
handler to produce a fully populated `QueryDetail`.

**Expected Outcomes**
- Known `queryid` returns full stats and a non-null `plan`
- Unknown `queryid` returns null stats, null plan, `errors` populated
- EXPLAIN failure (e.g. permission) populates `errors` but still returns stats
- Both steps are wrapped in a 10-second `asyncio.wait_for`

**Todo List**
1. Import `_fingerprint` and `_score_query` from `api.queries` — do not copy
2. Implement `_step_fetch_query(conn, queryid) -> tuple[str, dict]`
   - Runs the single-row `pg_stat_statements` SQL shown in the SQL Queries section
   - Returns `("ok", row_dict)` or `("not_found", {})` or `("fail", {"message": ...})`
3. Implement `_step_explain(conn, query_text) -> tuple[str, list | None]`
   - Runs `EXPLAIN (ANALYZE false, BUFFERS, FORMAT JSON) <query_text>`
   - asyncpg returns the JSON as a Python object already parsed; wrap in 10 s
     timeout
   - Returns `("ok", plan_list)` or `("fail", None)`
4. Wire both steps into `GET /{connection_id:path}/{queryid}`:
   - connect → fetch_query → explain → return `QueryDetail`
   - If connect fails: return all-null `QueryDetail` with `connect` error
   - If fetch_query returns `not_found`: return null-stats `QueryDetail` with error;
     still skip explain
   - If explain fails: return stats-populated `QueryDetail` with explain error

**Relevant Context**
- SQL Queries section above — exact SQL strings
- `backend/src/api/queries.py:87-100` — `_score_query` signature to call

---

### Sub-Task 3 — Backend tests

**Status**: `[ ] pending`

**Intent**
Write `backend/tests/test_explain.py` covering step functions with mocked
connections and HTTP endpoint integration tests.

**Expected Outcomes**
- `_step_fetch_query`: ok / not_found / timeout / postgres error paths
- `_step_explain`: ok / fail / timeout paths
- HTTP endpoint: connect fail → errors populated; happy path → plan non-null;
  queryid not found → errors populated, plan null
- All tests pass with `pytest`

**Todo List**
1. Create `backend/tests/test_explain.py`
2. Unit tests for `_step_fetch_query`: 4 cases (ok, not_found, timeout, pg error)
3. Unit tests for `_step_explain`: 3 cases (ok, fail, timeout)
4. `httpx.AsyncClient` endpoint test: connect fail
5. `httpx.AsyncClient` endpoint test: happy path (mock connect + fetch + explain)
6. `httpx.AsyncClient` endpoint test: queryid not found
7. Run `pytest` — all pass

**Relevant Context**
- `backend/tests/test_queries.py` — `_make_fetch_row`, `_collect_sse` helpers
  and `AsyncMock` + `patch` patterns to reuse
- `asyncpg` `EXPLAIN FORMAT JSON` returns a list of dicts directly via
  `conn.fetchval`; the mock should return `[{"Plan": {"Node Type": "Seq Scan",
  "Startup Cost": 0.0, "Total Cost": 100.0, "Plan Rows": 1000}}]`

---

### Sub-Task 4 — Frontend types, API client, and hook

**Status**: `[ ] pending`

**Intent**
Create the TypeScript types for the EXPLAIN response, the `fetchQueryDetail`
API function, and the `useQueryDetail` hook. No UI yet.

**Expected Outcomes**
- `QueryDetail` TypeScript interface matches the backend JSON schema exactly
- `PlanNode` interface captures the EXPLAIN JSON node shape
- `fetchQueryDetail(dsn, queryid)` calls `GET /api/explain/{encoded}/{queryid}`
  and returns `Promise<QueryDetail>`
- `useQueryDetail(dsn, queryid)` hook exposes `{ state, detail }` and fetches
  on mount when both arguments are non-empty

**Todo List**
1. Create `frontend/src/types/explain.ts`:
   - `PlanNode` interface (recursive: `Plans?: PlanNode[]`)
   - `ExplainError` interface
   - `QueryDetail` interface — all fields from the API Contract section
2. Create `frontend/src/api/explain.ts`:
   - `fetchQueryDetail(dsn: string, queryid: string): Promise<QueryDetail>`
   - Uses `fetch('/api/explain/${encodeURIComponent(dsn)}/${queryid}')`
3. Create `frontend/src/hooks/useQueryDetail.ts`:
   - `useQueryDetail(dsn: string | null, queryid: string | null)`
   - Calls `fetchQueryDetail` in a `useEffect`; transitions `idle → loading →
     loaded | error`

**Relevant Context**
- `frontend/src/types/queries.ts` — `SlowQuery` interface as the model for
  the shared numeric fields
- `frontend/vite.config.ts` — proxy already configured; use relative `/api` path

---

### Sub-Task 5 — Frontend components and QueryDetailPage

**Status**: `[ ] pending`

**Intent**
Build `PlanTree.tsx` and replace the `QueryDetailPage` stub with the full
detail UI that consumes `useQueryDetail`, renders the stats panel, and shows
the plan tree.

**Expected Outcomes**
- `QueryDetailPage` reads `:id` from route params, reads DSN from
  `localStorage` key `"slowtrace_dsn"`, and passes both to `useQueryDetail`
- Loading spinner shown while fetching
- Stats grid shows 6 tiles with correct values and formatting
- Score badge uses the same colour scheme as `QueryTable` (red ≥70, yellow
  ≥40, green <40)
- `PlanTree` recursively renders plan nodes with indentation; no-plan state
  renders a placeholder message
- `ScanPage` saves DSN to `localStorage` on scan start
- `npm run lint` and `npm run build` pass

**Todo List**
1. Add `localStorage.setItem('slowtrace_dsn', dsn.trim())` to
   `ScanPage.tsx`'s `handleScan` — one line change
2. Create `frontend/src/components/PlanTree.tsx` — recursive node renderer
3. Replace `frontend/src/pages/QueryDetailPage.tsx` with full implementation
   using `useQueryDetail`, the stats grid, and `PlanTree`
4. Run `npm run lint` and `npm run build` — fix any TypeScript/lint errors

**Relevant Context**
- `frontend/src/App.tsx:49` — route is `"/dashboard/query/:id"`; use
  `useParams<{ id: string }>()` to read the queryid
- `frontend/src/components/QueryTable.tsx:7-13` — `ScoreBadge` colour logic
  to replicate in the stats grid
- Tailwind dark theme: bg `#0f172a`, surfaces `slate-800`, borders
  `slate-700`, accents `indigo-500` — follow existing pages exactly

---

## Test Strategy Summary

| Layer | Tool | What is covered |
|---|---|---|
| Unit — step functions | `pytest` + `AsyncMock` | `_step_fetch_query` and `_step_explain` ok/fail/timeout paths |
| HTTP endpoint | `httpx.AsyncClient` | connect fail, happy path, queryid not found |
| Frontend types | TypeScript compiler | `QueryDetail` matches backend schema |
| Frontend hook | Vitest (future) | Not in scope for this module |

---

## Implementation Notes

1. **`ANALYZE false`** — never execute the query; only generate the plan. This
   is safe on any production database. Do not change this to `ANALYZE true`.
2. **Parameter substitution** — `pg_stat_statements` stores queries with `$1`,
   `$2` placeholders. `EXPLAIN` requires literal values for parameterised
   queries. Pass the query text as-is: PostgreSQL will plan it with generic
   parameter estimates, which is sufficient for identifying plan shapes.
3. **Import, don't duplicate** — `_fingerprint` and `_score_query` already
   exist in `queries.py`. Import them directly to keep a single source of
   truth.
4. **`localStorage` DSN** — the simplest cross-page state mechanism that
   avoids a global store. The key `"slowtrace_dsn"` is written by `ScanPage`
   and read by `QueryDetailPage`. If it is missing, show a "Return to Scan"
   prompt instead of attempting a fetch.
5. **asyncpg EXPLAIN result** — `EXPLAIN (FORMAT JSON)` returns a `text`
   column. asyncpg exposes it as a Python string; parse it with `json.loads`.
   The result is always a list with one element: the root plan node object.
6. **URL routing** — the `queryid` segment is a plain string (no slashes), so
   the route is `/{connection_id:path}/{queryid}` with `connection_id` being
   the URL-encoded DSN. Since the DSN ends before the last `/`, `asyncpg`
   path matching will correctly split them as long as the DSN is
   `encodeURIComponent`-encoded on the frontend (no raw `/` characters).

---

## Completed / Remaining Modules

### COMPLETED MODULES
- **Module 1** — Connection Testing (SSE-based, 4-step connection wizard)
- **Module 2** — Health Monitoring (7 checks, weighted score, SSE + JSON)
- **Module 3** — Slow Query Analysis (pg_stat_statements, fingerprinting, SSE + JSON)

### THIS PLAN
- **Module 4** — Query Detail + EXPLAIN output ← *this plan*

### REMAINING MODULES
- **Module 5** — Fix Wizard (guided remediation per query)
- **Module 6** — Dashboard (aggregated metrics, trends over time)
