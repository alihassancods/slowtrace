# SlowTrace

**SlowTrace turns PostgreSQL performance signals into an actionable investigation: database health and slow-query evidence, SQLCommenter code locations, codebase findings, and reviewable fixes.**

> **Implementation status:** SlowTrace currently contains the product workflow and an optional AI-assisted fix path. IBM Bob 2.0 is documented below as the development and task-session tool used around this repository; there is no IBM Bob runtime API or service integration in the application code.

## The Problem

Diagnosing a slow PostgreSQL query usually means moving between `pg_stat_statements`, `EXPLAIN`, database health views, SQL logs, ORM/application code, and a development environment. The database can show *which* query is expensive, while the application owns the code path that generated it. Without trace metadata or code scanning, the developer must manually connect those pieces and decide whether the problem is an index, an N+1 pattern, unbounded results, cache pressure, locks, or stale statistics.

## The Solution

SlowTrace provides a browser UI and FastAPI backend for a PostgreSQL investigation workflow:

- Registers a PostgreSQL connection and validates the DSN format.
- Streams a database scan over Server-Sent Events (SSE): metadata, eight health checks, slow-query summary, and a final score with quick wins.
- Reads up to 200 statements from `pg_stat_statements`, fingerprints literals, and computes a 0–100 priority score from mean time, total time, cache hits, and variability.
- Runs `EXPLAIN (ANALYZE false, BUFFERS, FORMAT JSON)` for a selected query and renders the plan tree.
- Parses SQLCommenter and Rails Marginalia comments such as `file='app/orders.py',line='47'` to expose a `file:line` code location.
- Scans a GitHub repository by shallow-cloning it and running the checked-in Semgrep ruleset for database-performance and SQL-construction smells.
- Correlates Semgrep findings with SQLCommenter-traced slow queries by file.
- Generates deterministic fix recommendations from query statistics and plans, and separately provides an AI-assisted fix generator with optional DeepSeek/OpenAI-compatible explanations.
- Optionally validates hypothetical index impact with HypoPG, applies a saved fix, measures health before and after, and automatically rolls back when the health score drops by more than 10 points.

The codebase scanner can find patterns in Python, JavaScript, and TypeScript repositories. It does not retrieve arbitrary source snippets from GitHub for a query location; `SQLCommentParser.get_code_snippet()` currently returns an explicit “not configured” response.

## How It Works

```text
PostgreSQL
  │
  │ pg_stat_statements, health views, EXPLAIN, SQLCommenter metadata
  ▼
SlowTrace
  │
  ├── scan + score slow queries
  ├── show plan and file:line context
  ├── scan application repository with Semgrep
  └── correlate code findings with traced queries
  │
  ▼
Application/code context
  │
  ▼
Fix workflow
  ├── deterministic recommendations (/api/fix)
  └── AI-assisted fix generation (/api/fixes/generate)
  │
  ▼
Review → optional HypoPG planner check → explicit apply
  │
  ▼
Verification: health/query impact response, history, rollback or auto-rollback
```

## IBM Bob 2.0 Integration

IBM Bob 2.0 is important to the **development and task workflow** represented by this repository, but it is not currently a runtime dependency of SlowTrace.

### What is implemented in the product

The implemented product workflow supplies a fix assistant with:

- Query text and `pg_stat_statements` metrics: calls, mean/total execution time, standard deviation, rows per call, and buffer hit/read counts.
- PostgreSQL `EXPLAIN` plan data, including scan type, estimated rows, relation name, and costs.
- SQLCommenter/Marginalia file and line metadata when the application emits it.
- Semgrep findings from a shallow clone of a GitHub repository, including rule ID, smell type, source path, line range, source excerpt, message, severity, and optional fix code.

The deterministic fix route (`backend/src/api/fix.py`) evaluates five rules: large sequential scans, low cache hit ratio, high mean execution time, high variability, and possible missing `VACUUM ANALYZE`. The AI-assisted route is implemented in `backend/src/agent/autofix/fix_generator.py`, `backend/src/services/ai_explainer.py`, and the `/api/fixes/*` routes. Its optional language explanation uses the DeepSeek OpenAI-compatible API, not IBM Bob.

