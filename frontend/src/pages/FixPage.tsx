import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams, Link } from 'react-router-dom'
import type { FixResultState } from '@/types/fix'
import Breadcrumb from '@/components/Breadcrumb'
import ConnectionLostCard from '@/components/ConnectionLostCard'
import ErrorCard from '@/components/ErrorCard'
import { Skeleton, SkeletonText } from '@/components/Skeleton'
import { friendlyError, isConnectionError } from '@/lib/errors'
import { useActiveConnection } from '@/lib/activeConnection'

// ---------------------------------------------------------------------------
// API types
// ---------------------------------------------------------------------------

interface Problem {
  type: string
  table?: string
  columns?: string[]
  calls_per_day?: number
  avg_rows_returned?: number
}

interface ExpectedImpact {
  before_ms: number
  after_ms: number
  speedup_factor: number
  time_saved_per_day_minutes: number
  /** 'hypopg_planner' when a hypothetical index proved the numbers. */
  estimation_basis?: 'heuristic' | 'hypopg_planner'
}

interface HypopgValidation {
  validated: boolean
  reason: string | null
  /** Database's own error text when a step failed; diagnostic only. */
  detail?: string | null
  baseline_cost: number | null
  improved_cost: number | null
  cost_reduction_percent: number | null
  cost_reduction_min?: number | null
  cost_reduction_max?: number | null
  baseline_scan_type: string | null
  improved_scan_type: string | null
  planner_would_use_index: boolean | null
  trials_run?: number
  trials_using_index?: number | null
  /** Stand-in values the planner was shown for each `$n` in the query. */
  sample_values?: Record<string, string> | null
  estimated_size_bytes: number | null
  proof_statement: string | null
}

interface GenerateResponse {
  problem: Problem
  fix_sql: string
  rollback_sql: string
  expected_impact: ExpectedImpact
  ai_explanation: string
  hypopg_validation?: HypopgValidation | null
}

// SSE apply events: { event, data }
interface SseApplyEvent {
  event: 'started' | 'executing' | 'success' | 'auto_rolled_back' | 'error'
  data: Record<string, unknown>
}

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

async function generateFix(connectionId: string, queryid: string): Promise<GenerateResponse> {
  const resp = await fetch('/api/fixes/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ connection_id: connectionId, queryid }),
  })
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({})) as { detail?: string }
    throw new Error(detail.detail ?? `HTTP ${resp.status}`)
  }
  return resp.json() as Promise<GenerateResponse>
}

async function saveFix(
  connectionId: string,
  queryid: string,
  fixSql: string,
  rollbackSql: string,
  impact?: ExpectedImpact,
): Promise<string> {
  const resp = await fetch('/api/fixes/save', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      connection_id: connectionId,
      queryid,
      fix_sql: fixSql,
      rollback_sql: rollbackSql,
      query_time_before_ms: impact?.before_ms,
      query_time_after_ms: impact?.after_ms,
      time_saved_per_day_minutes: impact?.time_saved_per_day_minutes,
    }),
  })
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
  const data = await resp.json() as { fix_id: string }
  return data.fix_id
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const PROBLEM_SUBTITLES: Record<string, string> = {
  missing_index: 'Missing database index detected',
  select_star: 'Fetching unnecessary columns',
  n_plus_one: 'N+1 query pattern detected',
  missing_limit: 'Query returns unlimited rows',
  seq_scan: 'Sequential table scan detected',
}

function problemSubtitle(type: string): string {
  return PROBLEM_SUBTITLES[type] ?? `Problem detected: ${type}`
}

/** Derive risk level from fix SQL text — low if CONCURRENTLY, else medium. */
function deriveRiskLevel(fixSql: string): 'low' | 'medium' | 'high' {
  const upper = fixSql.toUpperCase()
  if (upper.includes('CONCURRENTLY') || upper.startsWith('--')) return 'low'
  if (upper.includes('DROP') || upper.includes('ALTER')) return 'high'
  return 'medium'
}

function isConcurrently(fixSql: string): boolean {
  return fixSql.toUpperCase().includes('CONCURRENTLY')
}

function isCodeChangeOnly(fixSql: string): boolean {
  return fixSql.trim().startsWith('--')
}

