"""POST /api/connections/test — stream connection-test results via SSE.

On success, stores the DSN in the shared connection registry and includes
the generated ``connection_id`` in the final ``result`` SSE event.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import asyncpg
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.store import CONNECTIONS

router = APIRouter()


# ---------------------------------------------------------------------------
# Request schema
# ---------------------------------------------------------------------------


class ConnectionTestRequest(BaseModel):
    connection_string: str
    nickname: str = ""


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------


def _event(step: str, status: str, data: dict[str, Any]) -> str:
    """Format a single SSE data line."""
    payload = json.dumps({"step": step, "status": status, "data": data})
    return f"data: {payload}\n\n"


# ---------------------------------------------------------------------------
# Step implementations
# ---------------------------------------------------------------------------


async def _step_connect(
    dsn: str,
) -> tuple[asyncpg.Connection | None, str, dict[str, Any]]:
    """Step 1 – open a raw connection with a 5-second timeout."""
    try:
        conn = await asyncio.wait_for(asyncpg.connect(dsn=dsn), timeout=5)
        return conn, "ok", {}
    except asyncio.TimeoutError:
        return None, "fail", {"message": "Connection timed out after 5 seconds."}
    except asyncpg.InvalidPasswordError:
        return None, "fail", {"message": "Authentication failed: wrong password."}
    except asyncpg.InvalidCatalogNameError as exc:
        return None, "fail", {"message": f"Database not found: {exc}"}
    except OSError as exc:
        return None, "fail", {"message": f"Host not reachable: {exc}"}
    except asyncpg.PostgresError as exc:
        msg = str(exc)
        if "SSL" in msg.upper():
            return None, "fail", {"message": "SSL connection required by server."}
        return None, "fail", {"message": f"Connection error: {msg}"}


async def _step_pg_stat_statements(
    conn: asyncpg.Connection,
) -> tuple[str, dict[str, Any]]:
    """Step 2 – check that pg_stat_statements extension is installed."""
    try:
        count: int = await conn.fetchval(
            "SELECT count(*) FROM pg_extension WHERE extname = 'pg_stat_statements'"
        )
        if count:
            return "ok", {}
        return "warning", {
            "message": "pg_stat_statements extension is not installed.",
            "fix": "CREATE EXTENSION pg_stat_statements;",
        }
    except asyncpg.PostgresError as exc:
        return "fail", {"message": f"Could not query pg_extension: {exc}"}


async def _step_verify_permissions(
    conn: asyncpg.Connection,
) -> tuple[str, dict[str, Any]]:
    """Step 3 – verify the user can read from pg_stat_statements."""
    try:
        await conn.fetch("SELECT * FROM pg_stat_statements LIMIT 1")
        return "ok", {}
    except asyncpg.InsufficientPrivilegeError:
        try:
            user: str = await conn.fetchval("SELECT current_user")
        except Exception:
            user = "your_user"
        return "warning", {
            "message": "Insufficient privileges to read pg_stat_statements.",
            "fix": f"GRANT pg_read_all_stats TO {user};",
        }
    except asyncpg.UndefinedTableError:
        return "warning", {
            "message": "pg_stat_statements view is not accessible.",
            "fix": "CREATE EXTENSION pg_stat_statements;",
        }
    except asyncpg.PostgresError as exc:
        return "fail", {"message": f"Permission check failed: {exc}"}


async def _step_db_info(
    conn: asyncpg.Connection,
) -> tuple[str, dict[str, Any]]:
    """Step 4 – collect version, size, table count and total row estimate."""
    try:
        version: str = await conn.fetchval("SELECT version()")
        db_size: str = await conn.fetchval(
            "SELECT pg_size_pretty(pg_database_size(current_database()))"
        )
        table_count: int = await conn.fetchval(
            """
            SELECT count(*)
            FROM information_schema.tables
            WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
              AND table_type = 'BASE TABLE'
            """
        )
        total_rows: int = await conn.fetchval(
            """
            SELECT coalesce(sum(reltuples), 0)::bigint
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind = 'r'
              AND n.nspname NOT IN ('information_schema', 'pg_catalog')
            """
        )
        return "ok", {
            "version": version,
            "size": db_size,
            "table_count": int(table_count),
            "total_rows": int(total_rows),
        }
    except asyncpg.PostgresError as exc:
        return "fail", {"message": f"Could not read database info: {exc}"}


# ---------------------------------------------------------------------------
# SSE generator
# ---------------------------------------------------------------------------


async def _run_test(dsn: str, nickname: str) -> AsyncGenerator[str, None]:
    """Yield SSE events for each test step, then a final summary event."""
    steps: list[dict[str, Any]] = []
    db_info: dict[str, Any] = {}

    # Step 1 – connect
    conn, status, data = await _step_connect(dsn)
    steps.append({"step": "connect", "status": status, "data": data})
    yield _event("connect", status, data)

    if conn is None:
        yield _event(
            "result",
            "fail",
            {"success": False, "steps": steps},
        )
        return

    try:
        # Step 2 – pg_stat_statements
        status, data = await _step_pg_stat_statements(conn)
        steps.append({"step": "pg_stat_statements", "status": status, "data": data})
        yield _event("pg_stat_statements", status, data)

        # Step 3 – permissions
        status, data = await _step_verify_permissions(conn)
        steps.append({"step": "permissions", "status": status, "data": data})
        yield _event("permissions", status, data)

        # Step 4 – database info
        status, data = await _step_db_info(conn)
        steps.append({"step": "db_info", "status": status, "data": data})
        yield _event("db_info", status, data)
        if status == "ok":
            db_info = data

    finally:
        await conn.close()

    # Final summary — store connection on overall success
    overall = all(s["status"] in ("ok", "warning") for s in steps)

    connection_id: str | None = None
    if overall:
        connection_id = str(uuid.uuid4())
        CONNECTIONS[connection_id] = dsn

    yield _event(
        "result",
        "ok" if overall else "fail",
        {
            "success": overall,
            "connection_id": connection_id,
            "nickname": nickname,
            "version": db_info.get("version", ""),
            "size": db_info.get("size", ""),
            "table_count": db_info.get("table_count", 0),
            "total_rows": db_info.get("total_rows", 0),
            "steps": steps,
        },
    )


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.post("/connections/test")
async def test_connection(body: ConnectionTestRequest) -> StreamingResponse:
    """Stream connection-test step results as Server-Sent Events.

    On success the final ``result`` event includes a ``connection_id`` that
    callers must pass to subsequent endpoints.
    """
    return StreamingResponse(
        _run_test(body.connection_string, body.nickname),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
