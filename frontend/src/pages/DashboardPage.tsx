import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams, Link } from 'react-router-dom'
import type { SlowQuery, QueryReport } from '@/types/queries'
import ConnectionLostCard from '@/components/ConnectionLostCard'
import EmptyState from '@/components/EmptyState'
import ErrorCard from '@/components/ErrorCard'
import { Skeleton, SkeletonText } from '@/components/Skeleton'
import { friendlyError, isConnectionError } from '@/lib/errors'

// ---------------------------------------------------------------------------
// Data fetching
// ---------------------------------------------------------------------------

async function fetchQueries(connectionId: string): Promise<QueryReport> {
  const resp = await fetch(`/api/queries/${connectionId}`)
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
  return resp.json() as Promise<QueryReport>
}

// ---------------------------------------------------------------------------
// Derived stats
// ---------------------------------------------------------------------------

function computeStats(queries: SlowQuery[]) {
  if (queries.length === 0) {
    return { healthScore: 100, minutesWasted: 0, fixesAvailable: 0 }
  }
  const avgScore = queries.reduce((s, q) => s + q.score, 0) / queries.length
  // Invert score to get "health": 100 = perfect, 0 = everything is broken
  const healthScore = Math.max(0, Math.round(100 - avgScore))
  const totalMs = queries.reduce((s, q) => s + q.total_exec_time_ms, 0)
  const minutesWasted = totalMs / 60_000
  // "Fixable" = queries with score ≥ 40
  const fixesAvailable = queries.filter((q) => q.score >= 40).length
  return { healthScore, minutesWasted, fixesAvailable }
}

function severity(score: number): 'critical' | 'warning' | 'slow' {
  if (score >= 70) return 'critical'
  if (score >= 40) return 'warning'
  return 'slow'
}

// ---------------------------------------------------------------------------
// Minimal SQL token-based highlighter (no external deps)
// ---------------------------------------------------------------------------

const SQL_KEYWORDS = new Set([
  'SELECT','FROM','WHERE','JOIN','LEFT','RIGHT','INNER','OUTER','FULL','CROSS',
  'ON','AND','OR','NOT','IN','IS','NULL','AS','DISTINCT','ORDER','BY','GROUP',
  'HAVING','LIMIT','OFFSET','INSERT','INTO','VALUES','UPDATE','SET','DELETE',
  'CREATE','TABLE','INDEX','DROP','ALTER','ADD','COLUMN','PRIMARY','KEY',
  'FOREIGN','REFERENCES','CONSTRAINT','UNIQUE','DEFAULT','WITH','RECURSIVE',
  'UNION','ALL','EXCEPT','INTERSECT','CASE','WHEN','THEN','ELSE','END',
  'EXISTS','BETWEEN','LIKE','ILIKE','ANY','SOME','COALESCE','NULLIF','CAST',
  'RETURNING','EXPLAIN','ANALYZE','BUFFERS','FORMAT','JSON',
])

type Token = { type: 'keyword' | 'string' | 'number' | 'comment' | 'plain'; text: string }

function tokenise(sql: string): Token[] {
  const tokens: Token[] = []
  let i = 0
  while (i < sql.length) {
    // Single-line comment
    if (sql[i] === '-' && sql[i + 1] === '-') {
      const end = sql.indexOf('\n', i)
      const text = end === -1 ? sql.slice(i) : sql.slice(i, end + 1)
      tokens.push({ type: 'comment', text })
      i += text.length
      continue
    }
    // Block comment
    if (sql[i] === '/' && sql[i + 1] === '*') {
      const end = sql.indexOf('*/', i + 2)
      const text = end === -1 ? sql.slice(i) : sql.slice(i, end + 2)
      tokens.push({ type: 'comment', text })
      i += text.length
      continue
    }
    // String literal
    if (sql[i] === "'") {
      let j = i + 1
      while (j < sql.length && !(sql[j] === "'" && sql[j - 1] !== '\\')) j++
      const text = sql.slice(i, j + 1)
      tokens.push({ type: 'string', text })
      i += text.length
      continue
    }
    // Number
    if (/\d/.test(sql[i]) || (sql[i] === '.' && /\d/.test(sql[i + 1] ?? ''))) {
      let j = i
      while (j < sql.length && /[\d.]/.test(sql[j])) j++
      tokens.push({ type: 'number', text: sql.slice(i, j) })
      i = j
      continue
    }
    // Word (keyword or identifier)
    if (/[a-zA-Z_]/.test(sql[i])) {
      let j = i
      while (j < sql.length && /[\w]/.test(sql[j])) j++
      const word = sql.slice(i, j)
      tokens.push({
        type: SQL_KEYWORDS.has(word.toUpperCase()) ? 'keyword' : 'plain',
        text: word,
      })
      i = j
      continue
    }
    // Everything else (punctuation, whitespace)
    tokens.push({ type: 'plain', text: sql[i] })
    i++
  }
  return tokens
}