When a user explicitly applies a generated fix, `backend/src/agent/autofix/fix_executor.py` records health before the change, executes the SQL, waits for index progress when relevant, measures health afterward, persists fix history, and can roll back or automatically reverse a harmful change. HypoPG validation is implemented in `backend/src/agent/autofix/hypopg_validator.py` and is surfaced in the Fix page when available.

### What Bob contributes

The repository currently provides no code or configuration that calls IBM Bob, an IBM Bob API, or an IBM Bob model at runtime. Therefore this README does **not** claim that Bob investigates a live query, edits application files, runs the product's tests, or verifies a production performance improvement. Those are intended hackathon workflow claims, not implemented runtime integrations in the current tree.

For the hackathon, Bob can be demonstrated as the environment/task agent used to inspect this repository, implement or review the workflow, and produce the checked-in evidence artifacts. The product itself remains responsible for database analysis, Semgrep scanning, fix generation, optional HypoPG validation, application, and verification.

## Demo Workflow

The currently runnable demonstration is:

1. Start PostgreSQL with Docker Compose.
2. Load `database/init.sql`, which creates the demo schema, generates dummy rows, enables `pg_stat_statements`, and runs `ANALYZE`.
3. Run `database/slow_queries.sql` several times to create workload data in `pg_stat_statements`.
4. Start the FastAPI backend and Vite frontend.
5. Register the local PostgreSQL DSN from the SlowTrace home page.
6. Run **Database Scan** to stream metadata, eight health checks, and the slow-query summary.
7. Open a query from the dashboard to inspect SQL, execution statistics, and its `EXPLAIN` plan.
8. If the query contains SQLCommenter/Marginalia metadata, inspect its parsed file and line location. Otherwise use the Code Tracing Wizard to see setup instructions.
9. Use **Codebase Analysis** with a GitHub repository URL to shallow-clone it and stream Semgrep findings. If a connection is selected, matching traced queries are shown as best-effort same-file correlations.
10. Open **AI Fix Suggestion** for a query. Review the generated SQL, optional HypoPG validation, impact estimate, and safety information before applying it.
11. Confirm the change. SlowTrace reports health/query impact and supports rollback when executable rollback SQL exists.

## Architecture

```mermaid
flowchart LR
    PG[(PostgreSQL 16\npg_stat_statements + health views)]
    DB[(Docker Compose\nlocal demo database)]
    UI[React 18 + Vite + TypeScript\nDashboard / Scan / Query / Fix / Codebase]
    API[FastAPI\nbackend/src/api/main.py]
    QUERY[Query and health APIs\nSSE + JSON]
    TRACE[SQLCommentParser\nSQLCommenter / Marginalia]
    SEM[SemgrepScanner\nshallow clone + YAML rules]
    FIX[FixGenerator + AIExplainer\noptional external DeepSeek API]
    HYPO[HypoPG validator\noptional on monitored DB]
    EXEC[FixExecutor\napply, measure, rollback]
    HIST[(data/fix_history.json\ncreated when fixes are saved)]
    REPO[GitHub repository\nscan target]

    DB --> PG
    UI <-->|HTTP / SSE| API
    API --> QUERY
    QUERY --> PG
    QUERY --> TRACE
    UI -->|POST /api/codebase/scan| SEM
    SEM -->|git clone| REPO
    SEM -->|findings + source locations| UI
    TRACE -->|file:line + same-file correlation| UI
    UI --> FIX
    FIX --> PG
    FIX -. optional explanation .-> DS[DeepSeek API]
    FIX --> HYPO
    HYPO --> PG
    UI --> EXEC
    EXEC --> PG
    EXEC --> HIST
```

## Example

The checked-in workload contains this intentionally unindexed query:

```sql
SELECT category, COUNT(*), AVG(price)
FROM products
WHERE category = 'Electronics'
GROUP BY category;
```

The demo schema deliberately does not add an index on `products.category`. After the workload is run, SlowTrace can find the statement through `pg_stat_statements`, fingerprint it by replacing literals, show its execution statistics and JSON `EXPLAIN` plan, and—when the plan reports a large sequential scan—return a high-severity recommendation with a `CREATE INDEX CONCURRENTLY` template. The exact before/after runtime is environment-dependent and is not asserted here.

## Benchmarking

The repository includes a synthetic workload and automated tests, but it does not include a controlled, reproducible before/after performance benchmark with recorded results. `database/init.sql` and `database/slow_queries.sql` provide the repeatable demo setup; `backend/tests/` covers API, scanner, fix, HypoPG, and related behavior. Performance claims should be measured on a fixed PostgreSQL instance by recording query timing and plan changes before and after a fix.

