# SlowTrace

**SlowTrace** is a PostgreSQL performance monitoring and optimization tool. It connects to your database, scans slow queries via `pg_stat_statements`, explains execution plans, scores query badness, and walks you through actionable fixes — all from a React dashboard.

---

## Features

### 🔍 Slow Query Scanner
Connects to any PostgreSQL instance and pulls the top 200 slowest queries from `pg_stat_statements`. Each query is fingerprinted (literals stripped), scored on a 0–100 badness scale, and returned ranked by priority.

- **Scoring algorithm** — weighted across mean execution time, total execution time, cache hit ratio, and execution time variability
- **Live SSE stream** — `GET /api/queries/{dsn}/stream` pushes step-by-step progress (connect → check extension → check permissions → fetch queries) as Server-Sent Events
- **One-shot JSON** — `GET /api/queries/{dsn}` returns the full `QueryReport` in a single response

### 🩺 Database Health Monitor
Runs 8 independent health checks against your PostgreSQL instance and produces a scored health report (0–100) with a letter grade.

| Check | What it measures |
|---|---|
| Connection | Latency and basic reachability |
| Active connections | % of `max_connections` in use |
| Cache hit ratio | Shared buffer efficiency |
| Replication lag | Replica behind primary (seconds) |
| Table bloat | Dead tuple ratio per table |
| Lock contention | Blocked queries count |
| Long transactions | Transactions running > 5 minutes |
| Index usage | Tables with low index scan ratio |

- **Live SSE stream** — `GET /api/health/{dsn}/stream`
- **One-shot JSON** — `GET /api/health/{dsn}`

### 📋 Query Detail & EXPLAIN
Fetches full stats for a single query by `queryid` and runs `EXPLAIN (ANALYZE false, BUFFERS, FORMAT JSON)` to return a structured execution plan tree.

- `GET /api/explain/{dsn}/{queryid}` — stats + plan in one response
- Frontend renders the plan as an interactive tree via `PlanTree` component

### 🔧 Fix Wizard
For any slow query, runs 5 automated recommendation rules against its stats and EXPLAIN plan, then returns ranked, actionable fix recommendations with ready-to-run SQL.

| Rule | Trigger |
|---|---|
| Sequential scan on large table | `Seq Scan` node with ≥ 1,000 plan rows |
| Low buffer-cache hit ratio | Cache hit < 95% |
| High mean execution time | Mean > 500 ms |
| High execution time variability | stddev / mean > 50% |
| Table needs VACUUM ANALYZE | Seq scan + more disk reads than cache hits |

- `GET /api/fix/{dsn}/{queryid}` — returns a `FixReport` with scored, severity-ranked recommendations

### 🧪 Planner-Validated Index Fixes

