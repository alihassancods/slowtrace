# Module 6 — Dashboard: Implementation Plan

## Top-Level Overview

Replace the `DashboardPage.tsx` stub with a full aggregated-metrics view that
gives the user a single-screen health snapshot for their PostgreSQL instance.
The dashboard pulls from two existing backend endpoints — `GET /api/health/`
and `GET /api/queries/` — and aggregates and visualises their outputs with no
new backend routes required.

The module is **frontend-only except for one new backend endpoint**:
`GET /api/dashboard/{connection_id}` that fans out to health + queries in
parallel, aggregates the results, and returns a single response object. All
backend work follows the exact conventions of Modules 1–5:

- Router in `backend/src/api/dashboard.py`
- Exported in `backend/src/api/__init__.py`
- Registered in `backend/app/main.py`
- No ORM; results assembled from calls into existing module functions
- No SSE needed — one-shot JSON only

On the frontend, `DashboardPage.tsx` is currently a 7-line stub. This module
wires it up with a DSN guard, a live data fetch, a health score tile, a
top-query mini-table, and six aggregate stat tiles.

---

## File Tree

```
backend/
└── src/
    └── api/
        ├── __init__.py          ← add dashboard router export
        ├── connections.py       (unchanged)
        ├── explain.py           (unchanged)
        ├── fix.py               (unchanged)
        ├── health.py            (unchanged)
        ├── queries.py           (unchanged)
        └── dashboard.py         ← NEW

backend/
└── tests/
    └── test_dashboard.py        ← NEW

frontend/
└── src/
    ├── types/
    │   └── dashboard.ts         ← NEW — DashboardReport type
    ├── api/
    │   └── dashboard.ts         ← NEW — fetchDashboard()
    ├── hooks/
    │   └── useDashboard.ts      ← NEW — loading/loaded/error state machine
    └── pages/
        └── DashboardPage.tsx    ← REPLACE stub with full dashboard UI
```

`backend/app/main.py` — one-line addition to include the dashboard router.

---

## API Contract

### `GET /api/dashboard/{connection_id}`

One-shot JSON response. Fans out to the health and queries pipelines in
parallel using `asyncio.gather`, then assembles a `DashboardReport`.

**Path parameter**
- `connection_id` — URL-encoded DSN string (`postgresql://user:pass@host:5432/db`)

**Success response `200 OK`**
```json
{
  "connection_id": "postgresql://...",
  "health": {
    "score": 87,
    "grade": "B",
    "checks": [
      {
        "name": "connections",
        "status": "ok",
        "value": 42,
        "unit": "active_connections",
        "threshold": 80,
        "message": "42 active connections (53% of max_connections)",
        "deduction": 0
      }
    ],
    "deductions": [],
    "errors": []
  },
  "queries": {
    "total_queries": 142,
    "top_queries": [
      {
        "queryid": "8f3a1c2d",
        "query_fingerprint": "select * from orders where ...",
        "calls": 5820,
        "mean_exec_time_ms": 312.4,
        "total_exec_time_ms": 1818168.0,
        "cache_hit_ratio": 96.4,
        "score": 87.3
      }
    ],
    "avg_score": 54.1,
    "high_priority_count": 3,
    "total_exec_time_ms": 5284021.0,
    "errors": []
  },
  "errors": []
}
```

`queries.top_queries` contains at most **10 entries**, sorted by `score`
descending (same order as `GET /api/queries/`). The full `query` text is
omitted from `top_queries` to keep the response compact — only
`query_fingerprint` is included.

`queries.avg_score` is the mean `score` across all queries in the scan
result, rounded to 1 decimal place. `0.0` when `total_queries` is 0.

`queries.high_priority_count` is the count of queries with `score >= 70`.

`queries.total_exec_time_ms` is the sum of `total_exec_time_ms` across all
queries.

**Error response `200 OK`** (connection failure)
```json
{
  "connection_id": "postgresql://...",
  "health": null,
  "queries": null,
  "errors": [
    {"step": "connect", "message": "Authentication failed: wrong password."}
  ]
}
```

If health succeeds but queries fail (or vice versa), the successful half is
populated and the failed half is `null`, with the error appended to the
top-level `errors` list.

---

## Aggregation Logic

All aggregation is pure Python — no additional DB queries:

### `top_queries`
Take `queries[:10]` from the already-sorted `QueryReport.queries` list
(sorted by `score` descending in `queries.py`). Map each `SlowQuery` to a
`TopQuery` (dropping the full `query` text field).

