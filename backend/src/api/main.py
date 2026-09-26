"""SlowTrace FastAPI application.

Mounts all routers under /api and configures CORS for the React dev server.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.connections import router as connections_router
from api.dashboard import router as dashboard_router
from api.explain import router as explain_router
from api.fix import router as fix_router
from api.fixes import router as fixes_router
from api.health import router as health_router
from api.queries import router as queries_router
from api.scan import router as scan_router
from api.wizard import router as wizard_router

app = FastAPI(title="SlowTrace", version="0.1.0")

# ---------------------------------------------------------------------------
# CORS — allow the React dev server (Vite default: http://localhost:5173)
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers  (each router defines paths like /connections/test, /scan/{id} …
#           the /api prefix is added here so final paths are /api/…)
# ---------------------------------------------------------------------------

# New UUID-based routes
app.include_router(connections_router, prefix="/api")
app.include_router(scan_router, prefix="/api")
app.include_router(queries_router, prefix="/api")
app.include_router(wizard_router, prefix="/api")
app.include_router(fixes_router, prefix="/api")

# Legacy DSN-based routes (kept for backwards compatibility)
app.include_router(dashboard_router)
app.include_router(explain_router)
app.include_router(fix_router)
app.include_router(health_router)


# ---------------------------------------------------------------------------
# Utility routes
# ---------------------------------------------------------------------------


@app.get("/")
def home() -> dict[str, str]:
    return {"message": "Welcome to SlowTrace"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
