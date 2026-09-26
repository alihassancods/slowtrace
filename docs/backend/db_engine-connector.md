# DB Engine — `backend/src/db_engine/connector.py`

## Overview

[`backend/src/db_engine/connector.py`](../../backend/src/db_engine/connector.py) provides `DBConnector`, a thin async wrapper around an [asyncpg](https://magicstack.github.io/asyncpg/) connection pool. It centralises connection-string handling, pool lifecycle, and error translation into Python `ConnectionError` exceptions.

> **Note:** The API routers (health, queries, explain, fix) currently open raw `asyncpg.connect()` connections directly rather than using `DBConnector`. This class is available for future use or for code that needs a shared, long-lived pool.

---

## Class: `DBConnector`

```python
class DBConnector:
    def __init__(self, connection_string: str) -> None
```

### Constructor

| Parameter | Type | Description |
|-----------|------|-------------|
| `connection_string` | `str` | Standard PostgreSQL DSN, e.g. `postgresql://user:pass@host/db` |

Stores the DSN; the pool is not opened until `await db.connect()` is called.

---

## Pool lifecycle methods

### `async connect() → None`

Opens an asyncpg connection pool with:
- `min_size=2` — two connections kept warm at all times
- `max_size=10` — pool scales up to ten connections under load

Translates asyncpg exceptions to `ConnectionError` with human-readable messages:

| asyncpg exception | Raised `ConnectionError` message |
|-------------------|----------------------------------|
| `InvalidPasswordError` | `"Database authentication failed: wrong password."` |
| `InvalidCatalogNameError` | `"Database not found: <details>"` |
| `OSError` | `"Host not reachable: <details>"` |
| `PostgresError` (SSL) | `"SSL connection is required by the server but was not requested."` |
| `PostgresError` (other) | `"Failed to connect to database: <details>"` |

### `async close() → None`

Gracefully closes all connections in the pool. Safe to call if the pool was never opened (no-op).

---

## Query helpers

All methods raise `RuntimeError("Connection pool is not open. Call await db.connect() first.")` if called before `connect()`.

### `async fetch(query, *args) → list[Record]`

Executes `query` and returns all matching rows as asyncpg `Record` objects.

### `async fetchrow(query, *args) → Record | None`

Executes `query` and returns the first row, or `None` if no rows match.

### `async fetchval(query, *args) → object`

Executes `query` and returns the first column of the first row (a scalar).

### `async execute(query, *args) → None`

Executes a non-returning statement (`INSERT`, `UPDATE`, `DELETE`, DDL). Return value is discarded.

---

## Usage example

```python
db = DBConnector("postgresql://user:pass@host/dbname")
await db.connect()

rows = await db.fetch("SELECT * FROM events WHERE id = $1", 42)
row  = await db.fetchrow("SELECT name FROM users WHERE id = $1", 1)
val  = await db.fetchval("SELECT count(*) FROM orders")

await db.execute("UPDATE users SET active = true WHERE id = $1", 1)
await db.close()
```

Parameters are passed positionally as `$1`, `$2`, … in the SQL string. asyncpg handles proper escaping; never interpolate values directly into the query string.
