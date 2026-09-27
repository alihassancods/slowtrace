# Module 5 — Fix Wizard: Implementation Plan

## Top-Level Overview

Add a `/api/fix/{connection_id}/{queryid}` surface to SlowTrace that analyses a
slow query's performance signals — EXPLAIN plan shape, cache miss rate, mean
execution time, and call frequency — and returns a ranked list of concrete,
actionable fix recommendations. Each recommendation carries a title, a
severity, a one-paragraph explanation, and a ready-to-run SQL or config
snippet.

On the frontend, `FixPage.tsx` is currently a 7-line stub. The route
`/fix/:id` is already registered in `App.tsx`. This module replaces the stub
with a detail view: query fingerprint header, a stats summary bar, and a
prioritised list of fix cards.

The module follows the exact conventions of Modules 1–4:

- Router in `backend/src/api/fix.py`
- Exported in `backend/src/api/__init__.py`
- Registered in `backend/app/main.py`
- Raw SQL through `asyncpg`; no ORM
- URL-decoded `connection_id` path parameter (same DSN pattern)
- No persistent storage — all analysis is computed live per request
- Re-uses `_step_connect`, `_fingerprint`, `_score_query` from existing modules

---

## File Tree

```
backend/
└── src/
    └── api/
        ├── __init__.py          ← add fix router export
        ├── connections.py       (unchanged)
        ├── explain.py           (unchanged)
        ├── health.py            (unchanged)
        ├── queries.py           (unchanged)
        └── fix.py               ← NEW

backend/
└── tests/
    └── test_fix.py              ← NEW

frontend/
└── src/
    ├── types/
    │   └── fix.ts               ← NEW — FixRecommendation, FixReport types
    ├── api/
    │   └── fix.ts               ← NEW — fetchFix()
    ├── hooks/
    │   └── useFixWizard.ts      ← NEW — fetch hook managing load state
    ├── components/
    │   └── FixCard.tsx          ← NEW — single recommendation card
    └── pages/
        └── FixPage.tsx          ← REPLACE stub with full fix UI
```

`backend/app/main.py` — one-line addition to include the fix router.

---

## API Contract

### `GET /api/fix/{connection_id}/{queryid}`

One-shot JSON response. Fetches query stats from `pg_stat_statements`, runs
`EXPLAIN (ANALYZE false, BUFFERS, FORMAT JSON)`, analyses the results, and
returns a list of ranked recommendations.

**Path parameters**
- `connection_id` — URL-encoded DSN string (`postgresql://user:pass@host:5432/db`)
- `queryid` — the `queryid::text` value as returned by Module 3 (`SlowQuery.queryid`)

**Success response `200 OK`**
```json
{
  "connection_id": "postgresql://...",
  "queryid": "8f3a1c2d",
  "query_fingerprint": "select * from orders where customer_id = ?",
  "score": 87.3,
  "recommendations": [
    {
      "id": "missing_index",
      "title": "Add an index on the filter column",
      "severity": "high",
      "explanation": "The EXPLAIN plan shows a Seq Scan on 'orders' scanning 100,000 rows. Adding an index on the columns referenced in the WHERE clause will allow PostgreSQL to use an Index Scan, reducing the rows examined from ~100k to only those matching the predicate.",
      "sql": "CREATE INDEX CONCURRENTLY idx_orders_customer_id ON orders (customer_id);"
    },
    {
      "id": "low_cache_hit",
      "title": "Increase shared_buffers to improve cache hit ratio",
      "severity": "medium",
      "explanation": "The cache hit ratio for this query is 71.3%, below the recommended 95% threshold. This means roughly 1 in 3 block reads goes to disk. Increasing shared_buffers (typically to 25% of RAM) and ensuring autovacuum keeps the table statistics fresh will improve buffer pool effectiveness.",
      "sql": "-- In postgresql.conf:\nshared_buffers = '2GB'  -- adjust to 25% of available RAM\n-- Then run: SELECT pg_reload_conf();"
    }
  ],
  "errors": []
}
```

**Query not found `200 OK`**
```json
{
  "connection_id": "postgresql://...",
  "queryid": "8f3a1c2d",
  "query_fingerprint": null,
  "score": null,
  "recommendations": [],
  "errors": [{"step": "fetch_query", "message": "Query ID not found in pg_stat_statements."}]
}
```

