"""GET /api/wizard/status/{connection_id} — SQLCommenter detection status."""

from __future__ import annotations

import asyncio

import asyncpg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.store import CONNECTIONS

router = APIRouter()

_FETCH_QUERIES_SQL = """
SELECT query
FROM pg_stat_statements
WHERE calls > 0
LIMIT 100
"""


class WizardStatus(BaseModel):
    active: bool


@router.get("/wizard/status/{connection_id}", response_model=WizardStatus)
async def wizard_status(connection_id: str) -> WizardStatus:
    """Return whether SQLCommenter tracing is detected on any recent query."""
    from agent.code_linker.comment_parser import SQLCommentParser

    dsn = CONNECTIONS.get(connection_id)
    if dsn is None:
        raise HTTPException(status_code=404, detail="Connection ID not found.")

    try:
        conn: asyncpg.Connection = await asyncio.wait_for(
            asyncpg.connect(dsn=dsn), timeout=5
        )
    except Exception:
        raise HTTPException(status_code=502, detail="Could not connect to the database.")

    try:
        try:
            rows = await asyncio.wait_for(conn.fetch(_FETCH_QUERIES_SQL), timeout=10)
        except Exception:
            return WizardStatus(active=False)
    finally:
        await conn.close()

    queries = [{"query": r["query"]} for r in rows]
    parser = SQLCommentParser()
    return WizardStatus(active=parser.check_if_active(queries))
