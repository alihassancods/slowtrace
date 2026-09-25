import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI

from api.connections import router as connections_router

app = FastAPI(title="SlowTrace", version="0.1.0")

app.include_router(connections_router)


@app.get("/health")
def health():
    return {"status": "ok"}