### `avg_score`
```python
avg_score = round(sum(q.score for q in queries) / len(queries), 1) if queries else 0.0
```

### `high_priority_count`
```python
high_priority_count = sum(1 for q in queries if q.score >= 70)
```

### `total_exec_time_ms`
```python
total_exec_time_ms = sum(q.total_exec_time_ms for q in queries)
```

---

## Backend Implementation Details

### Fan-out pattern

```python
health_report, query_report = await asyncio.gather(
    _run_health(dsn),
    _run_queries(dsn),
    return_exceptions=True,
)
```

`_run_health(dsn)` is a thin coroutine that drives the existing health stream
generator to completion and returns the final `HealthReport` (or raises on
unhandled exception). `_run_queries(dsn)` does the same for the queries
stream, returning `QueryReport`.

Both helpers consume the existing `_run_health_stream` and `_run_query_stream`
async generators from `health.py` and `queries.py` respectively. They iterate
the generator to completion and return the `data` of the `result` event,
deserialized into the relevant Pydantic model.

```python
async def _run_health(dsn: str) -> HealthReport:
    async for raw in _run_health_stream(dsn):
        if raw.startswith("data: "):
            ev = json.loads(raw[len("data: "):])
            if ev["step"] == "result":
                return HealthReport(**ev["data"])
    raise RuntimeError("Health stream ended without result event")

async def _run_queries(dsn: str) -> QueryReport:
    async for raw in _run_query_stream(dsn):
        if raw.startswith("data: "):
            ev = json.loads(raw[len("data: "):])
            if ev["step"] == "result":
                return QueryReport(**ev["data"])
    raise RuntimeError("Queries stream ended without result event")
```

If `asyncio.gather` returns an `Exception` for a slot, that slot becomes
`None` in the response and the exception message is appended to `errors`.

### Imports

```python
from api.health import _run_health_stream, HealthReport
from api.queries import _run_query_stream, QueryReport
```

Both symbols are already defined and importable — no modifications to those
modules are needed.

---

## Pydantic Models

```python
class DashboardError(BaseModel):
    step: str
    message: str

class TopQuery(BaseModel):
    queryid: str
    query_fingerprint: str
    calls: int
    mean_exec_time_ms: float
    total_exec_time_ms: float
    cache_hit_ratio: float
    score: float

class QuerySummary(BaseModel):
    total_queries: int
    top_queries: list[TopQuery]
    avg_score: float
    high_priority_count: int
    total_exec_time_ms: float
    errors: list[DashboardError]

class DashboardReport(BaseModel):
    connection_id: str
    health: HealthReport | None
    queries: QuerySummary | None
    errors: list[DashboardError]
```

`HealthReport` is imported directly from `api.health` — it is not redefined.

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| Connection failure (health leg) | `health: null`; error appended to top-level `errors` |
| Connection failure (queries leg) | `queries: null`; error appended to top-level `errors` |
| Both legs fail | `health: null`, `queries: null`; both errors in `errors` list |
| `pg_stat_statements` missing | Queries leg returns `total_queries: 0`; health leg unaffected |
| Unexpected exception in either leg | Caught by `return_exceptions=True`; treated as that leg failing |
| DSN missing from `localStorage` (frontend) | Dashboard shows "run a scan first" prompt; no API call made |

---

## Frontend — Component Design

### `DashboardPage.tsx`

Reads the DSN from `localStorage` under key `"slowtrace_dsn"` (written by
`ScanPage` on scan start). If absent, renders a "No connection" prompt with a
link to `/scan`.

Three UI states managed by `useDashboard`:

1. **Loading** — full-page spinner
2. **Loaded** — dashboard panels
3. **Error** — error banner with a back link

Layout (top to bottom):
- Page heading "Dashboard" + a "Refresh" button (re-runs the fetch)
- DSN display line (`font-mono text-indigo-400`, truncated)
- Health Score section — large grade badge + score number + check list summary
- Query Overview section — 4 stat tiles + top-10 query mini-table
- Error banners if `report.errors` is non-empty

### Health Score Section

```
┌────────────────────────────────────────────────┐
│  Grade  │  Score  │  Checks passing  │  Deductions │
│   [B]   │   87    │   6 / 7          │    -13 pts  │
└────────────────────────────────────────────────┘
Failed / warned checks listed below as compact rows:
  ⚠ cache_hit_ratio: Cache hit ratio is 94.1% (target ≥ 95%)
  ✗ index_usage: 3 tables rely heavily on seq scans
```

Grade badge colours:
- A → `emerald`
- B → `indigo`
- C → `yellow`
- D/F → `red`