/** Why HypoPG could not prove the fix, phrased for the UI. */
const VALIDATION_REASONS: Record<string, string> = {
  hypopg_unavailable:
    'HypoPG is not installed on this database, so this improvement is estimated rather than proven.',
  explain_failed:
    "PostgreSQL could not plan this query, so this improvement is estimated rather than proven.",
  hypopg_create_failed:
    'HypoPG could not create the hypothetical index, so this improvement is estimated rather than proven.',
  no_index_target:
    'No concrete index columns were identified to test, so this improvement is estimated.',
}

function validationReasonText(reason: string | null): string {
  if (reason && VALIDATION_REASONS[reason]) return VALIDATION_REASONS[reason]
  return 'Hypothetical index testing was not possible, so this improvement is estimated.'
}

function formatBytes(bytes: number | null): string {
  if (bytes === null || bytes <= 0) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatCost(cost: number | null): string {
  if (cost === null) return '—'
  return cost.toLocaleString(undefined, { maximumFractionDigits: 0 })
}

/** Median reduction, widened to a range when several values were tested. */
function reductionLabel(v: HypopgValidation): string {
  const median = v.cost_reduction_percent
  if (median === null) return '0.0%'
  const min = v.cost_reduction_min ?? median
  const max = v.cost_reduction_max ?? median
  if (min === max) return `${median.toFixed(1)}%`
  return `${min.toFixed(1)}–${max.toFixed(1)}% (median ${median.toFixed(1)}%)`
}

function trialsNote(v: HypopgValidation): string {
  if (!v.trials_run || v.trials_run < 2 || v.trials_using_index === null) return ''
  return ` (${v.trials_using_index} of ${v.trials_run} sampled values)`
}

// ---------------------------------------------------------------------------
// Small shared components
// ---------------------------------------------------------------------------

function Spinner({ size = 'md' }: { size?: 'sm' | 'md' | 'lg' }) {
  const sz = size === 'sm' ? 'h-4 w-4' : size === 'lg' ? 'h-8 w-8' : 'h-5 w-5'
  return (
    <svg className={`${sz} animate-spin text-indigo-400`} viewBox="0 0 24 24" fill="none">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
    </svg>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <h2 className="text-base font-semibold text-slate-200">{title}</h2>
      {children}
    </section>
  )
}

function CopyButton({ text, label = 'Copy SQL' }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  function handleCopy() {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      if (timerRef.current) clearTimeout(timerRef.current)
      timerRef.current = setTimeout(() => setCopied(false), 2000)
    })
  }
  return (
    <button
      onClick={handleCopy}
      className={`shrink-0 rounded-lg px-3 py-1.5 text-xs font-semibold transition-all border ${
        copied
          ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30'
          : 'bg-slate-700 text-slate-300 border-slate-600 hover:bg-slate-600'
      }`}
    >
      {copied ? '✓ Copied!' : `📋 ${label}`}
    </button>
  )
}

function SqlBlock({ sql }: { sql: string }) {
  return (
    <pre className="overflow-x-auto rounded-xl bg-slate-950 border border-slate-700/60 px-5 py-4 text-xs font-mono leading-relaxed whitespace-pre-wrap break-all text-slate-200">
      {sql}
    </pre>
  )
}

// ---------------------------------------------------------------------------
// Section 3 — Expected Impact
// ---------------------------------------------------------------------------

function ImpactSection({ impact }: { impact: ExpectedImpact }) {
  const savedMin = impact.time_saved_per_day_minutes
  const savedDisplay =
    savedMin < 1
      ? `${(savedMin * 60).toFixed(0)}s`
      : `${savedMin.toFixed(1)} min`
  const provenByPlanner = impact.estimation_basis === 'hypopg_planner'

  return (
    <Section title="Expected Impact">
      <div className="grid grid-cols-2 gap-4">
        <div className="rounded-xl border border-slate-700 bg-slate-800/60 p-4">
          <p className="text-xs font-semibold uppercase tracking-widest text-slate-500 mb-1">Before</p>
          <p className="text-2xl font-extrabold tabular-nums text-red-400">
            {impact.before_ms.toFixed(1)}
            <span className="text-base font-semibold">ms</span>
          </p>
          <p className="text-xs text-slate-500 mt-0.5">average execution</p>
        </div>
        <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-4">
          <p className="text-xs font-semibold uppercase tracking-widest text-slate-500 mb-1">After</p>
          <p className="text-2xl font-extrabold tabular-nums text-emerald-400">
            ~{impact.after_ms.toFixed(1)}
            <span className="text-base font-semibold">ms</span>
          </p>
          <p className="text-xs text-slate-500 mt-0.5">
            {provenByPlanner ? 'planner-derived' : 'estimated'}
          </p>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-4 rounded-xl border border-slate-700 bg-slate-800/40 px-5 py-3">
        <span className="text-sm font-bold text-emerald-400">
          ⚡ {impact.speedup_factor.toFixed(1)}× faster
        </span>
        <span className="text-sm text-slate-400">
          Saves <span className="font-semibold text-slate-200">{savedDisplay}</span> per day
        </span>
        {provenByPlanner && (
          <span className="text-xs text-slate-500">
            derived from measured planner costs
          </span>
        )}
      </div>
    </Section>
  )
}

