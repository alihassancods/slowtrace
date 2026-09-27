"""src/api – SlowTrace API routers."""

from .connections import router as connections_router
from .dashboard import router as dashboard_router
from .explain import router as explain_router
from .fix import router as fix_router
from .fixes import router as fixes_router
from .health import router as health_router
from .queries import router as queries_router
from .scan import router as scan_router
from .wizard import router as wizard_router

__all__ = [
    "connections_router",
    "dashboard_router",
    "explain_router",
    "fix_router",
    "fixes_router",
    "health_router",
    "queries_router",
    "scan_router",
    "wizard_router",
]