### Query Overview Section

Four aggregate stat tiles in a 2×2 (mobile) / 4×1 (desktop) grid:

| Tile | Value source |
|---|---|
| Total Queries | `report.queries.total_queries` |
| High Priority (≥70) | `report.queries.high_priority_count` |
| Avg Score | `report.queries.avg_score` |
| Total Exec Time | `report.queries.total_exec_time_ms` formatted as seconds (÷1000, 1 decimal) |

Below the tiles: a condensed 4-column table of `top_queries` (top 10):
- Query (fingerprint, truncated to 60 chars, monospace)
- Mean (ms)
- Cache %
- Score badge

Clicking a row navigates to `/dashboard/query/:queryid` (existing Module 4
route). The table is read-only (no sorting) — it is always ordered by score
descending as returned by the API.

### `useDashboard.ts` hook

```ts
type DashboardState = 'idle' | 'loading' | 'loaded' | 'error'

interface UseDashboardResult {
  state: DashboardState
  report: DashboardReport | null
  load: (dsn: string) => void
}
```

- Calls `fetchDashboard(dsn)` when `load` is called (also called on mount
  when DSN is available)
- Transitions: `idle → loading → loaded | error`
- `load` can be called again to refresh (re-triggers the fetch)

### Navigation

No new routes. The dashboard lives at `/dashboard` (already registered in
`App.tsx`). The top-query mini-table links to the existing
`/dashboard/query/:id` route.

---

## Sub-Tasks

---

### Sub-Task 1 — Backend router scaffold + fan-out + aggregation

**Status**: `[ ] pending`

**Intent**
Create `backend/src/api/dashboard.py` with the router, Pydantic models,
`_run_health` / `_run_queries` helpers, and the fan-out route handler. Wire
into `__init__.py` and `main.py`.

**Expected Outcomes**
- `GET /api/dashboard/{connection_id}` returns `200 OK` JSON
- Both health and query data are populated for a valid DSN
- A bad DSN returns `health: null`, `queries: null`, and an `errors` entry
- No modifications to `health.py` or `queries.py`
- All existing tests still pass

**Todo List**
1. Create `backend/src/api/dashboard.py`:
   - `router = APIRouter(prefix="/api/dashboard")`
   - Import `_run_health_stream` + `HealthReport` from `api.health`
   - Import `_run_query_stream` + `QueryReport` from `api.queries`
   - Pydantic models: `DashboardError`, `TopQuery`, `QuerySummary`,
     `DashboardReport`
   - `_run_health(dsn) -> HealthReport` — iterates stream, returns on
     `result` event
   - `_run_queries(dsn) -> QueryReport` — iterates stream, returns on
     `result` event
   - `_build_query_summary(report: QueryReport) -> QuerySummary` — slices
     top 10, computes `avg_score`, `high_priority_count`, `total_exec_time_ms`
   - `GET /{connection_id:path}` route — `asyncio.gather` both helpers with
     `return_exceptions=True`, assemble `DashboardReport`
2. Add `from .dashboard import router as dashboard_router` to
   `backend/src/api/__init__.py` and `backend/app/main.py`

**Relevant Context**
- `backend/src/api/health.py` — `_run_health_stream` generator and `HealthReport` model
- `backend/src/api/queries.py` — `_run_query_stream` generator and `QueryReport` model
- `backend/app/main.py:12-24` — router registration pattern
- Aggregation Logic section above — exact formulas for the four summary fields

---

### Sub-Task 2 — Backend tests

**Status**: `[ ] pending`

**Intent**
Write `backend/tests/test_dashboard.py` covering the aggregation helpers and
the HTTP endpoint with mocked health + query data.

**Expected Outcomes**
- `_build_query_summary` unit tests: empty queries → zeros; 15 queries → top
  10 returned, correct avg/count/total
- HTTP endpoint test: happy path → both `health` and `queries` populated
- HTTP endpoint test: bad DSN → `health: null`, `queries: null`, errors non-empty
- HTTP endpoint test: health fails, queries succeeds → `health: null`,
  `queries` populated, one error
- All tests pass with `pytest`

**Todo List**
1. Create `backend/tests/test_dashboard.py`
2. Unit tests for `_build_query_summary`:
   - Empty `QueryReport` → all zeros, `top_queries: []`
   - 5 queries → all 5 returned in `top_queries`, correct avg/count/total
   - 15 queries → only top 10 in `top_queries` (highest scores)
