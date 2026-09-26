/**
 * Shapes for the scan SSE stream (`GET /api/scan/{connection_id}`) and for the
 * snapshot persisted between visits.
 */

export interface ScanConnectedData {
  version?: string
  size?: string
  table_count?: number
  total_rows?: number
  error?: string
}

export interface ScanHealthCheck {
  check: string
  status: 'ok' | 'warning' | 'fail'
  message?: string
  [key: string]: unknown
}

/**
 * Only the slow-query fields the scan page renders. The full query list is
 * deliberately not persisted — the page shows counts and a summary, not rows.
 */
export interface ScanSlowQuerySummary {
  count: number
  total_wasted_minutes: number
  human_description: string
}

export interface ScanCompleteData {
  health_score: number
  critical_count: number
  warning_count: number
  healthy_count: number
  quick_wins: { check: string; fix: string }[]
}

export type ScanStage = 'connected' | 'health_check' | 'slow_queries' | 'complete'

export interface ScanSseEvent {
  stage: ScanStage
  data: Record<string, unknown>
}

/** A finished (or partially finished) scan, kept so revisits need no API call. */
export interface ScanSnapshot {
  connectionId: string
  connectedData: ScanConnectedData | null
  healthChecks: ScanHealthCheck[]
  slowQuerySummary: ScanSlowQuerySummary | null
  completeData: ScanCompleteData | null
  updatedAt: number
}
