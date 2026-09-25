"""Async PostgreSQL connection pool backed by asyncpg."""

from __future__ import annotations

import asyncpg
from asyncpg import Pool, Record


class DBConnector:
    """Async connection pool wrapper around asyncpg.

    Usage::

        db = DBConnector("postgresql://user:pass@host/dbname")
        await db.connect()
        rows = await db.fetch("SELECT * FROM events WHERE id = $1", 42)
        await db.close()
    """

    def __init__(self, connection_string: str) -> None:
        self._dsn = connection_string
        self._pool: Pool | None = None

    # ------------------------------------------------------------------
    # Pool lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open the connection pool.  Call once before any queries."""
        try:
            self._pool = await asyncpg.create_pool(
                dsn=self._dsn,
                min_size=2,
                max_size=10,
            )
        except asyncpg.InvalidPasswordError as exc:
            raise ConnectionError(
                "Database authentication failed: wrong password."
            ) from exc
        except asyncpg.InvalidCatalogNameError as exc:
            raise ConnectionError(
                f"Database not found: {exc}"
            ) from exc
        except OSError as exc:
            # Covers TCP connection refused / hostname not resolvable.
            raise ConnectionError(
                f"Host not reachable: {exc}"
            ) from exc
        except asyncpg.PostgresError as exc:
            # Catch-all for other Postgres-level errors (e.g. SSL required).
            msg = str(exc)
            if "SSL" in msg.upper():
                raise ConnectionError(
                    "SSL connection is required by the server but was not requested."
                ) from exc
            raise ConnectionError(f"Failed to connect to database: {msg}") from exc

    async def close(self) -> None:
        """Gracefully close all connections in the pool."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def _require_pool(self) -> Pool:
        if self._pool is None:
            raise RuntimeError(
                "Connection pool is not open. Call await db.connect() first."
            )
        return self._pool

    async def fetch(self, query: str, *args: object) -> list[Record]:
        """Execute *query* and return all matching rows."""
        pool = self._require_pool()
        return await pool.fetch(query, *args)

    async def fetchrow(self, query: str, *args: object) -> Record | None:
        """Execute *query* and return the first row, or ``None``."""
        pool = self._require_pool()
        return await pool.fetchrow(query, *args)

    async def fetchval(self, query: str, *args: object) -> object:
        """Execute *query* and return the first column of the first row."""
        pool = self._require_pool()
        return await pool.fetchval(query, *args)

    async def execute(self, query: str, *args: object) -> None:
        """Execute *query* without returning any result (INSERT/UPDATE/DELETE)."""
        pool = self._require_pool()
        await pool.execute(query, *args)