3. Mock `_run_health` and `_run_queries` via `unittest.mock.patch`:
   - Happy path: both return valid Pydantic objects → assert response shape
   - `_run_health` raises → `health: null`, error in `errors`
   - `_run_queries` raises → `queries: null`, error in `errors`
4. `httpx.AsyncClient` endpoint test for the happy path (patch both helpers)
5. `httpx.AsyncClient` endpoint test for connect fail (patch both to raise)
6. Run `pytest` — all pass

**Relevant Context**
- `backend/tests/test_queries.py` — `_make_fetch_row` and `AsyncMock` patterns
  to reuse
- `backend/tests/test_health.py` — `_collect_sse` helper and mock connection
  patterns
- `backend/pyproject.toml` — `asyncio_mode = "auto"` already set

---

### Sub-Task 3 — Frontend types and API client

**Status**: `[ ] pending`

**Intent**
Create the TypeScript type definitions and the `fetchDashboard` API function.
No UI yet — this sub-task ends with typed data ready to consume.

**Expected Outcomes**
- `DashboardReport`, `QuerySummary`, `TopQuery` TypeScript interfaces match
  the backend JSON schema exactly
- `fetchDashboard(dsn)` calls `GET /api/dashboard/{encodeURIComponent(dsn)}`
  and returns `Promise<DashboardReport>`
- TypeScript compiler (`npm run build`) passes with no errors

**Todo List**
1. Create `frontend/src/types/dashboard.ts`:
   - Re-export or inline the `CheckResult` and `Deduction` shapes from
     health (or define them locally — do not import from backend types)
   - `HealthSummary` — mirrors `HealthReport` fields needed by the UI:
     `score`, `grade`, `checks`, `deductions`, `errors`
   - `TopQuery` interface (7 fields: `queryid`, `query_fingerprint`, `calls`,
     `mean_exec_time_ms`, `total_exec_time_ms`, `cache_hit_ratio`, `score`)
   - `QuerySummary` interface
   - `DashboardError` interface
   - `DashboardReport` interface
2. Create `frontend/src/api/dashboard.ts`:
   - `fetchDashboard(dsn: string): Promise<DashboardReport>` — calls
     `fetch('/api/dashboard/${encodeURIComponent(dsn)}')`, parses JSON,
     throws on non-2xx

**Relevant Context**
- `frontend/src/types/queries.ts` — `SlowQuery` as the model for numeric
  field names (they match `TopQuery`)
- `frontend/src/api/explain.ts` — `fetchQueryDetail` as the pattern for the
  fetch wrapper
- `frontend/vite.config.ts` — proxy already configured; use relative `/api` path

---

### Sub-Task 4 — `useDashboard` hook

**Status**: `[ ] pending`

**Intent**
Create the `useDashboard` React hook that manages the
`idle → loading → loaded | error` state machine, calls `fetchDashboard`, and
exposes a `load` function for the refresh button.

**Expected Outcomes**
- Hook exposes `{ state, report, load }`
- `load(dsn)` transitions `→ loading`, calls `fetchDashboard`, then
  `→ loaded` or `→ error`
- Calling `load` again while loaded transitions back to `loading` and
  re-fetches (refresh behaviour)
- TypeScript strict-mode compiles without errors

**Todo List**
1. Create `frontend/src/hooks/useDashboard.ts`:
   - `DashboardState` type: `'idle' | 'loading' | 'loaded' | 'error'`
   - State: `state`, `report`
   - `load(dsn: string)` — sets `loading`, awaits `fetchDashboard`, sets
     result; on throw sets `error`
   - Auto-loads on mount: `useEffect` calls `load(dsn)` when a non-null DSN
     is passed to the hook
   - Signature: `useDashboard(dsn: string | null): UseDashboardResult`

**Relevant Context**
- `frontend/src/hooks/useQueryDetail.ts` — exact `useEffect` + `useState`
  pattern to follow
- `frontend/src/hooks/useFixWizard.ts` — another reference for the same
  loading pattern

---

### Sub-Task 5 — Frontend components and DashboardPage

**Status**: `[ ] pending`

**Intent**
Build the dashboard UI inline in `DashboardPage.tsx` (no separate component
files needed — all UI sections are self-contained within the page) and replace
the stub.

**Expected Outcomes**
- Page guards against missing DSN with a "run a scan first" prompt
- Loading spinner shown while `state === 'loading'`
- Error banner shown when `state === 'error'`
- Health score section renders grade badge, score, pass/fail check list
- Query overview renders 4 stat tiles and a 4-column top-10 mini-table
- Clicking a mini-table row navigates to `/dashboard/query/:queryid`
- Refresh button calls `load(dsn)` again
- `npm run lint` and `npm run build` pass with no errors