Before suggesting a `CREATE INDEX`, the AI fix generator can *prove* it with [HypoPG](https://github.com/HypoPG/hypopg): a hypothetical index is created in the same session, the planner costs the query with and without it, then the index is dropped. Nothing is written to disk and nothing is visible to other sessions.

- `POST /api/fixes/generate` returns `hypopg_validation` with baseline/improved planner cost, scan-type change, and estimated index size
- When the planner confirms the index, Expected Impact is derived from the measured cost ratio instead of a fixed guess (`estimation_basis: "hypopg_planner"`)
- pg_stat_statements stores parameters as `$1`, and the planner proves `col = NULL` as a 0-cost no-op, so each `$n` is replaced with a value sampled from your own table (`TABLESAMPLE`, never the first physical row) and the index is tested against 3 such values — the verdict is the median, with the observed range and the exact values shown
- Degrades silently: without the extension the suggestion still works and the UI says the improvement is estimated
- Optional — install `hypopg` on the monitored database, add it to `shared_preload_libraries`, restart, then `CREATE EXTENSION hypopg;` (SlowTrace also tries to create it when the user has permission)

### 📊 Dashboard
Aggregates health and query data in a single parallel request — fans out to both the health and queries streams concurrently and returns a unified `DashboardReport`.

- Top 10 queries by badness score
- High-priority query count (score ≥ 70)
- Total execution time across all tracked queries
- Average badness score
- `GET /api/dashboard/{dsn}`

### 🔌 Connection Manager
Stores and manages named database connections.

- `GET /api/connections` — list saved connections
- `POST /api/connections` — add a connection
- `DELETE /api/connections/{id}` — remove a connection

---

## Project Structure

```
slowtrace/
├── backend/
│   ├── app/
│   │   └── main.py           # FastAPI entry point
│   ├── src/
│   │   └── api/
│   │       ├── connections.py # Connection manager
│   │       ├── dashboard.py   # Aggregated dashboard endpoint
│   │       ├── explain.py     # Query detail + EXPLAIN plan
│   │       ├── fix.py         # Fix wizard recommendations
│   │       ├── health.py      # Database health checks (SSE + JSON)
│   │       └── queries.py     # Slow query scanner (SSE + JSON)
│   └── tests/
│       ├── test_dashboard.py
│       ├── test_explain.py
│       ├── test_fix.py
│       ├── test_health.py
│       └── test_queries.py
├── frontend/
│   └── src/
│       ├── api/               # Typed fetch wrappers
│       ├── components/        # FixCard, PlanTree, QueryTable, ScanProgress
│       ├── hooks/             # useDashboard, useFixWizard, useQueryDetail, useQueryScan
│       ├── pages/             # WelcomePage, ScanPage, DashboardPage, QueryDetailPage, FixPage, WizardPage
│       └── types/             # Pydantic-mirrored TypeScript types
├── docs/                      # Per-module documentation
└── docker-compose.yml
```

---

## Getting Started

### Prerequisites

- Python 3.11+
- Node.js 20+
- Docker & Docker Compose
- Git — **must be installed on the host**; the codebase scanner clones repositories with it
- PostgreSQL with `pg_stat_statements` enabled
- Optional: `hypopg` on the monitored database, for planner-proven index suggestions

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
uvicorn app.main:app --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

### Local Database

```bash
docker compose up -d
```

### Deployment notes

- **System requirements: `git` and Python 3.11+ must be installed on the host.**
  Codebase scanning clones repositories with `git`; `docker-compose.yml` only
  provisions PostgreSQL, so the backend runs directly on the host (or in an image
  that installs git itself).
- Codebase scanning uses [Semgrep](https://semgrep.dev), installed as a Python
  dependency from `backend/requirements.txt`. Its CLI lands in the virtualenv
  (`backend/.venv/bin/semgrep`), **not** on the system `PATH`, and is resolved
  relative to the running interpreter. If it is missing, `/api/codebase/scan`
  returns a `semgrep_not_installed` error with an `install_command` to run:

  ```bash
  cd backend && uv pip install --python .venv/bin/python "semgrep>=1.70.0"
  ```

- Rules live in `backend/src/agent/codebase/rules/slowtrace-rules.yml`; the
  scanner contract and verified Semgrep behaviour are documented in
  [docs/backend/semgrep.md](docs/backend/semgrep.md).

---

## Development

| Command | Description |
|---|---|
| `uvicorn app.main:app --reload` | Start backend dev server (port 8000) |
| `npm run dev` | Start frontend dev server (port 5173) |
| `pytest` | Run all backend tests |
| `pytest tests/path/test_file.py::test_name` | Run a single backend test |
| `npm test` | Run frontend tests (watch) |
| `npm run test:run` | Run frontend tests (once) |
| `npm run lint` | Lint frontend |
| `npm run build` | Production build |

---

## API Overview

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/queries/{dsn}` | Slow query report (JSON) |
| `GET` | `/api/queries/{dsn}/stream` | Slow query scan (SSE) |
| `GET` | `/api/health/{dsn}` | Health report (JSON) |
| `GET` | `/api/health/{dsn}/stream` | Health checks (SSE) |
| `GET` | `/api/explain/{dsn}/{queryid}` | Query stats + EXPLAIN plan |
| `GET` | `/api/fix/{dsn}/{queryid}` | Fix recommendations |
| `GET` | `/api/dashboard/{dsn}` | Aggregated dashboard |
| `GET` | `/api/connections` | List saved connections |
| `POST` | `/api/connections` | Save a connection |
| `DELETE` | `/api/connections/{id}` | Delete a connection |