**Connection failed `200 OK`**
```json
{
  "connection_id": "postgresql://...",
  "queryid": "8f3a1c2d",
  "query_fingerprint": null,
  "score": null,
  "recommendations": [],
  "errors": [{"step": "connect", "message": "Authentication failed: wrong password."}]
}
```

---

## Recommendation Engine

The engine is a pure Python function that takes a `QueryDetail`-equivalent
dict and returns a list of `FixRecommendation` objects. It evaluates five
independent rule categories in priority order. Multiple recommendations can
be returned; they are sorted by severity (`high` → `medium` → `low`).

---

### Rule 1 — `seq_scan_large_table`

**Trigger**: EXPLAIN plan contains a `Seq Scan` node where `Plan Rows >= 1000`.

**Severity**: `high`

**Title**: `"Sequential scan on large table — consider an index"`

**Explanation**: Describes the table name (from the `Relation Name` field in
the plan node if present), the estimated row count from `Plan Rows`, and the
cost range. States that an index on the filter columns would allow an Index
Scan, reducing I/O proportionally.

**SQL snippet**:
```sql
-- Replace <table> and <column> with the actual table and filter column(s)
-- visible in the query fingerprint or plan node.
CREATE INDEX CONCURRENTLY idx_<table>_<column> ON <table> (<column>);
```

If `Relation Name` is available in the plan node, substitute it into the
snippet. Otherwise emit the generic template.

---

### Rule 2 — `low_cache_hit`

**Trigger**: `cache_hit_ratio < 95.0`

**Severity**: `high` if `cache_hit_ratio < 80.0`, otherwise `medium`.

**Title**: `"Low buffer-cache hit ratio (N%)"`

**Explanation**: States the measured ratio, explains that hits below 95% mean
frequent disk reads, and recommends increasing `shared_buffers` (to ~25% of
RAM) and running `VACUUM ANALYZE` to refresh planner statistics.

**SQL snippet**:
```sql
-- In postgresql.conf, increase shared_buffers (restart required):
shared_buffers = '2GB'   -- adjust to ~25% of total RAM

-- Refresh table statistics (no restart required):
VACUUM ANALYZE;
```

---

### Rule 3 — `high_mean_exec_time`

**Trigger**: `mean_exec_time_ms >= 500` (500 ms per call).

**Severity**: `high` if `mean_exec_time_ms >= 2000`, otherwise `medium`.

**Title**: `"High mean execution time (N ms)"`

**Explanation**: States the mean time and total execution time. Recommends
reviewing the EXPLAIN plan for expensive nodes (Hash Join, Sort, nested loops
on large sets), and checking whether `work_mem` is sufficient to avoid spill
to disk.

**SQL snippet**:
```sql
-- Increase work_mem for sort/hash operations in this session:
SET work_mem = '64MB';

-- Or globally in postgresql.conf (requires reload):
work_mem = '32MB'
-- Then: SELECT pg_reload_conf();
```

---

### Rule 4 — `high_stddev`

**Trigger**: `stddev_exec_time_ms / mean_exec_time_ms >= 0.5` AND
`mean_exec_time_ms >= 100` (to avoid noise on very fast queries).

**Severity**: `medium`

**Title**: `"High execution time variability (stddev N ms)"`

**Explanation**: States the stddev-to-mean ratio. High variability typically
indicates lock contention, autovacuum interference, or plan instability. Recommends
checking `pg_stat_activity` for blocking queries and ensuring `pg_stat_statements`
is reset periodically to avoid stale statistics influencing the planner.

**SQL snippet**:
```sql
-- Check for current lock contention:
SELECT pid, query, wait_event_type, wait_event, state
FROM pg_stat_activity
WHERE wait_event IS NOT NULL AND state != 'idle';

-- Reset pg_stat_statements to get a fresh baseline:
SELECT pg_stat_statements_reset();
```

---

### Rule 5 — `missing_vacuum`

**Trigger**: EXPLAIN plan contains a `Seq Scan` node AND `shared_blks_read > shared_blks_hit`
(more disk reads than cache hits on this query, indicating table bloat or stale
visibility map preventing index-only scans).

**Severity**: `low`

