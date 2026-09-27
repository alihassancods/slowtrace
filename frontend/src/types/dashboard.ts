export interface DashboardError {
  step: string
  message: string
}

export interface CheckResult {
  name: string
  status: string
  value: number | null
  unit: string | null
  threshold: number | null
  message: string
  deduction: number
}

export interface Deduction {
  check: string
  points: number
  reason: string
}

export interface HealthError {
  check: string
  message: string
}

export interface HealthSummary {
  connection_id: string
  score: number | null
  grade: string | null
  checks: CheckResult[]
  deductions: Deduction[]
  errors: HealthError[]
}

export interface TopQuery {
  queryid: string
  query_fingerprint: string
  calls: number
  mean_exec_time_ms: number
  total_exec_time_ms: number
  cache_hit_ratio: number
  score: number
}

export interface QuerySummary {
  total_queries: number
  top_queries: TopQuery[]
  avg_score: number
  high_priority_count: number
  total_exec_time_ms: number
  errors: DashboardError[]
}

export interface DashboardReport {
  connection_id: string
  health: HealthSummary | null
  queries: QuerySummary | null
  errors: DashboardError[]
}
