# SlowTrace

**SlowTrace** is a PostgreSQL performance monitoring tool that pinpoints which line of application code is responsible for slow database queries.

## Overview

SlowTrace connects to your PostgreSQL instance, captures slow query logs, and correlates them back to the exact file and line number in your application's source code — so you know exactly where to look when a query is underperforming.

## Project Structure

```
slowtrace/
├── backend/          # Python FastAPI application
│   ├── app/
│   │   ├── api/      # Route handlers
│   │   ├── core/     # Config, database, settings
│   │   ├── models/   # SQLAlchemy models
│   │   ├── schemas/  # Pydantic schemas
│   │   └── services/ # Business logic
│   └── tests/
├── frontend/         # React + Vite + TypeScript application
│   └── src/
│       ├── api/      # API client
│       ├── components/
│       ├── hooks/
│       ├── pages/
│       └── types/
├── bob_sessions/     # Bob session screenshots
└── docker-compose.yml
```

## Getting Started

### Prerequisites

- Python 3.11+
- Node.js 20+
- Docker & Docker Compose

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

## Development

| Command | Description |
|---|---|
| `uvicorn app.main:app --reload` | Start backend dev server (port 8000) |
| `npm run dev` | Start frontend dev server (port 5173) |
| `pytest` | Run backend tests |
| `pytest tests/path/test_file.py::test_name` | Run a single backend test |
| `npm test` | Run frontend tests (watch) |
| `npm run test:run` | Run frontend tests (once) |
| `npm run lint` | Lint frontend |
