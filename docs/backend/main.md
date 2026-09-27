# Application entry point — `backend/app/main.py`

## Overview

[`backend/app/main.py`](../../backend/app/main.py) is the FastAPI application entry point. It creates the `app` instance, loads environment variables, and mounts every API router.

Uvicorn expects the module path `app.main:app` when launched from the `backend/` directory:

```bash
# from backend/
uvicorn app.main:app --reload
```

---

## Path manipulation

```python
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
```

The actual router modules live in `backend/src/api/`. This `sys.path` insertion makes them importable as `api.*` without installing the package.

---

## Application instance

```python
app = FastAPI(title="SlowTrace", version="0.1.0")
```

The `app` name is required by Uvicorn's `app.main:app` reference.

---

## Registered routers

| Router variable | Module | Prefix |
|----------------|--------|--------|
| `connections_router` | `api.connections` | `/api/connections` |
| `dashboard_router` | `api.dashboard` | `/api/dashboard` |
| `explain_router` | `api.explain` | `/api/explain` |
| `fix_router` | `api.fix` | `/api/fix` |
| `health_router` | `api.health` | `/api/health` |
| `queries_router` | `api.queries` | `/api/queries` |

Each router is defined in its own file under `backend/src/api/` and documented separately.

---

## Root health check

A bare `GET /health` route is also registered directly on `app`:

```python
@app.get("/health")
def health():
    return {"status": "ok"}
```

This is distinct from the database health-scoring endpoint at `/api/health/{connection_id}`. It exists solely to let infrastructure (load balancers, container probes) confirm the process is alive.
