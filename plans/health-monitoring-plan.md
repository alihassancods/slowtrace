# Module 2 — Health Monitoring: Implementation Plan

## Top-Level Overview

Add a `/api/health/{connection_id}` surface to SlowTrace that evaluates the
"health" of a live PostgreSQL connection across seven diagnostic dimensions,
computes a weighted 0-100 score, and streams the result in real time via SSE.

The module sits entirely in the backend. It follows the exact conventions
established by Module 1 (`backend/src/api/connections.py`):

- Router in `backend/src/api/health.py`
- Exported in `backend/src/api/__init__.py`
- Registered in `backend/app/main.py`
- Raw SQL through `asyncpg`; no ORM
- `AsyncGenerator[str, None]` + `StreamingResponse` for SSE
- Status tokens: `"ok"` / `"warning"` / `"fail"`

`connection_id` is the URL-encoded connection string supplied by the caller
(the same DSN they already tested in Module 1). No persistent storage is
introduced in this module.

---

## File Tree

```
backend/
└── src/
    ├── api/
    │   ├── __init__.py          ← add health router export
    │   ├── connections.py       (unchanged)
    │   └── health.py            ← NEW — router + checks + score service + SSE
    └── db_engine/
        └── connector.py         (unchanged)

backend/
└── tests/
    └── test_health.py           ← NEW — unit + integration tests
```

`backend/app/main.py` — one-line addition to include the health router.

---

## API Contract

### `GET /api/health/{connection_id}`

One-shot JSON response (synchronous).

**Path parameter**
- `connection_id` — URL-encoded DSN string
  (`postgresql://user:pass@host:5432/db`)

**Success response `200 OK`**
```json
{
  "connection_id": "postgresql://...",
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
    },
    {
      "name": "cache_hit_ratio",
      "status": "warning",
      "value": 94.1,
      "unit": "percent",
      "threshold": 95.0,
      "message": "Cache hit ratio is 94.1% (target ≥ 95%)",
      "deduction": 5
    }
    // ... 5 more checks
  ],
  "deductions": [
    {"check": "cache_hit_ratio", "points": 5, "reason": "Cache hit ratio below 95%"}
  ],
  "errors": []
}
```

**Error response `200 OK`** (connection failed — errors array populated)
```json
{
  "connection_id": "postgresql://...",
  "score": null,
  "grade": null,
  "checks": [],
  "deductions": [],
  "errors": [
    {"check": "connect", "message": "Authentication failed: wrong password."}
  ]
}
```

---

### `GET /api/health/{connection_id}/stream`

SSE stream. Events are emitted as each check completes; a final `result`
event carries the full score object.

**Event format** (identical to Module 1)
```
data: {"step": "<check_name>", "status": "ok|warning|fail", "data": {...}}\n\n
```

**Check names streamed in order**
1. `connect`
2. `connections`
3. `cache_hit_ratio`
4. `replication_lag`
5. `table_bloat`
6. `lock_contention`
7. `long_transactions`
8. `index_usage`
9. `result` — final aggregated score object (same schema as JSON endpoint)

---

## Health Checks — Detail

### 1. `connections`
- Query: `SELECT count(*) FROM pg_stat_activity WHERE state != 'idle'`
  and `SHOW max_connections`
- Computes: active / max ratio
- Thresholds: warn ≥ 70 %, fail ≥ 90 %
- Max deduction: **10 pts**

### 2. `cache_hit_ratio`
- Query: `pg_statio_user_tables` sum of `heap_blks_hit` / (`heap_blks_hit` + `heap_blks_read`)
- Thresholds: warn < 95 %, fail < 80 %
- Max deduction: **15 pts**
- Requires: `SELECT` on `pg_catalog.pg_statio_user_tables` (usually granted)

### 3. `replication_lag`
- Query: `pg_stat_replication` — `write_lag`, `flush_lag`, `replay_lag`
  and `pg_wal_lsn_diff` for byte lag
