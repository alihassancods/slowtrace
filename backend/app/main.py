import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI

from api.connections import router as connections_router
from api.dashboard import router as dashboard_router
from api.explain import router as explain_router
from api.fix import router as fix_router
from api.health import router as health_router
from api.queries import router as queries_router

app = FastAPI(title="SlowTrace", version="0.1.0")

app.include_router(connections_router)
app.include_router(dashboard_router)
app.include_router(explain_router)
app.include_router(fix_router)
app.include_router(health_router)
app.include_router(queries_router)


@app.get("/health")
def health():
    return {"status": "ok"}
