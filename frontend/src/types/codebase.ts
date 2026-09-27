/**
 * Shapes for the Semgrep-driven codebase scan flow.
 *
 *   POST /api/codebase/scan  { repo_url, github_token?, connection_id? }
 *     → text/event-stream frames of `{ stage, data }`:
 *         started  { message, repo_url }
 *         progress { message, elapsed_seconds }
 *         complete { full scan result }
 *         error    { error, message, detail }
 *
 *   GET /api/codebase/results?repo_url=...
 *   GET /api/codebase/smell/{smell_id}?repo_url=...
 *
 * There is no scan id and no polling: the single POST streams until it finishes.
 * Framework detection and per-file progress events are gone — Semgrep reports
 * findings, not stages of a hand-written scanner.
 */

export type CodebaseSeverity = 'critical' | 'warning' | 'info'

/** Actionable failure kinds the screen maps to specific copy. */
export type CodebaseErrorKind =
  | 'invalid_url'
  | 'private'
  | 'not_found'
  | 'rate_limited'
  | 'network'
  | 'scan_timeout'
  | 'semgrep_error'
  | 'semgrep_not_installed'
  | 'rules_missing'
  | 'unknown'

/** SSE stage names emitted by POST /api/codebase/scan. */
export type CodebaseScanStage = 'started' | 'progress' | 'complete' | 'error'

export interface CodebaseScanRequest {
  repo_url: string
  github_token?: string
  connection_id?: string
}

/** Payload of the `started` and `progress` stages. */
export interface CodebaseProgressData {
  message?: string
  repo_url?: string
  elapsed_seconds?: number
  /** True for an advisory warning (e.g. a very large repository). */
  warning?: boolean
}

/** One envelope frame from the scan stream. */
export interface CodebaseStageEvent {
  stage: CodebaseScanStage
  data: CodebaseProgressData | CodebaseScanResult
}

/** A single Semgrep finding, enriched by the backend's SMELL_INFO map. */
export interface CodebaseFinding {
  /** Rule id from slowtrace-rules.yml, e.g. "django-n-plus-one-loop". */
  rule_id: string
  /** Grouping key, e.g. "n_plus_one"; registry rules report "general". */
  smell_id: string
  /** Repo-relative path, e.g. "shop/views.py". */
  file: string
  line_start: number
  line_end: number
  /** The matched source, read back out of the clone. */
  code: string
  /** Semgrep's message for this finding, with metavariables interpolated. */
  message: string
  /** Semgrep's own ERROR | WARNING | INFO. */
  raw_severity: string
  /** Example fix from rule metadata; "" when the rule has none. */
  fix_code: string
  category: string
  /** Direct link to the exact line(s) on GitHub. */
  github_url: string
  name: string
  severity: CodebaseSeverity
  impact: string
  docs_url: string
}

/** All findings of one smell type — the `grouped_by_type` values. */
export interface CodebaseSmellGroup {
  smell_id: string
  name: string
  severity: CodebaseSeverity
  impact: string
  fix_code: string
  count: number
  findings: CodebaseFinding[]
}

/** A finding matched to a SQLCommenter-traced slow query. */
export interface CodebaseCorrelation {
  finding: CodebaseFinding
  slow_query?: {
    query?: string
    mean_exec_time_ms?: number
    calls?: number
    code_location?: string | null
  }
  match_type: string
  confidence: string
  combined_message: string
}

/** The `complete` stage payload (also what GET /results returns as `data`). */
export interface CodebaseScanResult {
  success: boolean
  repo_url?: string
  scan_duration_seconds?: number
  files_scanned?: number
  total_findings?: number
  critical_count?: number
  warning_count?: number
  info_count?: number
  findings?: CodebaseFinding[]
  grouped_by_type?: Record<string, CodebaseSmellGroup>
  priority_fixes?: CodebaseSmellGroup[]
  correlations?: CodebaseCorrelation[]
  /** "semgrep" — labels the engine in the UI copy. */
  scanner?: string
  /** Advisory note when the repository is large enough to scan slowly. */
  size_warning?: string | null
  /** Error kind when success is false — see CodebaseErrorKind. */
  error?: string
  /** Display-ready copy for that error. */
  message?: string
  /** Setup command to show when Semgrep itself is missing. */
  install_command?: string
  /** Technical detail for logs only; never rendered. */
  detail?: string
}