// ---------------------------------------------------------------------------
// Section 3b — HypoPG what-if validation
// ---------------------------------------------------------------------------

function ValidationSection({ validation }: { validation: HypopgValidation }) {
  const proven = validation.validated && validation.planner_would_use_index === true
  const tested = validation.validated

  const tone = proven
    ? 'border-emerald-500/40 bg-emerald-500/10'
    : tested
      ? 'border-yellow-500/40 bg-yellow-500/10'
      : 'border-slate-700 bg-slate-800/60'

  return (
    <Section title="Hypothetical Index Test">
      <div className={`rounded-xl border ${tone} p-5 space-y-4`}>
        <div className="flex items-center gap-2">
          <span
            className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-bold uppercase tracking-wide ${
              proven
                ? 'border-emerald-500/30 bg-emerald-500/20 text-emerald-300'
                : tested
                  ? 'border-yellow-500/30 bg-yellow-500/20 text-yellow-300'
                  : 'border-slate-600 bg-slate-700/60 text-slate-300'
            }`}
          >
            {proven ? '✅ VALIDATED' : tested ? '⚠️ LIMITED IMPACT' : 'ℹ️ NOT PROVEN'}
          </span>
          <span className="text-xs text-slate-400">
            Powered by HypoPG — no changes written to your database
          </span>
        </div>

        {tested ? (
          <>
            <div className="grid grid-cols-2 gap-4">
              <div className="rounded-xl border border-slate-700 bg-slate-900/60 p-4">
                <p className="text-xs font-semibold uppercase tracking-widest text-slate-500 mb-1">
                  Before (no index)
                </p>
                <p className="text-sm font-semibold text-red-300">
                  {validation.baseline_scan_type ?? '—'}
                </p>
                <p className="mt-1 text-lg font-extrabold tabular-nums text-slate-200">
                  {formatCost(validation.baseline_cost)}
                </p>
                <p className="text-xs text-slate-500">planner cost</p>
              </div>
              <div className="rounded-xl border border-emerald-500/30 bg-slate-900/60 p-4">
                <p className="text-xs font-semibold uppercase tracking-widest text-slate-500 mb-1">
                  After (hypothetical index)
                </p>
                <p className="text-sm font-semibold text-emerald-300">
                  {validation.improved_scan_type ?? '—'}
                  {proven && <span className="ml-1">✅</span>}
                </p>
                <p className="mt-1 text-lg font-extrabold tabular-nums text-slate-200">
                  {formatCost(validation.improved_cost)}
                </p>
                <p className="text-xs text-slate-500">planner cost</p>
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-sm">
              <span className="font-bold text-emerald-400">
                Cost reduction {reductionLabel(validation)}
              </span>
              <span className="text-slate-400">
                Planner would use index:{' '}
                <span className={proven ? 'font-semibold text-emerald-300' : 'font-semibold text-yellow-300'}>
                  {proven ? '✅ Confirmed' : '❌ No'}
                  {trialsNote(validation)}
                </span>
              </span>
              <span className="text-slate-400">
                Index size ≤{' '}
                <span className="font-semibold text-slate-200">
                  {formatBytes(validation.estimated_size_bytes)}
                </span>
                <span className="text-xs text-slate-500"> (HypoPG over-reports)</span>
              </span>
            </div>

            {validation.sample_values &&
              Object.keys(validation.sample_values).length > 0 && (
                <p className="text-xs leading-relaxed text-slate-500">
                  Tested with representative values sampled from your table:{' '}
                  <span className="font-mono text-slate-400">
                    {Object.entries(validation.sample_values)
                      .map(([token, value]) => `${token} = ${value}`)
                      .join(', ')}
                  </span>
                </p>
              )}

            {validation.proof_statement && (
              <p className="text-sm leading-relaxed text-slate-300">
                {validation.proof_statement}
              </p>
            )}
          </>
        ) : (
          <p className="text-sm leading-relaxed text-slate-400">
            {validationReasonText(validation.reason)}
          </p>
        )}

        {!tested && validation.detail && (
          <p className="text-xs leading-relaxed text-slate-500">
            Database said: <span className="font-mono">{validation.detail}</span>
          </p>
        )}
      </div>
    </Section>
  )
}

// ---------------------------------------------------------------------------
// Section 4 — Safety
// ---------------------------------------------------------------------------

function SafetySection({ fixSql }: { fixSql: string }) {
  const risk = deriveRiskLevel(fixSql)
  const concurrently = isConcurrently(fixSql)
  const codeOnly = isCodeChangeOnly(fixSql)
  const isIndex = fixSql.toUpperCase().includes('CREATE INDEX')

  const riskBadge =
    risk === 'low'
      ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30'
      : risk === 'medium'
        ? 'bg-yellow-500/20 text-yellow-300 border-yellow-500/30'
        : 'bg-red-500/20 text-red-300 border-red-500/30'
  const riskIcon = risk === 'low' ? '🟢' : risk === 'medium' ? '🟡' : '🔴'

  const checks = [
    { label: 'Non-blocking (uses CONCURRENTLY)', show: concurrently },
    { label: 'Reversible — can be undone', show: isIndex || codeOnly },
    { label: 'No data modification', show: isIndex || codeOnly },
    { label: 'No downtime required', show: concurrently || codeOnly },
  ].filter((c) => c.show)

  return (
    <Section title="Safety">
      <div className="rounded-xl border border-slate-700 bg-slate-800/60 p-5 space-y-3">
        <div className="flex items-center gap-2">
          <span className="text-sm">Risk level:</span>
          <span
            className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-bold uppercase tracking-wide ${riskBadge}`}
          >
            {riskIcon} {risk.toUpperCase()}
          </span>
        </div>
        {checks.length > 0 && (
          <ul className="space-y-1.5 pt-1">
            {checks.map((c) => (
              <li key={c.label} className="flex items-center gap-2 text-sm text-slate-300">
                <span className="text-emerald-400">✅</span>
                {c.label}
              </li>
            ))}
          </ul>
        )}
      </div>
    </Section>
  )
}

