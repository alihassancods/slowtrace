import { useNavigate, Link } from 'react-router-dom'
import { useDashboard } from '@/hooks/useDashboard'
import type { HealthSummary, QuerySummary, TopQuery } from '@/types/dashboard'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmt(n: number | null | undefined, decimals = 0): string {
  if (n == null) return '—'
  return n.toLocaleString(undefined, { maximumFractionDigits: decimals })
}

function ScoreBadge({ score }: { score: number }) {
  const colour =
    score >= 70
      ? 'bg-red-500/20 text-red-300 border border-red-500/40'
      : score >= 40
      ? 'bg-yellow-500/20 text-yellow-300 border border-yellow-500/40'
      : 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40'
  return (
    <span className={`inline-block rounded px-2 py-0.5 text-sm font-semibold tabular-nums ${colour}`}>
      {score.toFixed(1)}
    </span>
  )
}

function StatTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-800 p-4 flex flex-col gap-1">
      <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">{label}</span>
      <span className="text-lg font-semibold tabular-nums text-white">{value}</span>
    </div>
  )
}

function gradeColour(grade: string | null): string {
  if (grade === 'A') return 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40'
  if (grade === 'B') return 'bg-indigo-500/20 text-indigo-300 border border-indigo-500/40'
  if (grade === 'C') return 'bg-yellow-500/20 text-yellow-300 border border-yellow-500/40'
  return 'bg-red-500/20 text-red-300 border border-red-500/40'
}

// ---------------------------------------------------------------------------
// Health section
// ---------------------------------------------------------------------------

