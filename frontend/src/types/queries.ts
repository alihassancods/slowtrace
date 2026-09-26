export interface SlowQuery {
  queryid: string
  query: string
  query_fingerprint: string
  calls: number
  mean_exec_time_ms: number
  total_exec_time_ms: number
  stddev_exec_time_ms: number
  rows_per_call: number
  shared_blks_hit: number
  shared_blks_read: number
  cache_hit_ratio: number
  score: number
}

export interface ScanError {
  step: string
  message: string
}

export interface QueryReport {
  connection_id: string
  total_queries: number
  queries: SlowQuery[]
  errors: ScanError[]
}

export interface ScanStep {
  step: string
  status: 'ok' | 'warning' | 'fail'
  data: Record<string, unknown>
}