**Todo List**
1. Replace `frontend/src/pages/DashboardPage.tsx` with full implementation:
   - Read DSN from `localStorage.getItem('slowtrace_dsn')`
   - Call `useDashboard(dsn)` hook
   - Render DSN-missing guard
   - Render loading / error states
   - Render `HealthSection` — grade badge (colour by grade), score number,
     checks-passing count, total deduction points, failed/warned check rows
   - Render `QuerySection` — 4 stat tiles, top-10 mini-table with navigation
   - Render top-level error banners from `report.errors`
   - Refresh button: `onClick={() => dsn && load(dsn)}`
2. Run `npm run lint` — fix any ESLint errors
3. Run `npm run build` — fix any TypeScript errors

**Relevant Context**
- `frontend/src/App.tsx:48` — route is `/dashboard`; no changes needed
- `frontend/src/pages/QueryDetailPage.tsx` — `StatTile`, `ScoreBadge`, and
  spinner JSX to replicate exactly (copy the helpers inline, do not extract
  to shared components)
- `frontend/src/components/QueryTable.tsx:7-19` — `ScoreBadge` colour
  thresholds (red ≥70, yellow ≥40, green <40)
- Tailwind dark theme: bg `#0f172a`, surfaces `slate-800`, borders
  `slate-700`, accents `indigo-500` — follow existing pages exactly
- Grade badge colours: A → `emerald-500`, B → `indigo-500`, C → `yellow-500`,
  D/F → `red-500` — use the same `/20` background + `/40` border pattern as
  `ScoreBadge`

---

## Test Strategy Summary

| Layer | Tool | What is covered |
|---|---|---|
| Unit — aggregation | `pytest` | `_build_query_summary`: empty, 5-row, 15-row cases |
| HTTP endpoint | `httpx.AsyncClient` + `patch` | Happy path, connect fail, one-leg fail |
| Frontend types | TypeScript compiler | `DashboardReport` matches backend schema |
| Frontend hook | `npm run build` (tsc) | Hook and page compile in strict mode |

---

## Implementation Notes

1. **No new backend routes beyond `/api/dashboard/`** — the module deliberately
   reuses `_run_health_stream` and `_run_query_stream` rather than duplicating
   SQL. The dashboard is an aggregation layer, not a new data source.
2. **`HealthReport` import** — `HealthReport` is already a `BaseModel` in
   `health.py`. Import it directly into `dashboard.py` rather than redefining
   it. Same for `QueryReport` from `queries.py`.
3. **`top_queries` omits `query` field** — the full raw SQL text from
   `pg_stat_statements` can be thousands of characters; it is not needed for
   the dashboard mini-table and would bloat the response unnecessarily.
4. **`asyncio.gather` with `return_exceptions=True`** — this is the only safe
   way to fan out two potentially-failing coroutines without one exception
   cancelling the other. Always check `isinstance(result, Exception)` before
   accessing result fields.
5. **DSN in `localStorage`** — the dashboard reads the same `"slowtrace_dsn"`
   key that `ScanPage` writes. If the user navigates directly to `/dashboard`
   without having scanned first, the guard prompt is shown. No additional
   state management (Redux, Context) is introduced.
6. **Refresh button** — calls `load(dsn)` again. The hook transitions back to
   `loading`, clearing the previous report, so a stale result is never shown
   alongside a spinner. Set `report` to `null` at the start of `load` before
   calling `fetchDashboard`.
7. **Mini-table is not sortable** — the top-10 list is always score-descending
   as returned by the API. Adding sort controls would add complexity with
   little benefit on a capped 10-row list.
8. **No chart/SVG** — the dashboard uses stat tiles and a table only. A
   bar-chart visualisation of scores is explicitly out of scope for this
   module to keep the implementation lean.

---

## Completed / Remaining Modules

### COMPLETED MODULES
- **Module 1** — Connection Testing (SSE-based, 4-step connection wizard)
- **Module 2** — Health Monitoring (7 checks, weighted score, SSE + JSON)
- **Module 3** — Slow Query Analysis (pg_stat_statements, fingerprinting, SSE + JSON)
- **Module 4** — Query Detail + EXPLAIN output
- **Module 5** — Fix Wizard (guided remediation per query)

### THIS PLAN
- **Module 6** — Dashboard (aggregated metrics overview) ← *this plan*

### REMAINING MODULES
- None — Module 6 completes the SlowTrace feature set.