**Title**: `"Table may benefit from VACUUM ANALYZE"`

**Explanation**: Explains that a high ratio of physical reads relative to
cache hits on a sequentially scanned table often indicates table bloat (dead
tuples) or out-of-date planner statistics. Running `VACUUM ANALYZE` reclaims
dead tuple space and refreshes row-count estimates, which may cause the planner
to switch from a Seq Scan to an Index Scan.

**SQL snippet**:
```sql
-- Replace <table> with the actual table name from the EXPLAIN plan:
VACUUM ANALYZE <table>;
```

---

### Severity ordering

Recommendations are sorted in the response: `high` first, then `medium`, then
`low`. Within the same severity level, preserve the evaluation order above
(Rule 1 before Rule 2, etc.). Each rule fires at most once per request.

---

## Plan Node Traversal Helper

The EXPLAIN plan is a nested tree. Rules 1 and 5 need to walk all nodes.
Implement a single helper:

```python
def _walk_plan(plan: list[dict]) -> list[dict]:
    """Yield every plan node from the EXPLAIN JSON tree (depth-first)."""
```

This returns a flat list of all nodes in the tree so rule functions can
`any()`/`filter()` over them without recursive logic in each rule.

The root of the EXPLAIN JSON from asyncpg is always:
```json
[{"Plan": { "Node Type": "...", "Plans": [...] }}]
```
`_walk_plan` should unwrap the top-level `"Plan"` key and recurse into
`"Plans"` children.

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| Connection failure | `connect` error; `recommendations: []`, `score: null` |
| `queryid` not in `pg_stat_statements` | `fetch_query` error; `recommendations: []` |
| `pg_stat_statements` not installed | `fetch_query` error (same as Module 4) |
| `InsufficientPrivilegeError` on stats fetch | `fetch_query` error with GRANT hint |
| `EXPLAIN` fails | `explain` warning; rules that need the plan are skipped; stat-only rules still fire |
| Timeout (per-step 10 s) | step error; `"message": "Step timed out"` |
| Any unexpected `PostgresError` | step error; full message |

The engine always returns whatever recommendations it can generate from
available data. If the EXPLAIN plan is absent (`plan is None`), Rules 1 and 5
are skipped silently; Rules 2, 3, and 4 still fire if their stat thresholds
are met.

---

## Implementation Details

### Imports from existing modules

```python
from api.queries import _fingerprint, _score_query, _step_connect
from api.explain import _step_fetch_query, _step_explain
```

Do not copy these functions — import them directly. `fix.py` adds only the
recommendation engine on top of what Modules 3 and 4 already provide.

### `_step_connect` and step reuse

`_step_connect`, `_step_fetch_query`, and `_step_explain` are imported
verbatim and called in sequence inside the route handler using the same
connect → fetch → explain → close pattern as `explain.py`.

### Score computation

