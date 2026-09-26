export interface PlanNode {
  'Node Type': string
  'Startup Cost': number
  'Total Cost': number
  'Plan Rows': number
  'Actual Rows'?: number
  'Shared Hit Blocks'?: number
  'Shared Read Blocks'?: number
  Plans?: PlanNode[]
  [key: string]: unknown
}

export interface ExplainError {
  step: string
  message: string
}

export interface QueryDetail {
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
