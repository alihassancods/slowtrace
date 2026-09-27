# Frontend Hooks — `frontend/src/hooks/`

## Overview

The [`frontend/src/hooks/`](../../frontend/src/hooks/) directory contains React custom hooks that own the loading state, data, and side-effects for each backend interaction. Pages and components import hooks rather than calling API functions directly.

---

## `hooks/useDashboard.ts`

Source: [`frontend/src/hooks/useDashboard.ts`](../../frontend/src/hooks/useDashboard.ts)

### `useDashboard(dsn)`

```ts
function useDashboard(dsn: string | null): UseDashboardResult
```

Fetches the aggregated dashboard report for the given DSN. Automatically triggers a load when `dsn` changes.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `dsn` | `string \| null` | PostgreSQL connection string; no fetch is made when `null` |

**Returns: `UseDashboardResult`**

| Field | Type | Description |
|-------|------|-------------|
| `state` | `'idle' \| 'loading' \| 'loaded' \| 'error'` | Current fetch state |
| `report` | `DashboardReport \| null` | Populated on `'loaded'` |
| `load` | `(dsn: string) => void` | Manually trigger a refresh |

**Behaviour:**
- On mount (or when `dsn` changes from non-null to a new value), calls `fetchDashboard(dsn)`.
- On success: transitions `'loading' → 'loaded'` and sets `report`.
- On failure: transitions `'loading' → 'error'`.
- `load` is stable (wrapped in `useCallback`) — safe to use as a dependency in `useEffect`.

---

## `hooks/useQueryScan.ts`

Source: [`frontend/src/hooks/useQueryScan.ts`](../../frontend/src/hooks/useQueryScan.ts)

### `useQueryScan()`

```ts
function useQueryScan(): UseQueryScanResult
```

Manages the live SSE slow-query scan. Supports start, cancel, and reset.

**Returns: `UseQueryScanResult`**

| Field | Type | Description |
|-------|------|-------------|
| `state` | `ScanState` | `'idle' \| 'scanning' \| 'done' \| 'error'` |
| `steps` | `ScanStep[]` | Accumulated SSE events received so far |
| `queries` | `SlowQuery[]` | Final list of queries (populated on `'done'`) |
| `start` | `(dsn: string) => void` | Opens the SSE stream |
| `reset` | `() => void` | Closes any open stream and resets to `'idle'` |

**State transitions:**

```
idle ──start()──▶ scanning ──result ok──▶ done
                           ──result fail──▶ error (or 'done' with errors)
                           ──onerror──────▶ error
     ◀──reset()────────────────────────────
```

The result step sets state to `'error'` only when the connect step itself failed (connection could not be established). If only the extension or permissions steps have errors, state is `'done'` (with errors in `steps`).

The `esRef` ref holds the active `EventSource` so `reset()` can close it even if the component re-renders in between.

---

## `hooks/useQueryDetail.ts`

Source: [`frontend/src/hooks/useQueryDetail.ts`](../../frontend/src/hooks/useQueryDetail.ts)

### `useQueryDetail(dsn, queryid)`

```ts
function useQueryDetail(
  dsn: string | null,
  queryid: string | null,
): UseQueryDetailResult
```

Fetches the EXPLAIN detail for a single query. Automatically triggers when both `dsn` and `queryid` are non-null.

**Returns: `UseQueryDetailResult`**

| Field | Type | Description |
|-------|------|-------------|
| `state` | `'idle' \| 'loading' \| 'loaded' \| 'error'` | Current fetch state |
| `detail` | `QueryDetail \| null` | Populated on `'loaded'` |
| `load` | `(dsn: string, queryid: string) => void` | Manually trigger a reload |

**Behaviour:**
- `useEffect` watches both `dsn` and `queryid`; fetches when both are truthy.
- `load` resets `detail` to `null` before fetching.

---

## `hooks/useFixWizard.ts`

Source: [`frontend/src/hooks/useFixWizard.ts`](../../frontend/src/hooks/useFixWizard.ts)

### `useFixWizard(dsn, queryid)`

```ts
function useFixWizard(
  dsn: string | null,
  queryid: string | null,
): UseFixWizardResult
```

Fetches fix recommendations for a single query. Automatically triggers when both `dsn` and `queryid` are non-null.

**Returns: `UseFixWizardResult`**

| Field | Type | Description |
|-------|------|-------------|
| `state` | `'idle' \| 'loading' \| 'loaded' \| 'error'` | Current fetch state |
| `report` | `FixReport \| null` | Populated on `'loaded'` |

Unlike `useQueryDetail`, `useFixWizard` does not expose a manual `load` function — the page simply remounts or re-navigates to refresh.

---

## Common patterns

### State machine shape

All data-fetching hooks follow the same four-state machine:

```
idle → loading → loaded
               → error
```

### DSN from `localStorage`

Pages read the DSN from `localStorage.getItem('slowtrace_dsn')` and pass it as the first argument. The `ScanPage` writes the DSN on each successful scan start.

### Stable callbacks

`load` functions are memoised with `useCallback(fn, [])` (empty dependency array) so they can be safely listed in `useEffect` dependency arrays without causing infinite re-renders.