- Handles: not a primary (no replicas) → status `"ok"`, deduction 0
- Thresholds: warn > 30 s, fail > 300 s
- Max deduction: **15 pts**
- Permission: requires `pg_monitor` role or superuser for lag columns

### 4. `table_bloat`
- Query: estimate from `pg_class` / `pg_stat_user_tables` (no `pgstattuple`)
  using `relpages` vs `pg_relation_size` ratio
- Reports top-5 bloated tables
- Thresholds: warn > 30 % wasted, fail > 60 % wasted (worst table)
- Max deduction: **10 pts**

### 5. `lock_contention`
- Query: `pg_locks` joined with `pg_stat_activity` — count waiting locks
- Thresholds: warn ≥ 1 waiting, fail ≥ 5 waiting
- Max deduction: **15 pts**

### 6. `long_transactions`
- Query: `pg_stat_activity WHERE state = 'idle in transaction'
  AND now() - xact_start > interval '5 minutes'`
- Thresholds: warn ≥ 1, fail ≥ 3
- Max deduction: **15 pts**

### 7. `index_usage`
- Query: `pg_stat_user_tables` — tables where `seq_scan > 0` and
  `seq_tup_read > 1000` and `idx_scan / (seq_scan + idx_scan) < 0.5`
- Reports top-5 under-indexed tables
- Thresholds: warn ≥ 1 table, fail ≥ 3 tables
- Max deduction: **20 pts**

---

## Health Score Service

### Scoring Algorithm

```
base_score = 100
for each check:
    if check.status == "fail":
        base_score -= check.weight
    elif check.status == "warning":
        base_score -= check.weight * 0.5
score = max(0, base_score)
```

### Weights (sum = 100 pts)

| Check              | Weight |
|--------------------|--------|
| connections        | 10     |
| cache_hit_ratio    | 15     |
| replication_lag    | 15     |
| table_bloat        | 10     |
| lock_contention    | 15     |
| long_transactions  | 15     |
| index_usage        | 20     |

### Grade Mapping

| Score  | Grade |
|--------|-------|
| 90-100 | A     |
| 75-89  | B     |
| 60-74  | C     |
| 45-59  | D     |
| 0-44   | F     |

### Deduction Explanation

Each check that reduces the score produces a `deduction` record:
```json
{"check": "index_usage", "points": 10, "reason": "3 tables rely heavily on seq scans"}
```

---

## Error Handling

| Scenario                         | Behaviour                                                    |
|----------------------------------|--------------------------------------------------------------|
| Connection failure               | Emit `connect` fail event; stream `result` with `score=null` |
| `InsufficientPrivilegeError`     | Check status `"warning"`; message includes `GRANT` hint     |
| Missing `pg_stat_statements`     | Noted in `errors[]`; does not block other checks            |
| `pg_stat_replication` empty      | Not a primary — check passes as `"ok"`                      |
| Any unexpected `PostgresError`   | Check status `"fail"`; full message in `data.message`       |
| Timeout (per-check 10 s)         | Check status `"fail"`; `"message": "Check timed out"`       |

---

## Sub-Tasks

---

### Sub-Task 1 — Router scaffold & `connect` check

**Status**: `[ ] pending`

**Intent**
Create `backend/src/api/health.py` with the router prefix, SSE helpers, and
the `connect` step that opens a connection using `asyncpg.connect`. Wire the
router into `__init__.py` and `main.py`. This makes both endpoints reachable
(returning partial/empty responses until checks are added).

**Expected Outcomes**
- `GET /api/health/{connection_id}` returns `200` JSON
- `GET /api/health/{connection_id}/stream` returns `text/event-stream`
- A bad DSN yields a `connect` fail event and `score=null`
- All existing Module 1 tests still pass

**Todo List**
1. Create `backend/src/api/health.py`:
   - `router = APIRouter(prefix="/api/health")`
   - Pydantic response models: `CheckResult`, `Deduction`, `HealthReport`
   - `_event()` SSE helper (same signature as connections.py)
   - `_check_connect()` async function — opens connection, returns
     `(conn | None, status, data)`
   - `_run_health_stream()` async generator — calls connect step, yields event,
     aborts with `result` if conn is None
   - `GET /{connection_id}` endpoint — runs all checks (stub), returns JSON
   - `GET /{connection_id}/stream` endpoint — returns `StreamingResponse`
