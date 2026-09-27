# Frontend Components — `frontend/src/components/`

## Overview

The [`frontend/src/components/`](../../frontend/src/components/) directory contains reusable UI components used by multiple pages. All components are default exports.

---

## `QueryTable`

Source: [`frontend/src/components/QueryTable.tsx`](../../frontend/src/components/QueryTable.tsx)

Renders a sortable table of slow queries.

### Props

```ts
interface QueryTableProps {
  queries: SlowQuery[]
}
```

### Behaviour

- Displays one row per `SlowQuery` with columns: Query, Calls, Mean (ms), Total (ms), Cache %, Score.
- **Sortable columns:** Calls, Mean (ms), Total (ms), Cache %, Score. Clicking a column header sorts ascending; clicking again toggles to descending. A `↕` arrow shows on unsorted columns; `↑` / `↓` on the active sort.
- Default sort: `score` descending (highest priority first).
- **Row click:** navigates to `/dashboard/query/:queryid` via React Router's `useNavigate`.
- **Query display:** shows the first 80 characters of `query_fingerprint` with `…` truncation. The full fingerprint is in the `title` attribute (native tooltip).
- Shows a centred "No slow queries found." message when `queries` is empty.

### `ScoreBadge` (internal)

Colour-coded score badge:

| Score range | Colour |
|-------------|--------|
| ≥ 70 | Red |
| 40–69 | Yellow |
| < 40 | Green |

---

## `ScanProgress`

Source: [`frontend/src/components/ScanProgress.tsx`](../../frontend/src/components/ScanProgress.tsx)

Renders a vertical step list showing the real-time progress of the query scan SSE stream.

### Props

```ts
interface ScanProgressProps {
  steps: ScanStep[]   // events received so far
  scanning: boolean   // whether the scan is still in progress
}
```

### Behaviour

The five steps are always shown in a fixed order:

```
connect → check_extension → check_permissions → fetch_queries → result
```

For each step:
- **Not yet reached:** empty circle, muted text.
- **Active (scanning + next in sequence):** spinning indigo ring.
- **Completed ok/warning:** green ✓ badge.
- **Failed:** red ✗ badge.

For `warning` and `fail` events, the step's `data.message` is shown as a small coloured sub-label below the step name.

When `fetch_queries` completes successfully, `data.count` is displayed (e.g. "42 queries found").

---

## `PlanTree`

Source: [`frontend/src/components/PlanTree.tsx`](../../frontend/src/components/PlanTree.tsx)

Renders the hierarchical EXPLAIN plan tree returned by the backend.

### Props

```ts
interface PlanTreeProps {
  plan: Record<string, unknown>[] | null
}
```

When `plan` is `null` or empty, a "EXPLAIN not available" placeholder is shown.

### Behaviour

Recursively renders `PlanNode` objects. Each node is shown as a card with:

| Field | Shown when |
|-------|-----------|
| `Node Type` | always (title) |
| `Startup Cost .. Total Cost` | always |
| `Plan Rows` | always |
| `Actual Rows` | only when present (requires `ANALYZE true`) |
| `Shared Hit Blocks` | only when present |
| `Shared Read Blocks` | only when present |

Child nodes (`Plans`) are rendered indented by 20 px per depth level with a left border connector line.

### `PlanNodeCard` (internal)

```ts
function PlanNodeCard({ node, depth }: { node: PlanNode; depth: number })
```

Renders a single node and recursively renders its children.

---

## `FixCard`

Source: [`frontend/src/components/FixCard.tsx`](../../frontend/src/components/FixCard.tsx)

Renders one fix recommendation card.

### Props

```ts
{ rec: FixRecommendation }
```

### Behaviour

Displays:
1. **Severity badge** — colour-coded (`high` = red, `medium` = yellow, `low` = slate).
2. **Title** — recommendation title.
3. **Explanation** — paragraph describing the problem and proposed fix.
4. **SQL snippet** — monospaced code block with a **Copy** button that writes `rec.sql` to the clipboard via `navigator.clipboard.writeText`.

The SQL block uses dark background (`#0f172a`) and `whitespace-pre-wrap` to preserve formatting.
