# Frontend API clients — `frontend/src/api/`

## Overview

The [`frontend/src/api/`](../../frontend/src/api/) directory contains thin wrappers around the browser's `fetch` API and `EventSource`. Each file corresponds to one backend API module. They handle URL encoding of the DSN and throw on HTTP errors.

All functions are tree-shakeable plain exports — no class instances or singletons.

---

## `api/dashboard.ts`

Source: [`frontend/src/api/dashboard.ts`](../../frontend/src/api/dashboard.ts)

### `fetchDashboard(dsn)`

```ts
async function fetchDashboard(dsn: string): Promise<DashboardReport>
```

Calls `GET /api/dashboard/<encoded-dsn>` and returns the parsed `DashboardReport`.

Throws a plain `Error("HTTP <status>")` on non-OK responses.

**Usage:**
```ts
import { fetchDashboard } from '@/api/dashboard'
const report = await fetchDashboard('postgresql://user:pass@host/db')
```

---

## `api/queries.ts`

Source: [`frontend/src/api/queries.ts`](../../frontend/src/api/queries.ts)

### `streamQueries(dsn, onEvent, onDone, onError)`

```ts
function streamQueries(
  dsn: string,
  onEvent: (step: ScanStep) => void,
  onDone:  (report: QueryReport) => void,
  onError: (err: Event) => void,
): EventSource
```

Opens an `EventSource` to `GET /api/queries/<encoded-dsn>/stream` and dispatches callbacks:

| Callback | When | Payload |
|----------|------|---------|
| `onEvent` | Each non-result SSE event | `ScanStep` |
| `onDone` | `"step": "result"` event | `QueryReport` |
| `onError` | `EventSource.onerror` | native `Event` |

The `EventSource` is closed automatically after the `result` event or on error. The returned `EventSource` reference can be closed early by the caller (e.g. on user cancel).

**Usage:**
```ts
import { streamQueries } from '@/api/queries'

const es = streamQueries(
  dsn,
  (step) => console.log(step.step, step.status),
  (report) => console.log('done', report.total_queries),
  (err)  => console.error('stream error', err),
)

// Cancel early:
es.close()
```

---

## `api/explain.ts`

Source: [`frontend/src/api/explain.ts`](../../frontend/src/api/explain.ts)

### `fetchQueryDetail(dsn, queryid)`

```ts
async function fetchQueryDetail(dsn: string, queryid: string): Promise<QueryDetail>
```

Calls `GET /api/explain/<encoded-dsn>/<queryid>` and returns the parsed `QueryDetail`.

Throws `Error("HTTP <status>")` on non-OK responses.

---

## `api/fix.ts`

Source: [`frontend/src/api/fix.ts`](../../frontend/src/api/fix.ts)

### `fetchFix(dsn, queryid)`

```ts
async function fetchFix(dsn: string, queryid: string): Promise<FixReport>
```

Calls `GET /api/fix/<encoded-dsn>/<queryid>` and returns the parsed `FixReport`.

Throws `Error("HTTP <status>")` on non-OK responses.

---

## URL encoding

All DSN values are passed through `encodeURIComponent(dsn)` before being embedded in the URL. This ensures special characters in the connection string (`:`, `/`, `@`, `?`) are properly escaped.

The backend decodes the path parameter with `urllib.parse.unquote`.

---

## Dev proxy

During development, Vite proxies all `/api/*` requests to `http://localhost:8000`, so no full origin is needed in the URL strings. See [`vite.config.ts`](../../frontend/vite.config.ts) for the proxy configuration.
