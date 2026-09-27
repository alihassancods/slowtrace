# Frontend Pages — `frontend/src/pages/` and routing

## Overview

The [`frontend/src/pages/`](../../frontend/src/pages/) directory contains page-level React components. Each page maps to a route declared in [`App.tsx`](../../frontend/src/App.tsx).

---

## Routing — `App.tsx`

Source: [`frontend/src/App.tsx`](../../frontend/src/App.tsx)

React Router v6 `BrowserRouter` with the following route table:

| Path | Component | Description |
|------|-----------|-------------|
| `/` | `WelcomePage` | Landing page |
| `/scan` | `ScanPage` | Slow-query scan |
| `/dashboard` | `DashboardPage` | Aggregated health + query overview |
| `/dashboard/query/:id` | `QueryDetailPage` | Single-query EXPLAIN detail |
| `/fix/:id` | `FixPage` | Fix Wizard recommendations |
| `/wizard` | `WizardPage` | (Wizard flow entry point) |

The navigation bar links to `/`, `/scan`, `/dashboard`, and `/wizard`. Active links are white/bold; inactive links are muted slate.

---

## `ScanPage`

Source: [`frontend/src/pages/ScanPage.tsx`](../../frontend/src/pages/ScanPage.tsx)

The slow-query scan interface.

### Behaviour

1. **Input form** — shows a DSN text field and a Scan button when not scanning. On submit, the DSN is saved to `localStorage('slowtrace_dsn')` and `useQueryScan().start(dsn)` is called.
2. **Scanning state** — the input form is replaced by the DSN label + a Cancel button. [`ScanProgress`](components.md#scanprogress) renders live step events.
3. **Done state** — `ScanProgress` remains visible (frozen), and [`QueryTable`](components.md#querytable) renders all returned queries. The results header shows the count.
4. **Error state** — a red error banner is shown. The input form reappears with a Reset button.

### Dependencies

- Hook: [`useQueryScan`](hooks.md#hooksusescants)
- Components: [`ScanProgress`](components.md#scanprogress), [`QueryTable`](components.md#querytable)

---

## `DashboardPage`

Source: [`frontend/src/pages/DashboardPage.tsx`](../../frontend/src/pages/DashboardPage.tsx)

The aggregated health + query overview.

### Behaviour

- Reads the DSN from `localStorage`. If absent, shows a "run a scan first" message.
- Uses [`useDashboard`](hooks.md#hooksuseashboard) to fetch `DashboardReport`.
- **Loading:** spinner overlay.
- **Error:** error banner with a "Try again" link that calls `load(dsn)`.
- **Loaded:** renders `HealthSection` and `QuerySection` sub-components.

### `HealthSection` (internal)

Displays:
- Grade badge (A/B/C/D/F) with colour coding
- Score, checks passing count, total deductions
- Warning/fail check banners for each non-passing check

### `QuerySection` (internal)

Displays:
- Stat tiles: total queries, high-priority count (score ≥ 70), average score, total exec time
- A clickable table of the top 10 queries by score; clicking navigates to `/dashboard/query/:id`

### `Refresh` button

The page header contains a Refresh button that calls `load(dsn)` to re-fetch without navigating away.

---

## `QueryDetailPage`

Source: [`frontend/src/pages/QueryDetailPage.tsx`](../../frontend/src/pages/QueryDetailPage.tsx)

Single-query detail with EXPLAIN plan.

### Route

`/dashboard/query/:id` — the `:id` segment is the `queryid` from `pg_stat_statements`.

### Behaviour

- Reads DSN from `localStorage`; if absent, shows a prompt to run a scan first.
- Uses [`useQueryDetail`](hooks.md#hooksquerydetail) to fetch `QueryDetail`.
- Renders:
  - Query fingerprint (monospace pre block)
  - Error banners for any `ExplainError` entries
  - A stats grid: Calls, Mean (ms), Total (ms), Cache Hit %, Std Dev (ms), Score
  - [`PlanTree`](components.md#plantree) for the EXPLAIN output
- **Fix Wizard →** button in the top-right navigates to `/fix/:id`.

---

## `FixPage`

Source: [`frontend/src/pages/FixPage.tsx`](../../frontend/src/pages/FixPage.tsx)

Fix Wizard — ranked recommendations for a single query.

### Route

`/fix/:id` — the `:id` segment is the `queryid`.

### Behaviour

- Reads DSN from `localStorage`.
- Uses [`useFixWizard`](hooks.md#hooksusefixwizard) to fetch `FixReport`.
- Renders:
  - Query fingerprint
  - Error banners for any `FixError` entries
  - Stats bar: Score, Mean (ms), Total (ms), Cache Hit %
  - A count badge showing the number of recommendations
  - One [`FixCard`](components.md#fixcard) per recommendation (sorted high → medium → low by the backend)
  - "No issues detected" placeholder when `recommendations` is empty.
- **Back to Query Detail** link navigates to `/dashboard/query/:id`.

---

## DSN persistence

The DSN is stored in `localStorage` under the key `slowtrace_dsn`. `ScanPage` writes it on each scan start. All detail pages (`QueryDetailPage`, `FixPage`, `DashboardPage`) read from this key so the user does not need to re-enter the connection string on every navigation.
