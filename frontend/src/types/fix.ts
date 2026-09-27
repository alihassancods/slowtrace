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

export type FixStatus = 'success' | 'auto_rolled_back' | 'failed'

/** Normalised result handed from FixPage → FixResultPage via router state. */
export interface FixResultState {
  status: FixStatus
  connectionId: string
  queryid: string
  // success
  health_before?: number
  health_after?: number
  health_improvement?: number
  query_time_before_ms?: number
  query_time_after_ms?: number
  speedup_factor?: number
  time_saved_per_day_minutes?: number
  can_rollback?: boolean
  rollback_available_until?: string
  // auto_rolled_back
  reason?: string
  // failed
  error?: string
}

export interface RollbackResponse {
  status: string
  message: string
}
