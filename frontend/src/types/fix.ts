export interface FixError {
  step: string
  message: string
}

export interface FixRecommendation {
  id: string
  title: string
  severity: 'high' | 'medium' | 'low'
  explanation: string
  sql: string
}

export interface FixReport {
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