The checked-in security audit artifact reports **329 / 329 tests passed** for the audit run and documents Semgrep verification/live-scan observations. Those are test/audit records, not a SlowTrace query-speed benchmark.

## Bob Task Sessions

`bob_sessions/` currently contains only `.gitkeep`; no Bob screenshots, transcripts, task exports, or session-specific evidence are checked in. The hackathon evidence package still needs to be added if required. In particular, the repository does not currently document which Bob session investigated a query, what context was supplied, what code change Bob produced, or how Bob participated in test/performance verification.

## Dataset

The demo uses a **synthetic/generated PostgreSQL dataset** defined in `database/init.sql`:

- `users`: 1,000 generated rows
- `products`: 5,000 generated rows
- `orders`: 10,000 generated rows
- `order_items`: 25,000 generated rows
- `audit_logs`: 20,000 generated JSONB records

Rows are generated with PostgreSQL `generate_series()` and `random()`. The schema includes dummy usernames, example email addresses, generated product/order data, and non-real audit payloads. It is safe for a hackathon demo because it is generated locally and contains no checked-in production data or secrets. The workload queries are in `database/slow_queries.sql` and are designed to exercise sequential scans, joins, JSONB filtering, and repeated scans.

Loading the dataset resets the demo tables because `init.sql` drops them before recreating them.

## Tech Stack

- Python 3.11+; FastAPI; Uvicorn
- `asyncpg` for PostgreSQL access
- Pydantic and `python-dotenv`
- Semgrep 1.70+ for repository scanning
- React 18; TypeScript; Vite; React Router
- Tailwind CSS via `@tailwindcss/vite`
- Vitest and ESLint
- PostgreSQL 16 Alpine via Docker Compose
- Optional: HypoPG on the monitored PostgreSQL instance
- Optional: DeepSeek OpenAI-compatible API for AI-generated plain-English explanations

## Running Locally

### Prerequisites

- Python 3.11+
- Node.js 20+
- Docker and Docker Compose
- Git on the host, required by the codebase scanner
- A PostgreSQL instance with `pg_stat_statements` enabled; the included Compose service provides PostgreSQL 16
- Optional: HypoPG installed on the monitored database for hypothetical-index validation

### 1. Start PostgreSQL

From the repository root:

```bash
docker compose up -d
```

Load the generated schema and data:

```bash
docker compose exec -T postgres psql -U slowtrace -d slowtrace < database/init.sql
docker compose exec -T postgres psql -U slowtrace -d slowtrace < database/slow_queries.sql
```

Run the workload more than once if you want more useful accumulated statistics.

### 2. Configure and start the backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
uvicorn app.main:app --reload
```

Set `DATABASE_URL` in `backend/.env` for the application’s configured database. The included local value is:

```dotenv
DATABASE_URL=postgresql://slowtrace:slowtrace@localhost:5432/slowtrace
```

The optional `DEEPSEEK_API_KEY` or `OPENAI_API_KEY` enables external AI explanations in `backend/src/services/ai_explainer.py`. Do not commit secrets. `WATSONX_API_KEY` appears in `.env.example`, but no current source file was found that uses it.

For codebase scanning, install Semgrep in the backend environment if it is not installed by the requirements setup:

```bash
uv pip install --python .venv/bin/python "semgrep>=1.70.0"
```

### 3. Start the frontend

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. Vite proxies `/api` requests to `http://localhost:8000`.

### Tests and checks

```bash
cd backend
pytest

cd ../frontend
npm run test:run
npm run lint
npm run build
```

## Project Structure

