# SlowTrace Documentation

SlowTrace is a PostgreSQL slow-query analysis tool. It connects to any Postgres database, reads [`pg_stat_statements`](https://www.postgresql.org/docs/current/pgstatstatements.html), scores every query by badness, generates an EXPLAIN plan, and produces ranked fix recommendations — all surfaced in a React SPA.

---

## Architecture overview

```
┌─────────────────────┐          HTTP / SSE          ┌─────────────────────┐
│   React frontend    │  ───────────────────────────▶ │  FastAPI backend    │
│  (Vite · TypeScript)│  ◀───────────────────────────  (Python 3.11+)      │
└─────────────────────┘                               └──────────┬──────────┘
                                                                  │ asyncpg
                                                       ┌──────────▼──────────┐
                                                       │    PostgreSQL DB     │
                                                       └─────────────────────┘
```

The frontend proxies all `/api/*` requests to the backend (Vite dev server), so no CORS config is needed during development.

---

## Documentation map

### Backend

| Module | Description |
|--------|-------------|
| [Application entry point](backend/main.md) | FastAPI app creation and router registration |
| [Connections API](backend/api-connections.md) | `POST /api/connections/test` — SSE connection test |
| [Health API](backend/api-health.md) | `GET /api/health/{connection_id}` — database health scoring |
| [Queries API](backend/api-queries.md) | `GET /api/queries/{connection_id}` — slow query scan |
| [Explain API](backend/api-explain.md) | `GET /api/explain/{connection_id}/{queryid}` — EXPLAIN plan |
| [Fix API](backend/api-fix.md) | `GET /api/fix/{connection_id}/{queryid}` — fix recommendations |
| [Dashboard API](backend/api-dashboard.md) | `GET /api/dashboard/{connection_id}` — aggregated report |
| [DB Engine](backend/db_engine-connector.md) | `DBConnector` — async connection pool wrapper |
| [Semgrep](backend/semgrep.md) | Scanner environment setup and verified JSON output contract |
| [Testing](backend/testing.md) | Test suite structure and conventions |

### Frontend

| Module | Description |
|--------|-------------|
| [Types](frontend/types.md) | TypeScript interfaces mirroring backend Pydantic models |
| [API clients](frontend/api.md) | Thin fetch/EventSource wrappers for each backend route |
| [Hooks](frontend/hooks.md) | React hooks for data fetching and SSE state |
| [Components](frontend/components.md) | Reusable UI components |
| [Pages](frontend/pages.md) | Page-level components and routing |

---

## Quick start

```bash
# 1. Start the database
docker compose up -d

# 2. Start the backend (from backend/)
uvicorn app.main:app --reload

# 3. Start the frontend (from frontend/)
npm run dev
```

The frontend is available at `http://localhost:5173` and proxies API calls to `http://localhost:8000`.

---

## Environment

Copy `backend/.env.example` to `backend/.env` and set `DATABASE_URL` before starting the server.

```
DATABASE_URL=postgresql://user:pass@localhost:5432/dbname
```
