# Frontend Types — `frontend/src/types/`

## Overview

The [`frontend/src/types/`](../../frontend/src/types/) directory contains TypeScript interfaces that mirror the Pydantic response models on the backend. All types are plain interfaces (no classes, no runtime code) and are imported only for type-checking.

---

## `types/queries.ts`

Source: [`frontend/src/types/queries.ts`](../../frontend/src/types/queries.ts)

### `SlowQuery`

Represents one row from `pg_stat_statements` after scoring and fingerprinting.

| Field | Type | Description |
|-------|------|-------------|
| `queryid` | `string` | Internal Postgres query identifier |
| `query` | `string` | Raw SQL text |
| `query_fingerprint` | `string` | Normalised, literal-free SQL |
| `calls` | `number` | Number of times the query was executed |
| `mean_exec_time_ms` | `number` | Average execution time in milliseconds |
| `total_exec_time_ms` | `number` | Total cumulative execution time |
| `stddev_exec_time_ms` | `number` | Standard deviation of execution times |
| `rows_per_call` | `number` | Average rows returned per call |
| `shared_blks_hit` | `number` | Buffer-cache hits |
| `shared_blks_read` | `number` | Physical disk reads |
| `cache_hit_ratio` | `number` | `shared_blks_hit / (hit + read) * 100` |
| `score` | `number` | Badness score 0–100 (higher = worse) |

### `ScanError`

```ts
interface ScanError {
  step: string
  message: string
}
```

### `QueryReport`

```ts
interface QueryReport {
  connection_id: string
  total_queries: number
  queries: SlowQuery[]
  errors: ScanError[]
}
```

### `ScanStep`

Represents one SSE event emitted by the query scan stream.

```ts
interface ScanStep {
  step: string
  status: 'ok' | 'warning' | 'fail'
  data: Record<string, unknown>
}
```

---

## `types/dashboard.ts`

Source: [`frontend/src/types/dashboard.ts`](../../frontend/src/types/dashboard.ts)

### `CheckResult`

One health check result.

| Field | Type | Description |
|-------|------|-------------|
| `name` | `string` | Check identifier (e.g. `"cache_hit_ratio"`) |
| `status` | `string` | `"ok"`, `"warning"`, or `"fail"` |
| `value` | `number \| null` | Measured value (e.g. cache ratio %) |
| `unit` | `string \| null` | Unit label (e.g. `"%"`, `"s"`) |
| `threshold` | `number \| null` | The threshold value that was evaluated |
| `message` | `string` | Human-readable result description |
| `deduction` | `number` | Points deducted from the health score |

### `Deduction`

```ts
interface Deduction {
  check: string
  points: number
  reason: string
}
```

### `HealthError`

```ts
interface HealthError {
  check: string
  message: string
}
```

### `HealthSummary`

```ts
interface HealthSummary {
  connection_id: string
  score: number | null
  grade: string | null       // "A" | "B" | "C" | "D" | "F"
  checks: CheckResult[]
  deductions: Deduction[]
  errors: HealthError[]
}
```

### `TopQuery`

Slim query summary shown in the dashboard table (top 10 by score).

```ts
interface TopQuery {
  queryid: string
  query_fingerprint: string
  calls: number
  mean_exec_time_ms: number
  total_exec_time_ms: number
  cache_hit_ratio: number
  score: number
}
```

### `QuerySummary`

```ts
interface QuerySummary {
  total_queries: number
  top_queries: TopQuery[]
  avg_score: number
  high_priority_count: number    // queries with score >= 70
  total_exec_time_ms: number
  errors: DashboardError[]
}
```

### `DashboardReport`

```ts
interface DashboardReport {
  connection_id: string
  health: HealthSummary | null
  queries: QuerySummary | null
  errors: DashboardError[]
}
```

---

## `types/explain.ts`

Source: [`frontend/src/types/explain.ts`](../../frontend/src/types/explain.ts)

### `PlanNode`

Typed representation of one node in the `EXPLAIN FORMAT JSON` output.

| Field | Type | Description |
|-------|------|-------------|
| `'Node Type'` | `string` | e.g. `"Seq Scan"`, `"Hash Join"`, `"Index Scan"` |
| `'Startup Cost'` | `number` | Estimated cost before first row is returned |
| `'Total Cost'` | `number` | Estimated total cost |
| `'Plan Rows'` | `number` | Planner's row estimate |
| `'Actual Rows'?` | `number` | Actual rows (only with `ANALYZE true`) |
| `'Shared Hit Blocks'?` | `number` | Buffer-cache hits for this node |
| `'Shared Read Blocks'?` | `number` | Physical reads for this node |
| `Plans?` | `PlanNode[]` | Child nodes |
| `[key: string]` | `unknown` | All other plan fields passthrough |

### `QueryDetail`

```ts
interface QueryDetail {
  connection_id: string
  queryid: string
  query: string | null
  query_fingerprint: string | null
  calls: number | null
  mean_exec_time_ms: number | null
  total_exec_time_ms: number | null
  stddev_exec_time_ms: number | null
  rows_per_call: number | null
  shared_blks_hit: number | null
  shared_blks_read: number | null
  cache_hit_ratio: number | null
  score: number | null
  plan: Record<string, unknown>[] | null
  errors: ExplainError[]
}
```

All statistics are nullable because they will be `null` if the query was not found or the connection failed.

---

## `types/fix.ts`

Source: [`frontend/src/types/fix.ts`](../../frontend/src/types/fix.ts)

### `FixRecommendation`

```ts
interface FixRecommendation {
  id: string                           // e.g. "seq_scan_large_table"
  title: string                        // short display title
  severity: 'high' | 'medium' | 'low'
  explanation: string                  // paragraph describing the issue
  sql: string                          // ready-to-copy SQL snippet
}
```

### `FixReport`

```ts
interface FixReport {
  connection_id: string
  queryid: string
  query_fingerprint: string | null
  score: number | null
  mean_exec_time_ms: number | null
  total_exec_time_ms: number | null
  cache_hit_ratio: number | null
  recommendations: FixRecommendation[]
  errors: FixError[]
}
```
