import { useParams, Link } from 'react-router-dom'
import { useFixWizard } from '@/hooks/useFixWizard'
import FixCard from '@/components/FixCard'

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

export default function FixPage() {
  const { id: queryid } = useParams<{ id: string }>()
  const dsn = localStorage.getItem('slowtrace_dsn')

  const { state, report } = useFixWizard(dsn ?? null, queryid ?? null)

  const backLink = queryid ? `/dashboard/query/${queryid}` : '/scan'

  if (!dsn) {
    return (
      <div className="p-8 max-w-3xl mx-auto">
        <Link to="/scan" className="text-sm text-indigo-400 hover:text-indigo-300 transition-colors">
          ← Back to Scan
        </Link>
        <div className="mt-6 rounded-lg border border-slate-700 bg-slate-800 p-6 text-slate-400 text-sm">
          No connection found. Please{' '}
          <Link to="/scan" className="text-indigo-400 hover:text-indigo-300 underline">
            run a scan first
          </Link>{' '}
          to analyse a query.
        </div>
      </div>
    )
  }

  if (state === 'loading' || state === 'idle') {
    return (
      <div className="p-8 max-w-3xl mx-auto">
        <Link to={backLink} className="text-sm text-indigo-400 hover:text-indigo-300 transition-colors">
          ← Back to Query Detail
        </Link>
        <div className="mt-8 flex items-center gap-3 text-slate-400 text-sm">
          <svg className="animate-spin h-5 w-5 text-indigo-400" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path
              className="opacity-75"
              fill="currentColor"
              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
            />
          </svg>
          Analysing query…
        </div>
      </div>
    )
  }

  if (state === 'error' || !report) {
    return (
      <div className="p-8 max-w-3xl mx-auto">
        <Link to={backLink} className="text-sm text-indigo-400 hover:text-indigo-300 transition-colors">
          ← Back to Query Detail
        </Link>
        <div className="mt-6 rounded-lg border border-red-500/40 bg-red-500/10 p-4 text-sm text-red-300">
          Failed to load fix recommendations.
        </div>
      </div>
    )
  }

  const hasErrors = report.errors.length > 0

  return (
    <div className="p-8 max-w-3xl mx-auto">
      {/* Back link */}
      <Link to={backLink} className="text-sm text-indigo-400 hover:text-indigo-300 transition-colors">
        ← Back to Query Detail
      </Link>

      {/* Page heading */}
      <div className="mt-6 mb-6">
        <h1 className="text-xl font-bold mb-2">Fix Wizard</h1>
        {report.query_fingerprint ? (
          <pre className="mt-2 rounded-lg border border-slate-700 bg-slate-800 p-4 text-xs font-mono text-slate-300 whitespace-pre-wrap break-all leading-relaxed">
            {report.query_fingerprint}
          </pre>
        ) : (
          <p className="text-slate-500 text-sm mt-2">Query fingerprint unavailable.</p>
        )}
      </div>

      {/* Error banners */}
      {hasErrors && (
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

      {/* Stats bar */}
      <div className="mb-8 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div className="rounded-lg border border-slate-700 bg-slate-800 p-4 flex flex-col gap-1">
          <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">Score</span>
          {report.score != null ? (
            <ScoreBadge score={report.score} />
          ) : (
            <span className="text-sm text-slate-500">—</span>
          )}
        </div>
        <StatTile label="Mean (ms)" value={fmt(report.mean_exec_time_ms, 1)} />
        <StatTile label="Total (ms)" value={fmt(report.total_exec_time_ms)} />
        <StatTile
          label="Cache Hit %"
          value={report.cache_hit_ratio != null ? `${fmt(report.cache_hit_ratio, 1)}%` : '—'}
        />
      </div>

      {/* Recommendations */}
      <div>
        <div className="flex items-center gap-2 mb-4">
          <h2 className="text-base font-semibold text-slate-200">Recommendations</h2>
          <span className="inline-block rounded-full bg-indigo-500/20 text-indigo-300 border border-indigo-500/40 px-2 py-0.5 text-xs font-semibold tabular-nums">
            {report.recommendations.length}
          </span>
        </div>

        {report.recommendations.length === 0 ? (
          <div className="rounded-lg border border-slate-700 bg-slate-800 p-6 text-slate-400 text-sm text-center">
            No issues detected for this query.
          </div>
        ) : (
          <div className="space-y-4">
            {report.recommendations.map((rec) => (
              <FixCard key={rec.id} rec={rec} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