2. Add `from api.health import router as health_router` to
   `backend/src/api/__init__.py` (if the file is used) and
   `backend/app/main.py`
3. Smoke-test manually with a valid and invalid DSN

**Relevant Context**
- `backend/src/api/connections.py` — exact pattern to follow for SSE
- `backend/app/main.py:12-16` — how to register a router
- `backend/src/db_engine/connector.py` — `DBConnector` available but Module 1
  uses raw `asyncpg.connect()`; health module can do the same to avoid
  managing a pool lifecycle per request

---

### Sub-Task 2 — Implement checks 1–4

**Status**: `[ ] pending`

**Intent**
Add the first four diagnostic checks to the health generator:
`connections`, `cache_hit_ratio`, `replication_lag`, `table_bloat`.

**Expected Outcomes**
- Each check is an isolated `async def _check_<name>(conn) -> tuple[str, dict]`
- Correct SQL queries per the API Contract section above
- Correct threshold logic and status assignment
- Each check is `yield`ed as an SSE event before the next begins
- Timeout wrapper (10 s) applied to every check

**Todo List**
1. Implement `_check_connections(conn)` — ratio of active to max_connections
2. Implement `_check_cache_hit_ratio(conn)` — `pg_statio_user_tables`
3. Implement `_check_replication_lag(conn)` — `pg_stat_replication`; handle
   no-replica case
4. Implement `_check_table_bloat(conn)` — `pg_class` estimate, top-5 tables
5. Add a `_with_timeout(coro, seconds=10)` helper that wraps any check in
   `asyncio.wait_for` and returns `("fail", {"message": "Check timed out"})`
   on `asyncio.TimeoutError`
6. Wire all four into `_run_health_stream()` after the connect step
7. Wire all four into the one-shot JSON endpoint

**Relevant Context**
- API Contract → Health Checks — Detail section (queries + thresholds)
- `backend/src/api/connections.py:43-62` — connect step pattern
- Replication lag: if `pg_stat_replication` returns zero rows the DB is not a
  primary; return `"ok"` with `{"message": "No replicas configured"}`
- `pg_stat_replication` lag columns require `pg_monitor` role; catch
  `asyncpg.InsufficientPrivilegeError`

---

### Sub-Task 3 — Implement checks 5–7

**Status**: `[ ] pending`

**Intent**
Add the remaining three checks: `lock_contention`, `long_transactions`,
`index_usage`.

**Expected Outcomes**
- Same isolation pattern as Sub-Task 2
- `lock_contention` surfaces waiting PID count and a list of blocked queries
- `long_transactions` surfaces duration and query text of oldest idle-in-txn
- `index_usage` surfaces top-5 tables sorted by seq_tup_read descending

**Todo List**
1. Implement `_check_lock_contention(conn)`
2. Implement `_check_long_transactions(conn)`
3. Implement `_check_index_usage(conn)`
4. Wire all three into the generator and JSON endpoint

**Relevant Context**
- API Contract → Health Checks — Detail section
- `pg_locks` joined with `pg_stat_activity` for lock contention
- `long_transactions` query: `state = 'idle in transaction'` +
  `xact_start` age > 5 minutes
- `index_usage`: tables with `seq_tup_read > 1000` and
  `idx_scan / (idx_scan + seq_scan) < 0.5`

---

### Sub-Task 4 — Health Score Service

**Status**: `[ ] pending`

**Intent**
Implement `_compute_score(checks: list[CheckResult]) -> tuple[int, str, list[Deduction]]`
that applies the weighted scoring algorithm and produces the grade and
deduction list.

**Expected Outcomes**
- Score is clamped to [0, 100]
- Grade is determined by the grade mapping table
- Each non-`"ok"` check produces a `Deduction` record with a human-readable
  `reason`
