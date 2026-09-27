# Connections API — `backend/src/api/connections.py`

## Overview

[`backend/src/api/connections.py`](../../backend/src/api/connections.py) implements the connection-test endpoint. Clients submit a PostgreSQL DSN and receive a real-time stream of step results as [Server-Sent Events (SSE)](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events).

**Base prefix:** `/api/connections`

---

## Route

### `POST /api/connections/test`

Runs a four-step connection test and streams one SSE event per step, then a final `result` event.

**Request body** (`application/json`):

```json
{ "connection_string": "postgresql://user:pass@host:5432/dbname" }
```

**Response:** `text/event-stream` — each event is a `data: <json>\n\n` line with the shape:

```json
{
  "step":   "<step_name>",
  "status": "ok" | "warning" | "fail",
  "data":   { … }
}
```

The final event always has `"step": "result"` and a `data` object that contains:

| Field | Type | Description |
|-------|------|-------------|
| `success` | `bool` | `true` when all steps passed (ok or warning) |
| `version` | `string` | Postgres server version string |
| `size` | `string` | Human-readable database size (e.g. `"42 MB"`) |
| `table_count` | `int` | Number of user-defined base tables |
| `total_rows` | `int` | Fast planner-estimate total row count |
| `steps` | `list` | Echo of each step's `{step, status, data}` |

---

## Step sequence

| Order | Step name | What it checks |
|-------|-----------|----------------|
| 1 | `connect` | Opens a raw asyncpg connection with a 5-second timeout |
| 2 | `pg_stat_statements` | Verifies `pg_stat_statements` extension is installed |
| 3 | `permissions` | Verifies the connecting user can `SELECT` from `pg_stat_statements` |
| 4 | `db_info` | Collects version, size, table count, and total row estimate |

If step 1 (`connect`) fails, steps 2–4 are skipped and the result event is emitted immediately with `"success": false`.

---

## Pydantic models

### `ConnectionTestRequest`

```python
class ConnectionTestRequest(BaseModel):
    connection_string: str
```

The POST body schema.

---

## Step implementations

### `_step_connect(dsn)`

Opens a raw connection using `asyncpg.connect(dsn=dsn, timeout=5)`.

Returns `(conn, status, data)` where:
- `conn` is `None` on failure
- `status` is `"ok"` or `"fail"`
- `data` contains a `"message"` key on failure

Handles: `TimeoutError`, `InvalidPasswordError`, `InvalidCatalogNameError`, `OSError`, SSL errors, and generic `PostgresError`.

### `_step_pg_stat_statements(conn)`

Queries `pg_extension` to confirm the extension is installed.

Returns `"ok"` / `"warning"` (with `"fix"` hint) / `"fail"`.

### `_step_verify_permissions(conn)`

Attempts `SELECT * FROM pg_stat_statements LIMIT 1`.

Returns `"ok"` / `"warning"` (insufficient privilege or undefined table) / `"fail"`.

The warning `data` object includes a `"fix"` key with the exact `GRANT` statement needed.

### `_step_db_info(conn)`

Runs four queries to collect:
- `version()` — full version string
- `pg_size_pretty(pg_database_size(...))` — database size
- `information_schema.tables` count — user table count
- `pg_class.reltuples` sum — estimated total rows

Returns `"ok"` with the data dict, or `"fail"` with a message on any Postgres error.

---

## SSE generator — `_run_test(dsn)`

An async generator that yields one formatted SSE line per step, then the final `result` event. Used directly by the route handler as the `StreamingResponse` body.

---

## Error handling

All errors are surfaced inline as SSE events rather than HTTP error responses. The HTTP status of the SSE stream itself is always `200`; consumers must inspect each event's `status` field.

Headers set on the response:
```
Cache-Control: no-cache
X-Accel-Buffering: no
```

`X-Accel-Buffering: no` prevents Nginx from buffering the stream.