Call `_score_query(row, row["total_exec_time_ms"] or 0.0)` — identical to how
`explain.py` computes the score for a single query (no other queries in set,
so `max_total_ms` equals the query's own total).

---

## Frontend — Component Design

### `FixPage.tsx`

Reads `:id` (the `queryid`) from the URL via `useParams`. Reads the DSN from
`localStorage` under key `"slowtrace_dsn"` — written by `ScanPage` (already
implemented in Module 3).

Three UI states managed by `useFixWizard`:

1. **Loading** — spinner while `fetchFix` is in flight
2. **Loaded** — query fingerprint header + stats bar + fix cards list
3. **Error** — error banner with a back link

Layout (top to bottom):
- Back link → `/dashboard/query/:id`
- Query fingerprint in a monospace code block
- Stats bar: Score badge · Mean (ms) · Total (ms) · Cache Hit % (same 4-tile
  strip as QueryDetailPage, but horizontal and compact)
- Section heading "Recommendations" + count badge
- Ordered list of `FixCard` components (one per recommendation)
- If `recommendations` is empty: a "No issues detected" empty state

### `FixCard.tsx`

Receives a single `FixRecommendation`. Renders:

```
┌─────────────────────────────────────────────┐
│ [HIGH]  Sequential scan on large table       │
│                                              │
│  Explanation paragraph text…                 │
│                                              │
│  ┌──────────────────────────────────────┐   │
│  │ CREATE INDEX CONCURRENTLY …          │   │  ← monospace code block
│  └──────────────────────────────────────┘   │
└─────────────────────────────────────────────┘
```

Severity badge colours:
- `high` — red (`bg-red-500/20 text-red-300 border-red-500/40`)
- `medium` — yellow (`bg-yellow-500/20 text-yellow-300 border-yellow-500/40`)
- `low` — slate (`bg-slate-500/20 text-slate-300 border-slate-500/40`)

The SQL snippet block has a "Copy" button in the top-right corner that copies
the SQL to the clipboard using `navigator.clipboard.writeText`.

### `useFixWizard.ts` hook

```ts
type FixState = 'idle' | 'loading' | 'loaded' | 'error'

interface UseFixWizardResult {
  state: FixState
  report: FixReport | null
}
```

Calls `fetchFix(dsn, queryid)` in a `useEffect` on mount when both `dsn` and
`queryid` are non-null. Transitions `idle → loading → loaded | error`.

### Navigation wiring

`QueryDetailPage.tsx` needs a "Fix Wizard" button that navigates to
`/fix/:queryid`. Add it next to the back link — one line change only.

---

## Sub-Tasks

---

### Sub-Task 1 — Backend router scaffold

**Status**: `[ ] pending`

**Intent**
Create `backend/src/api/fix.py` with the router, Pydantic models, and a
working route that connects, fetches stats, and runs EXPLAIN — returning a
stub `FixReport` with empty recommendations. Wire into `__init__.py` and
`main.py`.

**Expected Outcomes**
- `GET /api/fix/{connection_id}/{queryid}` returns `200` JSON
- A bad DSN yields a `connect` error and empty recommendations
- An unknown queryid yields a `fetch_query` error and empty recommendations
- All existing tests still pass

**Todo List**
1. Create `backend/src/api/fix.py`:
   - `router = APIRouter(prefix="/api/fix")`
   - Pydantic models: `FixError`, `FixRecommendation`, `FixReport`
   - Import `_fingerprint`, `_score_query`, `_step_connect` from `api.queries`
   - Import `_step_fetch_query`, `_step_explain` from `api.explain`
   - `GET /{connection_id:path}/{queryid}` route skeleton — connect → fetch →
     explain → return stub `FixReport(recommendations=[])`
2. Add `from .fix import router as fix_router` to
   `backend/src/api/__init__.py`
3. Add `from api.fix import router as fix_router` and
   `app.include_router(fix_router)` to `backend/app/main.py`

**Relevant Context**
- `backend/src/api/explain.py:148-239` — identical connect/fetch/explain
  wiring to replicate
- `backend/app/main.py:12-22` — router registration pattern

---

### Sub-Task 2 — Recommendation engine

**Status**: `[ ] pending`

**Intent**
Implement `_walk_plan()` and the five rule functions, then wire them into
`_analyse()` which takes the fetched row dict and parsed plan and returns
`list[FixRecommendation]`. Wire `_analyse` into the route handler.

**Expected Outcomes**
- A query with a Seq Scan on ≥ 1000 rows returns a `missing_index` recommendation
- A query with cache hit ratio < 95% returns a `low_cache_hit` recommendation
- A query with mean exec time ≥ 500 ms returns a `high_mean_exec_time` recommendation
- A query with stddev/mean ≥ 0.5 (and mean ≥ 100 ms) returns `high_stddev`
- A query with Seq Scan and more disk reads than cache hits returns `missing_vacuum`
- Recommendations are sorted: `high` before `medium` before `low`
- If plan is `None`, Rules 1 and 5 are skipped; Rules 2–4 still fire

**Todo List**
1. Implement `_walk_plan(plan: list[dict]) -> list[dict]` — depth-first node
   flattening, unwrapping the top-level `"Plan"` key
2. Implement `_rule_seq_scan(nodes: list[dict]) -> FixRecommendation | None`
3. Implement `_rule_low_cache(row: dict) -> FixRecommendation | None`
4. Implement `_rule_high_mean(row: dict) -> FixRecommendation | None`
5. Implement `_rule_high_stddev(row: dict) -> FixRecommendation | None`
6. Implement `_rule_missing_vacuum(row: dict, nodes: list[dict]) -> FixRecommendation | None`
7. Implement `_analyse(row: dict, plan: list[dict] | None) -> list[FixRecommendation]` —
   calls all rules, filters `None` results, sorts by severity
8. Wire `_analyse` into the route handler — replace stub `[]` with
   `_analyse(row, plan)`

**Relevant Context**
- Recommendation Engine section above — exact trigger conditions, severities,
  titles, explanations, and SQL snippets
- Plan Node Traversal Helper section above — `_walk_plan` contract

---

### Sub-Task 3 — Backend tests

**Status**: `[ ] pending`

**Intent**
Write `backend/tests/test_fix.py` covering `_walk_plan`, each rule function,
`_analyse`, and the HTTP endpoint.

**Expected Outcomes**
- `_walk_plan` correctly flattens a 3-level plan tree
- Each rule function fires on its trigger condition and returns `None` below
  the threshold
- `_analyse` returns recommendations sorted by severity
- HTTP endpoint: connect fail → errors populated, empty recommendations
- HTTP endpoint: happy path (mocked connect + fetch + explain) → non-empty
  recommendations with correct `id` values
- HTTP endpoint: queryid not found → errors populated, empty recommendations
- `pytest` — all pass

**Todo List**
1. Create `backend/tests/test_fix.py`
2. Unit tests for `_walk_plan` (flat plan, nested plan, empty plan)
3. Unit tests for each of the 5 rule functions — trigger case and non-trigger
   case (10 tests total)
4. Unit test for `_analyse` — combined row + plan that triggers multiple rules;
   assert ordering
5. `httpx.AsyncClient` endpoint test: connect fail
6. `httpx.AsyncClient` endpoint test: happy path with mocked asyncpg — at
   least one recommendation returned
7. `httpx.AsyncClient` endpoint test: queryid not found
8. Run `pytest` — all pass

**Relevant Context**
- `backend/tests/test_explain.py` — `AsyncMock` + `patch` patterns to reuse
  directly; the mock EXPLAIN return value
  `[{"Plan": {"Node Type": "Seq Scan", "Startup Cost": 0.0, "Total Cost":
  100.0, "Plan Rows": 5000, "Relation Name": "orders"}}]` triggers Rule 1
- `backend/tests/test_queries.py` — `_make_fetch_row` helper for constructing
  mock stat rows

---

### Sub-Task 4 — Frontend types, API client, and hook

**Status**: `[ ] pending`

**Intent**
Create the TypeScript type definitions, the `fetchFix` API function, and the
`useFixWizard` hook. No UI yet.

**Expected Outcomes**
- `FixRecommendation` and `FixReport` TypeScript interfaces match the backend
  JSON schema exactly
- `fetchFix(dsn, queryid)` calls `GET /api/fix/{encoded}/{queryid}` and
  returns `Promise<FixReport>`
- `useFixWizard(dsn, queryid)` hook exposes `{ state, report }` and fetches
  on mount when both arguments are non-null

**Todo List**
1. Create `frontend/src/types/fix.ts`:
   - `FixError` interface
   - `FixRecommendation` interface (`id`, `title`, `severity`, `explanation`,
     `sql`)
   - `FixReport` interface — all fields from the API Contract section
2. Create `frontend/src/api/fix.ts`:
   - `fetchFix(dsn: string, queryid: string): Promise<FixReport>`
   - Uses `fetch('/api/fix/${encodeURIComponent(dsn)}/${queryid}')`
3. Create `frontend/src/hooks/useFixWizard.ts`:
   - `useFixWizard(dsn: string | null, queryid: string | null)`
   - Calls `fetchFix` in a `useEffect`; transitions
     `idle → loading → loaded | error`

**Relevant Context**
- `frontend/src/hooks/useQueryDetail.ts` — identical state machine to replicate
- `frontend/src/api/explain.ts` — identical fetch pattern to follow

---

### Sub-Task 5 — Frontend components and FixPage

**Status**: `[ ] pending`

**Intent**
Build `FixCard.tsx` and replace the `FixPage.tsx` stub with the full fix
wizard UI. Add a "Fix Wizard" navigation link from `QueryDetailPage`.

**Expected Outcomes**
- `FixPage` reads `:id` from route params, reads DSN from `localStorage`, and
  passes both to `useFixWizard`
- Loading spinner shown while fetching
- Loaded state shows fingerprint header, compact stats bar, and ordered list
  of `FixCard` components
- Empty recommendations renders a "No issues detected" message
- `FixCard` shows severity badge, title, explanation, and SQL block with a
  working Copy button
- `QueryDetailPage` has a "Fix Wizard →" button that navigates to
  `/fix/:queryid`
- `npm run lint` and `npm run build` pass

**Todo List**
1. Create `frontend/src/components/FixCard.tsx` — severity badge, explanation,
   SQL code block with Copy button
2. Replace `frontend/src/pages/FixPage.tsx` with the full implementation using
   `useFixWizard` and `FixCard`
3. Add a "Fix Wizard →" `<Link>` to `frontend/src/pages/QueryDetailPage.tsx`
   next to the back link — points to `/fix/${queryid}`
4. Run `npm run lint` and `npm run build` — fix any TypeScript/lint errors

**Relevant Context**
- `frontend/src/pages/QueryDetailPage.tsx:24-30` — `useParams`, DSN read, and
  back link patterns to replicate
- `frontend/src/components/QueryTable.tsx:7-18` — `ScoreBadge` colour logic to
  use for the stats bar score tile
- Tailwind dark theme: bg `#0f172a`, surfaces `slate-800`, borders `slate-700`,
  accents `indigo-500` — follow existing pages exactly
- Route `/fix/:id` already registered in `frontend/src/App.tsx:50`

---

## Test Strategy Summary

| Layer | Tool | What is covered |
|---|---|---|
| Unit — plan traversal | `pytest` | `_walk_plan` on flat, nested, empty plans |
| Unit — rule functions | `pytest` | Each rule: trigger + non-trigger (10 cases) |
| Unit — engine | `pytest` | `_analyse` multi-rule + severity sort |
| HTTP endpoint | `httpx.AsyncClient` | connect fail, happy path, queryid not found |
| Frontend types | TypeScript compiler | `FixReport` matches backend schema |

---

## Implementation Notes

1. **Import, don't copy** — `_step_connect`, `_step_fetch_query`,
   `_step_explain`, `_fingerprint`, and `_score_query` all already exist.
   `fix.py` imports them; it adds only the recommendation engine.

2. **Score with itself as max** — for a single-query fetch, `max_total_ms`
   equals the query's own `total_exec_time_ms`. This is the same convention
   used in `explain.py`. The resulting score is a standalone badness measure,
   not relative to other queries.

3. **Plan may be `None`** — `_step_explain` can fail (permission, syntax).
   `_analyse` must accept `plan=None` and simply skip the two plan-dependent
   rules. The stat-based rules (2, 3, 4) still run.

4. **Generic SQL snippets** — the `sql` field in each recommendation is a
   ready-to-copy snippet, but it will contain placeholder tokens like
   `<table>` and `<column>` where the actual identifiers cannot be inferred
   reliably from the fingerprint alone. Rule 1 substitutes `Relation Name`
   from the plan node when available; all other tokens remain as templates.

5. **`navigator.clipboard` requires HTTPS or localhost** — the Copy button in
   `FixCard.tsx` uses `navigator.clipboard.writeText`. In development (Vite on
   localhost) this works without any special config. No polyfill is needed.

6. **URL routing** — identical to Module 4: `/{connection_id:path}/{queryid}`
   with the DSN URL-encoded by the frontend. `unquote` is applied server-side
   before passing to asyncpg.

7. **No SSE in this module** — fix analysis is fast (two DB round trips at
   most) and the result set is small. A one-shot JSON endpoint is sufficient;
   an SSE stream would add complexity without meaningful UX benefit.

---

## Completed / Remaining Modules

### COMPLETED MODULES
- **Module 1** — Connection Testing (SSE-based, 4-step connection wizard)
- **Module 2** — Health Monitoring (7 checks, weighted score, SSE + JSON)
- **Module 3** — Slow Query Analysis (pg_stat_statements, fingerprinting, SSE + JSON)
- **Module 4** — Query Detail + EXPLAIN output

### THIS PLAN
- **Module 5** — Fix Wizard ← *this plan*

### REMAINING MODULES
- **Module 6** — Dashboard (aggregated metrics, trends over time)