- `_run_health_stream()` calls `_compute_score` before emitting the final
  `result` event
- One-shot JSON endpoint also uses `_compute_score`

**Todo List**
1. Define `WEIGHTS: dict[str, int]` constant (7 checks, sums to 100)
2. Implement `_grade(score: int) -> str`
3. Implement `_compute_score(checks)` — returns `(score, grade, deductions)`
4. Emit final `result` SSE event with the full `HealthReport` payload
5. Return `HealthReport` from the JSON endpoint

**Relevant Context**
- API Contract → Health Score Service section
- `HealthReport` Pydantic model defined in Sub-Task 1

---

### Sub-Task 5 — Tests

**Status**: `[ ] pending`

**Intent**
Write `backend/tests/test_health.py` covering the score service and the
endpoints with mocked DB connections.

**Expected Outcomes**
- Score service unit tests: all `"ok"` → 100; all `"fail"` → 0; mixed → correct
- Grade boundary tests
- Endpoint tests via `httpx.AsyncClient` + `AsyncMock` for `asyncpg.connect`
- SSE stream test: collect all events and assert `result` event is last
- Each error scenario (bad password, timeout, insufficient privilege, missing
  pg_stat_statements) produces the expected event

**Todo List**
1. Set up `pytest-asyncio` `asyncio_mode = "auto"` in `pyproject.toml`
   (if not already set)
2. Create `backend/tests/conftest.py` with a mock `asyncpg.Connection`
3. Unit tests for `_compute_score` and `_grade`
4. Integration-style tests for each check function using a mock connection
5. End-to-end stream test: mock `asyncpg.connect` → collect SSE events →
   assert count, order, and final `result` shape
6. Error-path tests: connect fail, timeout, `InsufficientPrivilegeError`
7. Run `pytest` — all tests pass

**Relevant Context**
- `backend/requirements-dev.txt` — `pytest-asyncio` already installed
- `backend/src/api/connections.py` — the existing router has no tests yet;
  this module sets the testing baseline
- `httpx.AsyncClient(app=app, base_url="http://test")` for FastAPI testing

---

## Test Strategy Summary

| Layer                | Tool                            | What is covered                                      |
|----------------------|---------------------------------|------------------------------------------------------|
| Unit                 | `pytest` + `pytest-asyncio`     | Score algorithm, grade boundaries, deduction logic   |
| Check functions      | `pytest` + `asyncpg` mock       | Each of the 7 SQL checks: ok / warning / fail paths  |
| HTTP endpoints       | `httpx.AsyncClient`             | JSON endpoint shape and status codes                 |
| SSE stream           | `httpx.AsyncClient` streaming   | Event order, final result shape, early-exit on fail  |
| Error scenarios      | `asyncpg` exception mocks       | Auth failure, timeout, privilege error, no extension |

---

## Implementation Notes

1. **No persistent storage** — `connection_id` is the raw DSN; no database
   table is created.
2. **`asyncpg.connect()` not `DBConnector`** — each health request opens a
   single direct connection (same as Module 1), avoids pool lifecycle
   complexity.
3. **`pg_stat_statements` missing** — the check adds a warning to `errors[]`
   but does not block other checks (unlike Module 1 which aborts the stream).
4. **Per-check timeout** — 10-second `asyncio.wait_for` wrapper prevents a
   single slow query from stalling the stream.
5. **URL encoding** — `connection_id` in the path must be URL-decoded before
   passing to asyncpg; use `urllib.parse.unquote`.

---

## Completed / Remaining Modules

### COMPLETED MODULES
- **Module 1** — Connection Testing (SSE-based, 4-step connection wizard)

### REMAINING MODULES
- **Module 2** — Health Monitoring ← *this plan*
- **Module 3** — Slow Query Analysis (pg_stat_statements, query fingerprinting)
- **Module 4** — Query Detail + EXPLAIN output
- **Module 5** — Fix Wizard (guided remediation per query)
- **Module 6** — Dashboard (aggregated metrics, trends over time)