// ---------------------------------------------------------------------------
// Confirmation modal
// ---------------------------------------------------------------------------

function ConfirmModal({
  fixSql,
  onCancel,
  onConfirm,
}: {
  fixSql: string
  onCancel: () => void
  onConfirm: () => void
}) {
  // Truncate for display
  const preview = fixSql.length > 200 ? fixSql.slice(0, 200) + '\n...' : fixSql

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm px-4">
      <div className="w-full max-w-lg rounded-2xl border border-slate-700 bg-slate-900 p-6 shadow-2xl">
        <h3 className="text-lg font-bold text-white mb-1">Apply this fix?</h3>
        <p className="text-sm text-slate-400 mb-4">
          This will execute the following SQL on your database:
        </p>
        <pre className="mb-5 overflow-x-auto rounded-lg bg-slate-950 border border-slate-700/60 px-4 py-3 text-xs font-mono text-slate-200 whitespace-pre-wrap break-all leading-relaxed">
          {preview}
        </pre>
        <div className="flex gap-3">
          <button
            onClick={onCancel}
            className="flex-1 rounded-xl border border-slate-600 py-2.5 text-sm font-semibold text-slate-300 transition-colors hover:border-slate-400 hover:text-white"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className="flex-1 rounded-xl bg-emerald-600 py-2.5 text-sm font-bold text-white shadow transition-colors hover:bg-emerald-500"
          >
            ✅ Yes, Apply Fix
          </button>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Apply progress view
// ---------------------------------------------------------------------------

type ApplyStep =
  | { status: 'pending'; label: string }
  | { status: 'running'; label: string }
  | { status: 'done'; label: string }
  | { status: 'error'; label: string }

function ApplyProgress({ steps }: { steps: ApplyStep[] }) {
  return (
    <div className="rounded-xl border border-slate-700 bg-slate-800/60 p-5 space-y-3">
      {steps.map((step, i) => (
        <div key={i} className="flex items-center gap-3 text-sm">
          {step.status === 'done' && <span className="text-emerald-400 text-base">✅</span>}
          {step.status === 'running' && <Spinner size="sm" />}
          {step.status === 'pending' && (
            <span className="h-4 w-4 rounded-full border border-slate-600" />
          )}
          {step.status === 'error' && <span className="text-red-400 text-base">❌</span>}
          <span
            className={
              step.status === 'done'
                ? 'text-slate-200'
                : step.status === 'running'
                  ? 'text-indigo-300 font-medium'
                  : step.status === 'error'
                    ? 'text-red-300'
                    : 'text-slate-600'
            }
          >
            {step.label}
          </span>
        </div>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

function FixSkeleton() {
  return (
    <div className="space-y-10" aria-busy="true" aria-label="Generating fix suggestion">
      {/* Suggestion */}
      <div>
        <Skeleton className="mb-3 h-5 w-44" />
        <div className="rounded-xl border border-slate-700 bg-slate-800/60 p-5">
          <Skeleton className="h-4 w-56" />
          <SkeletonText className="mt-3" lines={3} />
        </div>
      </div>

      {/* SQL */}
      <div>
        <Skeleton className="mb-3 h-5 w-20" />
        <Skeleton className="h-28 w-full" />
      </div>

      {/* Impact */}
      <div>
        <Skeleton className="mb-3 h-5 w-32" />
        <div className="grid grid-cols-2 gap-4">
          {[0, 1].map((i) => (
            <div key={i} className="rounded-xl border border-slate-700 bg-slate-800/60 p-5">
              <Skeleton className="h-3 w-16" />
              <Skeleton className="mt-3 h-8 w-24" />
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function FixPage() {
  const { connectionId, queryid, id: legacyId } = useParams<{
    connectionId?: string
    queryid?: string
    id?: string
  }>()
  const navigate = useNavigate()
  const activeConnection = useActiveConnection()

  const connId = connectionId ?? activeConnection?.id ?? ''
  const qid = queryid ?? legacyId ?? ''
  const backTo = connId && qid ? `/query/${connId}/${qid}` : '/dashboard'

  // ── Phase: generate → confirm → applying → done|error ──────────────────
  type Phase = 'loading' | 'ready' | 'confirm' | 'applying' | 'success' | 'error' | 'no_params'
  const [phase, setPhase] = useState<Phase>(!connId || !qid ? 'no_params' : 'loading')
  const [genData, setGenData] = useState<GenerateResponse | null>(null)
  const [genError, setGenError] = useState<unknown>(null)
  const [applySteps, setApplySteps] = useState<ApplyStep[]>([])
  const [applyError, setApplyError] = useState<string | null>(null)
  const [autoRollbackReason, setAutoRollbackReason] = useState<string | null>(null)

  // Load generate on mount (and on retry)
  const reload = useCallback(() => {
    if (!connId || !qid) return
    setPhase('loading')
    setGenError(null)
    setApplyError(null)
    setAutoRollbackReason(null)
    setGenData(null)
    generateFix(connId, qid)
      .then((data) => {
        setGenData(data)
        setPhase('ready')
      })
      .catch((err: unknown) => {
        setGenError(err)
        setPhase('error')
      })
  }, [connId, qid])

  useEffect(() => {
    reload()
  }, [reload])

  // ── Handlers ─────────────────────────────────────────────────────────────

  function handleApplyClick() {
    setPhase('confirm')
  }

  async function handleConfirm() {
    if (!genData || !connId || !qid) return
    setPhase('applying')
    setApplySteps([
      { status: 'running', label: 'Saving fix…' },
      { status: 'pending', label: 'Fix SQL executed' },
      { status: 'pending', label: 'Building index…' },
      { status: 'pending', label: 'Measuring impact…' },
    ])

    // Step 1: save fix to history to obtain a fix_id
    let savedFixId: string
    try {
      savedFixId = await saveFix(
        connId,
        qid,
        genData.fix_sql,
        genData.rollback_sql,
        genData.expected_impact,
      )
    } catch (err: unknown) {
      console.error('Failed to save fix before applying:', err)
      setApplySteps([
        { status: 'error', label: 'Failed to save fix — cannot apply.' },
      ])
      setApplyError('We could not save the fix before applying it. Your database was not changed.')
      setPhase('error')
      return
    }

    setApplySteps([
      { status: 'done', label: 'Fix saved' },
      { status: 'running', label: 'Connecting to database…' },
      { status: 'pending', label: 'Executing fix SQL…' },
      { status: 'pending', label: 'Measuring impact…' },
    ])

    // Step 2: call apply SSE stream
    let resp: Response
    try {
      resp = await fetch('/api/fixes/apply', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ connection_id: connId, fix_id: savedFixId }),
      })
    } catch (err: unknown) {
      console.error('Network error while applying fix:', err)
      setApplySteps((prev) => prev.map((s, i) => (i === 1 ? { ...s, status: 'error', label: 'Network error' } : s)))
      setApplyError('We lost the connection while applying the fix. Your database was not changed.')
      setPhase('error')
      return
    }

    if (!resp.ok || !resp.body) {
      console.error(`Apply request failed with HTTP ${resp.status}`)
      setApplyError('The server rejected the request to apply the fix. Your database was not changed.')
      setPhase('error')
      return
    }

    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''

    const terminal = await (async (): Promise<FixResultState | null> => {
      while (true) {
        const { done: streamDone, value } = await reader.read()
        if (streamDone) break
        buf += decoder.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop() ?? ''

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const raw = line.slice(6).trim()
          if (!raw) continue
          let evt: SseApplyEvent
          try { evt = JSON.parse(raw) as SseApplyEvent } catch { continue }

          if (evt.event === 'started') {
            setApplySteps([
              { status: 'done', label: 'Fix saved' },
              { status: 'done', label: 'Connected to database' },
              { status: 'running', label: 'Executing fix SQL…' },
              { status: 'pending', label: 'Measuring impact…' },
            ])
          } else if (evt.event === 'executing') {
            setApplySteps([
              { status: 'done', label: 'Fix saved' },
              { status: 'done', label: 'Connected to database' },
              { status: 'running', label: (evt.data.message as string) ?? 'Executing fix SQL…' },
              { status: 'pending', label: 'Measuring impact…' },
            ])
          } else if (evt.event === 'success') {
            setApplySteps([
              { status: 'done', label: 'Fix saved' },
              { status: 'done', label: 'Fix SQL executed' },
              { status: 'done', label: 'Index built' },
              { status: 'done', label: 'Impact measured' },
            ])
            setPhase('success')
            return {
              status: 'success',
              connectionId: connId,
              queryid: qid,
              health_before: evt.data.health_before as number | undefined,
              health_after: evt.data.health_after as number | undefined,
              health_improvement: evt.data.health_improvement as number | undefined,
              query_time_before_ms: evt.data.query_time_before_ms as number | undefined,
              query_time_after_ms: evt.data.query_time_after_ms as number | undefined,
              speedup_factor: evt.data.speedup_factor as number | undefined,
              time_saved_per_day_minutes: evt.data.time_saved_per_day_minutes as number | undefined,
              can_rollback: evt.data.can_rollback as boolean | undefined,
              rollback_available_until: evt.data.rollback_available_until as string | undefined,
            }
          } else if (evt.event === 'auto_rolled_back') {
            setApplySteps([
              { status: 'done', label: 'Fix saved' },
              { status: 'done', label: 'Fix SQL executed' },
              { status: 'error', label: 'Health dropped — auto-rolled back' },
            ])
            const reason = (evt.data.reason as string) ?? 'The fix lowered your database health, so SlowTrace reversed it automatically.'
            setAutoRollbackReason(reason)
            setPhase('error')
            return {
              status: 'auto_rolled_back',
              connectionId: connId,
              queryid: qid,
              health_before: evt.data.health_before as number | undefined,
              health_after: evt.data.health_after as number | undefined,
              reason,
            }
          } else if (evt.event === 'error') {
            const detail = (evt.data.message as string)
              ?? (evt.data.error as string)
              ?? 'Apply failed.'
            console.error('Apply failed:', detail)
            setApplySteps((prev) =>
              prev.map((s) => s.status === 'running' ? { ...s, status: 'error', label: 'Fix could not be applied' } : s),
            )
            setApplyError('We could not apply this fix. Your database was not changed.')
            setPhase('error')
            return {
              status: 'failed',
              connectionId: connId,
              queryid: qid,
              error: 'We could not apply this fix. Your database was not changed.',
            }
          }
        }
      }
      return null
    })()

    let resultState: FixResultState
    if (terminal) {
      resultState = terminal
    } else {
      // Stream ended without a terminal event — treat as an apply failure
      const msg = 'The connection closed before the fix finished. Your database was not changed.'
      setApplyError(msg)
      setPhase('error')
      resultState = { status: 'failed', connectionId: connId, queryid: qid, error: msg }
    }

    // Navigate to result page after a short pause
    if (savedFixId) {
      setTimeout(() => navigate(`/result/${savedFixId}`, { state: resultState }), 1500)
    }
  }

  // ── Render helpers ────────────────────────────────────────────────────────

  if (phase === 'no_params') {
    return (
      <div className="mx-auto max-w-3xl px-4 py-10">
        <div className="rounded-xl border border-red-500/40 bg-red-500/10 p-4 text-sm text-red-300">
          Missing connection ID or query ID. Navigate here from the query detail page.
        </div>
      </div>
    )
  }

  if (phase === 'loading') {
    return (
      <div className="mx-auto max-w-3xl px-4 py-10">
        <Breadcrumb
          items={[
            { label: 'Dashboard', to: '/dashboard' },
            { label: 'Query Detail', to: backTo },
            { label: 'Fix Suggestion' },
          ]}
        />
        <div className="mt-8">
          <FixSkeleton />
        </div>
        <p className="mt-6 text-center text-xs text-slate-600">
          Analysing the query and generating an AI fix suggestion — this usually takes 5–15 seconds.
        </p>
      </div>
    )
  }

  return (
    <>
      {phase === 'confirm' && genData && (
        <ConfirmModal
          fixSql={genData.fix_sql}
          onCancel={() => setPhase('ready')}
          onConfirm={() => void handleConfirm()}
        />
      )}

      <div className="mx-auto max-w-3xl px-4 py-10 space-y-10">
        {/* Breadcrumb */}
        <Breadcrumb
          items={[
            { label: 'Dashboard', to: '/dashboard' },
            { label: 'Query Detail', to: backTo },
            { label: 'Fix Suggestion' },
          ]}
        />

        {/* Generate failed */}
        {phase === 'error' && !applyError && !autoRollbackReason && (
          isConnectionError(genError) ? (
            <ConnectionLostCard onReconnect={() => navigate('/')} />
          ) : (
            <ErrorCard
              title="Could not generate a fix"
              message={friendlyError(genError, 'We could not analyse this query. Please try again.')}
              onRetry={reload}
            />
          )
        )}

        {/* Fix applied, then reversed automatically */}
        {phase === 'error' && autoRollbackReason && (
          <div className="rounded-xl border border-yellow-500/40 bg-yellow-500/10 p-5">
            <p className="text-sm font-semibold text-yellow-300">
              ⚠️ This fix was rolled back automatically
            </p>
            <p className="mt-1 text-sm leading-relaxed text-yellow-200/90">{autoRollbackReason}</p>
          </div>
        )}

        {/* Apply failed */}
        {phase === 'error' && applyError && (
          <ErrorCard
            title="Could not apply this fix"
            message={applyError}
            onRetry={() => void handleConfirm()}
          />
        )}

        {genData && (
          <>
            {/* ── Section 1: Problem ────────────────────────────────────── */}
            <Section title="🔧 AI Fix Suggestion">
              <div className="rounded-xl border border-slate-700 bg-slate-800/60 p-5 space-y-3">
                <div>
                  <p className="text-base font-semibold text-white">
                    {genData.problem.type
                      ? problemSubtitle(genData.problem.type)
                      : 'No specific problem detected'}
                  </p>
                  {genData.problem.table && (
                    <p className="mt-0.5 text-xs text-slate-500">
                      Table: <span className="font-mono text-indigo-300">{genData.problem.table}</span>
                      {genData.problem.columns && genData.problem.columns.length > 0 && (
                        <> · Columns: <span className="font-mono text-indigo-300">{genData.problem.columns.join(', ')}</span></>
                      )}
                    </p>
                  )}
                </div>
                <p className="text-sm leading-relaxed text-slate-300">{genData.ai_explanation}</p>
              </div>
            </Section>

            {genData.problem.type ? (
              <>
                {/* ── Section 2: The Fix ────────────────────────────────── */}
                <Section title="The Fix">
                  <div className="space-y-3">
                    <div className="rounded-xl border border-slate-700 bg-slate-950 overflow-hidden">
                      <div className="flex items-center justify-between gap-3 border-b border-slate-700/60 bg-slate-900 px-4 py-2">
                        <span className="text-xs font-medium text-slate-400">SQL</span>
                        <CopyButton text={genData.fix_sql} />
                      </div>
                      <SqlBlock sql={genData.fix_sql} />
                    </div>
                    <p className="text-sm text-slate-400 leading-relaxed">
                      {genData.problem.type === 'missing_index' || genData.problem.type === 'seq_scan'
                        ? 'Creates a B-tree index with CONCURRENTLY so existing queries are not blocked while it builds.'
                        : genData.problem.type === 'select_star'
                          ? 'Replace SELECT * with only the columns your application needs to reduce data transfer and allow better query planning.'
                          : genData.problem.type === 'n_plus_one'
                            ? 'Batch individual lookups into a single query to eliminate the N+1 round-trip overhead.'
                            : genData.problem.type === 'missing_limit'
                              ? 'Add LIMIT to prevent the database from returning unbounded result sets.'
                              : 'Apply this fix to resolve the detected performance problem.'}
                    </p>
                  </div>
                </Section>

                {/* ── Section 3: Hypothetical index test ────────────────── */}
                {genData.hypopg_validation && (
                  <ValidationSection validation={genData.hypopg_validation} />
                )}

                {/* ── Section 4: Expected Impact ────────────────────────── */}
                <ImpactSection impact={genData.expected_impact} />

                {/* ── Section 5: Safety ─────────────────────────────────── */}
                <SafetySection fixSql={genData.fix_sql} />

                {/* ── Apply progress (shown while applying / after) ─────── */}
                {(phase === 'applying' || phase === 'success') && applySteps.length > 0 && (
                  <Section title="Applying Fix">
                    <ApplyProgress steps={applySteps} />
                    {phase === 'success' && (
                      <div className="rounded-xl border border-emerald-500/40 bg-emerald-500/10 px-5 py-3 text-sm font-medium text-emerald-300">
                        ✅ Fix applied successfully — redirecting to result…
                      </div>
                    )}
                  </Section>
                )}

                {/* ── Section 6: Actions ────────────────────────────────── */}
                {(phase === 'ready' || phase === 'error') && (
                  <Section title="Actions">
                    <div className="space-y-3">
                      <button
                        onClick={handleApplyClick}
                        disabled={phase !== 'ready'}
                        className="w-full rounded-xl bg-emerald-600 py-3.5 text-sm font-bold text-white shadow-lg transition-colors hover:bg-emerald-500 focus:outline-none focus:ring-2 focus:ring-emerald-500 focus:ring-offset-2 focus:ring-offset-slate-900 disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        {genData.hypopg_validation?.validated &&
                        genData.hypopg_validation?.planner_would_use_index
                          ? '✅ Apply Validated Fix'
                          : '✅ Apply This Fix'}
                      </button>

                      <div className="flex items-center gap-3">
                        <CopyButton text={genData.fix_sql} label="Copy SQL" />
                        <span className="flex-1" />
                        <Link
                          to={backTo}
                          className="text-sm text-slate-500 hover:text-slate-300 transition-colors"
                        >
                          ← Back
                        </Link>
                        <button
                          onClick={() => navigate(backTo)}
                          className="text-sm text-slate-500 hover:text-red-400 transition-colors"
                        >
                          ❌ Dismiss
                        </button>
                      </div>
                    </div>
                  </Section>
                )}
              </>
            ) : (
              <Section title="No Automated Fix">
                <div className="space-y-2 rounded-xl border border-slate-700 bg-slate-800/60 p-5">
                  <p className="text-sm font-semibold text-slate-200">
                    SlowTrace could not generate a fix for this query.
                  </p>
                  <p className="text-sm leading-relaxed text-slate-400">
                    No known problem pattern matched. The query may already be indexed, or the
                    slowdown is not caused by a missing index, <span className="font-mono">SELECT *</span>,
                    an N+1 pattern or a missing LIMIT. Review the execution plan and stats on the
                    query detail page.
                  </p>
                  <Link
                    to={backTo}
                    className="inline-block pt-1 text-sm text-indigo-400 transition-colors hover:text-indigo-300"
                  >
                    ← Back to query detail
                  </Link>
                </div>
              </Section>
            )}
          </>
        )}
      </div>
    </>
  )
}
