# AGENTS.md

This file provides guidance to agents when working with code in this repository.

## Stack

- **Backend**: Python 3.11+ · FastAPI · SQLAlchemy · Alembic · PostgreSQL (psycopg2)
- **Frontend**: React 18 · Vite · TypeScript (strict mode) · Vitest
- **Dev DB**: Docker Compose — `docker compose up -d` starts PostgreSQL on port 5432

## Commands

### Backend (run from `backend/`)

```bash
uvicorn app.main:app --reload          # dev server → http://localhost:8000
pytest                                  # all tests
pytest tests/path/test_file.py::test_fn # single test
```

### Frontend (run from `frontend/`)

```bash
npm run dev       # dev server → http://localhost:5173
npm run test:run  # single-pass test run (CI)
npm test          # watch mode
npm run lint      # ESLint
npm run build     # tsc + vite build
```

## Key conventions

- Backend `app/main.py` is the FastAPI entry point — `app` is the instance name expected by uvicorn.
- Frontend dev server proxies `/api/*` → `http://localhost:8000` (configured in [`vite.config.ts`](frontend/vite.config.ts)).
- TypeScript path alias `@/*` → `src/*` is configured in [`tsconfig.json`](frontend/tsconfig.json).
- Backend env vars live in `backend/.env` (copy from `backend/.env.example`); `DATABASE_URL` is the only required variable.
- `bob_sessions/` is intentionally tracked in git (screenshots).