function SqlHighlight({ sql }: { sql: string }) {
  const tokens = tokenise(sql)
  return (
    <pre className="overflow-x-auto rounded-lg bg-slate-950 px-4 py-3 text-xs leading-relaxed font-mono whitespace-pre-wrap break-all">
      {tokens.map((tok, i) => {
        if (tok.type === 'keyword')
          return <span key={i} className="text-indigo-400 font-semibold">{tok.text}</span>
        if (tok.type === 'string')
          return <span key={i} className="text-amber-300">{tok.text}</span>
        if (tok.type === 'number')
          return <span key={i} className="text-emerald-400">{tok.text}</span>
        if (tok.type === 'comment')
          return <span key={i} className="text-slate-500 italic">{tok.text}</span>
        return <span key={i} className="text-slate-300">{tok.text}</span>
      })}
    </pre>
  )
}

// ---------------------------------------------------------------------------
// Stat card
// ---------------------------------------------------------------------------

function StatCard({
  label,
  value,
  sub,
  valueClass = 'text-white',
}: {
  label: string
  value: string
  sub?: string
  valueClass?: string
}) {
  return (
    <div className="flex flex-col gap-1 rounded-xl border border-slate-700 bg-slate-800/60 p-5">
      <span className="text-xs font-semibold uppercase tracking-widest text-slate-400">{label}</span>
      <span className={`text-3xl font-extrabold tabular-nums ${valueClass}`}>{value}</span>
      {sub && <span className="text-xs text-slate-500">{sub}</span>}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Severity badge
// ---------------------------------------------------------------------------

function SeverityBadge({ sev }: { sev: ReturnType<typeof severity> }) {
  if (sev === 'critical')
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-red-500/20 px-2.5 py-0.5 text-xs font-bold text-red-300 border border-red-500/30">
        🔴 CRITICAL
      </span>
    )
  if (sev === 'warning')
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-yellow-500/20 px-2.5 py-0.5 text-xs font-bold text-yellow-300 border border-yellow-500/30">
        🟡 WARNING
      </span>
    )
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-slate-600/60 px-2.5 py-0.5 text-xs font-bold text-slate-300 border border-slate-600">
      🟢 SLOW
    </span>
  )
}

// ---------------------------------------------------------------------------
// Query card
// ---------------------------------------------------------------------------