function HealthSection({ health }: { health: HealthSummary }) {
  const passing = health.checks.filter((c) => c.status === 'ok').length
  const totalDeduction = health.deductions.reduce((acc, d) => acc + d.points, 0)
  const failedChecks = health.checks.filter((c) => c.status !== 'ok')

  return (
    <div className="rounded-lg border border-slate-700 bg-slate-800 p-6">
      <h2 className="text-base font-semibold text-slate-200 mb-4">Health Score</h2>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 mb-4">
        <div className="rounded-lg border border-slate-700 bg-slate-900 p-4 flex flex-col gap-1 items-start">
          <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">Grade</span>
          <span className={`inline-block rounded px-3 py-1 text-xl font-bold ${gradeColour(health.grade)}`}>
            {health.grade ?? '—'}
          </span>
        </div>
        <StatTile label="Score" value={health.score != null ? String(health.score) : '—'} />
        <StatTile label="Checks Passing" value={`${passing} / ${health.checks.length}`} />
        <StatTile label="Deductions" value={totalDeduction > 0 ? `-${totalDeduction} pts` : '0 pts'} />
      </div>
      {failedChecks.length > 0 && (
        <div className="space-y-2">
          {failedChecks.map((c) => (
            <div
              key={c.name}
              className={`flex items-start gap-2 rounded px-3 py-2 text-sm ${
                c.status === 'warning'
                  ? 'bg-yellow-500/10 text-yellow-300'
                  : 'bg-red-500/10 text-red-300'
              }`}
            >
              <span className="mt-0.5 shrink-0">{c.status === 'warning' ? '⚠' : '✗'}</span>
              <span>
                <span className="font-semibold">{c.name}:</span> {c.message}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Query section
// ---------------------------------------------------------------------------

function QuerySection({ queries }: { queries: QuerySummary }) {
  const navigate = useNavigate()

  return (
    <div className="rounded-lg border border-slate-700 bg-slate-800 p-6">
      <h2 className="text-base font-semibold text-slate-200 mb-4">Query Overview</h2>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 mb-6">
        <StatTile label="Total Queries" value={fmt(queries.total_queries)} />
        <StatTile label="High Priority (≥70)" value={fmt(queries.high_priority_count)} />
        <StatTile label="Avg Score" value={fmt(queries.avg_score, 1)} />
        <StatTile
          label="Total Exec Time"
          value={`${fmt(queries.total_exec_time_ms / 1000, 1)}s`}
        />
      </div>

      {queries.top_queries.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-700 text-left text-xs font-semibold uppercase tracking-wider text-slate-400">
                <th className="pb-2 pr-4">Query</th>
                <th className="pb-2 pr-4 text-right whitespace-nowrap">Mean (ms)</th>
                <th className="pb-2 pr-4 text-right whitespace-nowrap">Cache %</th>
                <th className="pb-2 text-right">Score</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-700/50">
              {queries.top_queries.map((q: TopQuery) => (
                <tr
                  key={q.queryid}
                  onClick={() => navigate(`/dashboard/query/${q.queryid}`)}
                  className="cursor-pointer hover:bg-slate-700/30 transition-colors"
                >
                  <td className="py-2 pr-4">
                    <span className="font-mono text-xs text-slate-300 line-clamp-1">
                      {q.query_fingerprint.length > 60
                        ? q.query_fingerprint.slice(0, 60) + '…'
                        : q.query_fingerprint}
                    </span>
                  </td>
                  <td className="py-2 pr-4 text-right tabular-nums text-slate-300">
                    {fmt(q.mean_exec_time_ms, 1)}
                  </td>
                  <td className="py-2 pr-4 text-right tabular-nums text-slate-300">
                    {fmt(q.cache_hit_ratio, 1)}%
                  </td>
                  <td className="py-2 text-right">
                    <ScoreBadge score={q.score} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-sm text-slate-500">No queries found.</p>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function DashboardPage() {
  const dsn = localStorage.getItem('slowtrace_dsn')
  const { state, report, load } = useDashboard(dsn)

  if (!dsn) {
    return (
      <div className="p-8 max-w-4xl mx-auto">
        <h1 className="text-3xl font-bold mb-4">Dashboard</h1>
        <div className="rounded-lg border border-slate-700 bg-slate-800 p-6 text-slate-400 text-sm">
          No connection found. Please{' '}
          <Link to="/scan" className="text-indigo-400 hover:text-indigo-300 underline">
            run a scan first
          </Link>{' '}
          to view the dashboard.
        </div>
      </div>
    )
  }

  if (state === 'loading' || state === 'idle') {
    return (
      <div className="p-8 max-w-4xl mx-auto">
        <h1 className="text-3xl font-bold mb-4">Dashboard</h1>
        <div className="mt-8 flex items-center gap-3 text-slate-400 text-sm">
          <svg className="animate-spin h-5 w-5 text-indigo-400" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path
              className="opacity-75"
              fill="currentColor"
              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
            />
          </svg>
          Loading dashboard…
        </div>
      </div>
    )
  }

  if (state === 'error' || !report) {
    return (
      <div className="p-8 max-w-4xl mx-auto">
        <h1 className="text-3xl font-bold mb-4">Dashboard</h1>
        <div className="mt-6 rounded-lg border border-red-500/40 bg-red-500/10 p-4 text-sm text-red-300">
          Failed to load dashboard data.{' '}
          <button
            onClick={() => load(dsn)}
            className="underline text-red-300 hover:text-red-200 transition-colors"
          >
            Try again
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="p-8 max-w-4xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-2">
        <h1 className="text-3xl font-bold">Dashboard</h1>
        <button
          onClick={() => load(dsn)}
          className="rounded px-3 py-1.5 text-sm font-medium bg-slate-700 hover:bg-slate-600 text-slate-200 transition-colors"
        >
          Refresh
        </button>
      </div>

      {/* DSN display */}
      <p className="mb-6 font-mono text-sm text-indigo-400 truncate">{dsn}</p>

      {/* Top-level error banners */}
      {report.errors.length > 0 && (
        <div className="mb-6 space-y-2">
          {report.errors.map((err, i) => (
            <div
              key={i}
              className="rounded-lg border border-yellow-500/40 bg-yellow-500/10 px-4 py-2 text-xs text-yellow-300"
            >
              <span className="font-semibold uppercase tracking-wide">{err.step}:</span>{' '}
              {err.message}
            </div>
          ))}
        </div>
      )}

      {/* Health section */}
      <div className="mb-6">
        {report.health ? (
          <HealthSection health={report.health} />
        ) : (
          <div className="rounded-lg border border-slate-700 bg-slate-800 p-6 text-sm text-slate-500">
            Health data unavailable.
          </div>
        )}
      </div>

      {/* Query section */}
      <div>
        {report.queries ? (
          <QuerySection queries={report.queries} />
        ) : (
          <div className="rounded-lg border border-slate-700 bg-slate-800 p-6 text-sm text-slate-500">
            Query data unavailable.
          </div>
        )}
      </div>
    </div>
  )
}