```text
slowtrace/
├── backend/
│   ├── app/main.py                 # Uvicorn entry point; re-exports the FastAPI app
│   ├── src/api/                    # FastAPI routers and database workflows
│   │   ├── main.py                # App and router registration
│   │   ├── scan.py                # 11-event database scan SSE workflow
│   │   ├── queries.py              # pg_stat_statements, fingerprinting, scoring
│   │   ├── explain.py              # Query stats + JSON EXPLAIN
│   │   ├── fix.py                 # Deterministic plan/stat recommendations
│   │   ├── codebase.py             # Semgrep scan stream, cache, correlations
│   │   └── fixes.py                # AI fix generation/apply/history routes
│   ├── src/agent/
│   │   ├── code_linker/           # SQLCommenter/Marginalia parser
│   │   ├── codebase/               # Semgrep scanner and 12-rule YAML ruleset
│   │   └── autofix/                # Fix generation, HypoPG, apply/rollback
│   ├── src/services/ai_explainer.py # Optional DeepSeek/OpenAI-compatible explanations
│   └── tests/                      # Backend unit and API tests
├── frontend/src/
│   ├── api/                       # Typed fetch and SSE clients
│   ├── components/                # Query, plan, fix, scan, and codebase UI
│   ├── hooks/                     # Query, dashboard, scan, and fix state
│   ├── pages/                     # Scan, dashboard, query, wizard, fix, codebase pages
│   └── types/                     # TypeScript API models
├── database/
│   ├── init.sql                   # Synthetic schema and generated data
│   └── slow_queries.sql           # Reproducible slow-query workload
├── docs/                          # Backend/frontend implementation notes
├── plans/                         # Historical implementation plans
├── security_report/               # Checked-in security audit artifact
├── bob_sessions/                  # Reserved for Bob evidence; currently empty
└── docker-compose.yml              # PostgreSQL 16 development service
```

## Limitations

- IBM Bob is not a runtime integration in the current application.
- `bob_sessions/` has no screenshots or session exports yet.
- No controlled performance benchmark results are checked in.
- SQLCommenter/Marginalia tracing requires instrumentation in the monitored application; SlowTrace does not inject it automatically.
- Source-code snippet retrieval for a parsed `file:line` is not implemented; the current parser exposes location metadata and returns a placeholder for snippet lookup.
- Codebase scan results and saved fix history are process-local/file-based rather than backed by a durable application database.
- Semgrep repository scanning requires Git, a reachable GitHub repository, and Semgrep in the backend virtual environment. Private repositories require a GitHub token.
- HypoPG validation is optional and only applies to supported hypothetical-index cases; otherwise impact remains an estimate.
- The AI explanation service is optional and depends on an external DeepSeek/OpenAI-compatible API. Deterministic fallback explanations are used when it is unavailable.
- The included Docker Compose file provisions PostgreSQL only; it does not containerize the backend or frontend.

## Future Work

These are extensions, not current capabilities:

- Add checked-in IBM Bob task/session artifacts that show investigation, code changes, testing, and verification.
- Add a first-class Bob-facing product workflow or documented handoff format for query evidence and application-code context.
- Retrieve and display real source snippets around traced locations, with repository/ref validation.
- Add durable storage and authentication for connections, scan results, and fix history.
- Add a repeatable benchmark harness that records before/after execution times and plans under fixed data and hardware conditions.
- Expand correlation beyond same-file matching to query fingerprints, call sites, and framework-specific metadata.
- Add CI coverage for the frontend and a deployment configuration for the complete stack.

## Hackathon Submission

- **IBM Bob 2.0 usage:** Bob task/session evidence is not yet checked in. The current repository documents the intended development workflow but does not claim runtime Bob integration.
- **Repository:** [alihassancods/slowtrace](https://github.com/alihassancods/slowtrace)
- **Demo:** The local database, backend, frontend, generated dataset, and workload commands above are available in the repository.
- **Video:** No video link is currently present in the repository.
- **Screenshots/evidence:** `bob_sessions/` currently contains only `.gitkeep`; screenshots and session exports still need to be added.

## Evidence intentionally not claimed

The current implementation does not provide evidence for the following claims, so they are intentionally excluded from the product description:

- IBM Bob calling SlowTrace at runtime or acting as the shipped query-analysis engine.
- Bob independently tracing a production query, modifying an application repository, running the repository’s tests, or verifying a measured performance improvement.
- A stored set of Bob task sessions, screenshots, or transcripts.
- A fixed numeric query-speed improvement, benchmark result, or production outcome.
- Automatic retrieval of source code snippets from the reported file and line.
- Watsonx integration: `WATSONX_API_KEY` is present in `.env.example`, but no current implementation uses it.
- A public demo deployment or submission video.

These omissions are deliberate: the README describes the code that is present, separates estimates from measurements, and identifies the evidence still required for a complete IBM Bob 2.0 Hackathon submission.