function QueryCard({
  query,
  connectionId,
  index,
}: {
  query: SlowQuery
  connectionId: string
  index: number
}) {
  const navigate = useNavigate()
  const sev = severity(query.score)
  const minutesWasted = query.total_exec_time_ms / 60_000

  // Code location display
  let filePart = ''
  let linePart = ''
  if (query.code_location) {
    const colonIdx = query.code_location.lastIndexOf(':')
    if (colonIdx !== -1) {
      filePart = query.code_location.slice(0, colonIdx)
      linePart = query.code_location.slice(colonIdx + 1)
    } else {
      filePart = query.code_location
    }
  }

  return (
    <div
      className="rounded-xl border border-slate-700 bg-slate-800/60 overflow-hidden transition-all hover:border-slate-600 cursor-pointer group"
      style={{
        animation: `slideFadeIn 0.25s ease-out ${index * 0.06}s both`,
      }}
      onClick={() => navigate(`/query/${connectionId}/${query.queryid}`)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') navigate(`/query/${connectionId}/${query.queryid}`)
      }}
    >
      {/* Card header */}
      <div className="flex items-center justify-between gap-4 px-5 pt-4 pb-3">
        <SeverityBadge sev={sev} />
        <span className="text-xs text-slate-500 tabular-nums">score {query.score.toFixed(1)}</span>
      </div>

      {/* SQL */}
      <div className="px-5 pb-4" onClick={(e) => e.stopPropagation()}>
        <SqlHighlight
          sql={
            query.query.length > 800
              ? query.query.slice(0, 800) + '\n-- … truncated'
              : query.query
          }
        />
      </div>

      {/* Metrics row */}
      <div className="flex flex-wrap items-center gap-x-6 gap-y-1 border-t border-slate-700/60 px-5 py-3 text-sm text-slate-400">
        <span>
          <span className="mr-1">⏱</span>
          <span className="tabular-nums font-medium text-slate-200">
            {query.mean_exec_time_ms.toFixed(1)}ms
          </span>{' '}
          average
        </span>
        <span>
          <span className="mr-1">📞</span>
          <span className="tabular-nums font-medium text-slate-200">
            {query.calls.toLocaleString()}
          </span>{' '}
          calls
        </span>
        <span>
          <span className="mr-1">💸</span>
          <span className="tabular-nums font-medium text-slate-200">
            {minutesWasted < 0.01
              ? `${(minutesWasted * 60).toFixed(1)}s`
              : `${minutesWasted.toFixed(2)} min`}
          </span>{' '}
          wasted
        </span>
      </div>

      {/* Code location row */}
      <div
        className="flex items-center justify-between gap-4 border-t border-slate-700/60 px-5 py-2.5"
        onClick={(e) => e.stopPropagation()}
      >
        {query.is_traced ? (
          <span className="flex min-w-0 flex-1 items-center gap-1.5 text-xs text-slate-400">
            <span className="shrink-0">📍</span>
            <span className="truncate font-mono text-indigo-300">{filePart}</span>
            {linePart && (
              <span className="shrink-0 text-slate-500">line {linePart}</span>
            )}
          </span>
        ) : (
          <Link
            to={`/wizard/${connectionId}`}
            className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-indigo-400 transition-colors"
          >
            <span>📍</span>
            <span>Not yet traced — Set up Code Linking</span>
          </Link>
        )}

        <button
          className="shrink-0 rounded-lg bg-indigo-600/80 px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-indigo-500 group-hover:bg-indigo-600"
          onClick={(e) => {
            e.stopPropagation()
            navigate(`/query/${connectionId}/${query.queryid}`)
          }}
        >
          View Details &amp; Fix →
        </button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Spinner
// ---------------------------------------------------------------------------

function Spinner() {
  return (
    <svg className="h-5 w-5 animate-spin text-indigo-400" viewBox="0 0 24 24" fill="none">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
    </svg>
  )
}

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

function DashboardSkeleton() {
  return (
    <div className="mx-auto max-w-4xl px-4 py-10" aria-busy="true" aria-label="Loading dashboard">
      {/* Page header */}
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-2">
          <Skeleton className="h-7 w-40" />
          <Skeleton className="h-3 w-64" />
        </div>
        <Skeleton className="h-8 w-24" />
      </div>

      {/* Stat cards */}
      <div className="mb-8 grid grid-cols-1 gap-4 sm:grid-cols-3">
        {[0, 1, 2].map((i) => (
          <div key={i} className="rounded-xl border border-slate-700 bg-slate-800/60 p-5">
            <Skeleton className="h-3 w-28" />
            <Skeleton className="mt-3 h-8 w-20" />
            <Skeleton className="mt-2 h-3 w-24" />
          </div>
        ))}
      </div>

      {/* Section heading */}
      <Skeleton className="mb-4 h-5 w-44" />

      {/* Query cards */}
      <div className="space-y-4">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="rounded-xl border border-slate-700 bg-slate-800/60 p-5">
            <div className="mb-4 flex items-center justify-between gap-4">
              <Skeleton className="h-5 w-28 rounded-full" />
              <Skeleton className="h-3 w-16" />
            </div>
            <Skeleton className="h-20 w-full" />
            <SkeletonText className="mt-4" lines={1} />
          </div>
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

const REFRESH_INTERVAL_MS = 30_000

export default function DashboardPage() {
  const { id: connectionId } = useParams<{ id: string }>()
  const navigate = useNavigate()

  const [status, setStatus] = useState<'loading' | 'loaded' | 'error'>('loading')
  const [report, setReport] = useState<QueryReport | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const load = useCallback(async () => {
    if (!connectionId) return
    setStatus('loading')
    try {
      const data = await fetchQueries(connectionId)
      setReport(data)
      setError(null)
      setStatus('loaded')
      setLastRefreshed(new Date())
    } catch (err: unknown) {
      setError(err)
      setStatus('error')
    }
  }, [connectionId])

  // Initial load + 30-second auto-refresh
  useEffect(() => {
    load()
    timerRef.current = setInterval(load, REFRESH_INTERVAL_MS)
    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
    }
  }, [load])

  // ── No connection id ────────────────────────────────────────────────────
  if (!connectionId) {
    return (
      <div className="p-8 max-w-4xl mx-auto">
        <h1 className="text-2xl font-bold mb-4">Dashboard</h1>
        <p className="text-slate-400 text-sm">
          No connection selected.{' '}
          <Link to="/" className="text-indigo-400 hover:underline">
            Connect a database
          </Link>{' '}
          to view the dashboard.
        </p>
      </div>
    )
  }

  // ── Loading (initial) ───────────────────────────────────────────────────
  if (status === 'loading' && !report) {
    return <DashboardSkeleton />
  }

  // ── Error ───────────────────────────────────────────────────────────────
  if (status === 'error' && !report) {
    return (
      <div className="mx-auto max-w-4xl px-4 py-10">
        {isConnectionError(error) ? (
          <ConnectionLostCard onReconnect={() => navigate('/')} />
        ) : (
          <ErrorCard
            title="Could not load your dashboard"
            message={friendlyError(error, 'We could not load your slow queries. Please try again.')}
            onRetry={() => void load()}
          />
        )}
      </div>
    )
  }

  // ── Backend reported it could not reach the database ─────────────────────
  if (report && report.errors.some((e) => /connect/i.test(e.step))) {
    return (
      <div className="mx-auto max-w-4xl px-4 py-10">
        <ConnectionLostCard onReconnect={() => navigate('/')} />
      </div>
    )
  }

  const queries = report?.queries ?? []
  const { healthScore, minutesWasted, fixesAvailable } = computeStats(queries)

  const healthScoreClass =
    healthScore > 75
      ? 'text-emerald-400'
      : healthScore >= 50
        ? 'text-yellow-400'
        : 'text-red-400'

  const timeWastedDisplay =
    minutesWasted < 1
      ? `${(minutesWasted * 60).toFixed(0)}s`
      : minutesWasted < 60
        ? `${minutesWasted.toFixed(1)} min`
        : `${(minutesWasted / 60).toFixed(1)} hr`

  const timeWastedEmotion =
    minutesWasted > 60
      ? '🔥 Critical'
      : minutesWasted > 10
        ? '😬 High'
        : minutesWasted > 1
          ? '⚠️ Moderate'
          : '✅ Low'

  const tracedCount = queries.filter((q) => q.is_traced).length
  const showTracePrompt = queries.length > 0 && tracedCount < queries.length / 2

  return (
    <>
      <style>{`
        @keyframes slideFadeIn {
          from { opacity: 0; transform: translateY(8px); }
          to   { opacity: 1; transform: translateY(0); }
        }
      `}</style>

      <div className="mx-auto max-w-4xl px-4 py-10">
        {/* Page header */}
        <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold text-white">Dashboard</h1>
            {lastRefreshed && (
              <p className="mt-0.5 text-xs text-slate-500">
                Last refreshed {lastRefreshed.toLocaleTimeString()} · auto-refreshes every 30s
              </p>
            )}
          </div>
          <div className="flex items-center gap-3">
            {showTracePrompt && (
              <Link
                to={`/wizard/${connectionId}`}
                className="flex items-center gap-1.5 rounded-lg border border-indigo-500/40 bg-indigo-500/10 px-3 py-1.5 text-xs font-semibold text-indigo-300 transition-colors hover:bg-indigo-500/20"
              >
                🔗 Set Up Code Tracing
              </Link>
            )}
            <button
              onClick={load}
              disabled={status === 'loading'}
              className="flex items-center gap-1.5 rounded-lg bg-slate-700 px-3 py-1.5 text-xs font-semibold text-slate-200 transition-colors hover:bg-slate-600 disabled:opacity-50"
            >
              {status === 'loading' ? <Spinner /> : '↺'}
              Refresh
            </button>
          </div>
        </div>

        {/* Backend errors banner */}
        {report && report.errors.length > 0 && (
          <div className="mb-6 rounded-lg border border-yellow-500/40 bg-yellow-500/10 px-4 py-2.5 text-xs text-yellow-300">
            Some checks could not complete. SlowTrace may not have permission to read every
            database statistic — the results below may be incomplete.
          </div>
        )}

        {/* Stat cards */}
        <div className="mb-8 grid grid-cols-1 gap-4 sm:grid-cols-3">
          <StatCard
            label="Health Score"
            value={String(healthScore)}
            sub={
              healthScore > 75 ? 'Looking good' : healthScore >= 50 ? 'Needs attention' : 'Critical issues'
            }
            valueClass={healthScoreClass}
          />
          <StatCard
            label="Time Wasted Today"
            value={timeWastedDisplay}
            sub={timeWastedEmotion}
            valueClass={minutesWasted > 60 ? 'text-red-400' : minutesWasted > 10 ? 'text-yellow-400' : 'text-emerald-400'}
          />
          <StatCard
            label="Fixes Available"
            value={String(fixesAvailable)}
            sub={fixesAvailable > 0 ? `of ${queries.length} queries` : 'All queries look fine'}
            valueClass={fixesAvailable > 0 ? 'text-indigo-400' : 'text-emerald-400'}
          />
        </div>

        {/* Slow queries section */}
        <div>
          <div className="mb-4 flex items-baseline gap-2">
            <h2 className="text-lg font-bold text-white">🐌 Slow Queries</h2>
            <span className="text-sm text-slate-500">Ranked by impact</span>
          </div>

          {queries.length === 0 ? (
            <EmptyState
              icon="🎉"
              title="🎉 No slow queries detected! Your database is performing well."
            />
          ) : (
            <div className="space-y-4">
              {queries.map((q, i) => (
                <QueryCard
                  key={q.queryid}
                  query={q}
                  connectionId={connectionId}
                  index={i}
                />
              ))}
            </div>
          )}
        </div>
      </div>
    </>
  )
}
