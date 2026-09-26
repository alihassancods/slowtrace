import { useParams, Link } from 'react-router-dom'
import { useQueryDetail } from '@/hooks/useQueryDetail'
import PlanTree from '@/components/PlanTree'

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

export default function QueryDetailPage() {
  const { id: queryid } = useParams<{ id: string }>()
  const dsn = localStorage.getItem('slowtrace_dsn')

  const { state, detail } = useQueryDetail(dsn ?? null, queryid ?? null)

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
          to load a query detail.
        </div>
      </div>
    )
  }

  if (state === 'loading' || state === 'idle') {
    return (
      <div className="p-8 max-w-3xl mx-auto">
        <Link to="/scan" className="text-sm text-indigo-400 hover:text-indigo-300 transition-colors">
          ← Back to Scan
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
          Loading query detail…
        </div>
      </div>
    )
  }

  if (state === 'error' || !detail) {
    return (
      <div className="p-8 max-w-3xl mx-auto">
        <Link to="/scan" className="text-sm text-indigo-400 hover:text-indigo-300 transition-colors">
          ← Back to Scan
        </Link>
        <div className="mt-6 rounded-lg border border-red-500/40 bg-red-500/10 p-4 text-sm text-red-300">
          Failed to load query detail.
        </div>
      </div>
    )
  }

  // detail.errors may carry explain/fetch_query errors even when partial data is available
  const hasErrors = detail.errors.length > 0

  return (
    <div className="p-8 max-w-3xl mx-auto">
      {/* Back link + Fix Wizard button */}
      <div className="flex items-center justify-between">
        <Link to="/scan" className="text-sm text-indigo-400 hover:text-indigo-300 transition-colors">
          ← Back to Scan
        </Link>
        <Link
          to={`/fix/${queryid}`}
          className="rounded px-3 py-1.5 text-sm font-medium bg-indigo-600 hover:bg-indigo-500 text-white transition-colors"
        >
          Fix Wizard →
        </Link>
      </div>

      {/* Query fingerprint */}
      <div className="mt-6 mb-6">
        <h1 className="text-xl font-bold mb-2">Query Detail</h1>
        {detail.query_fingerprint ? (
          <pre className="mt-2 rounded-lg border border-slate-700 bg-slate-800 p-4 text-xs font-mono text-slate-300 whitespace-pre-wrap break-all leading-relaxed">
            {detail.query_fingerprint}
          </pre>
        ) : (
          <p className="text-slate-500 text-sm mt-2">Query text unavailable.</p>
        )}
      </div>

      {/* Error banner if any step failed */}
      {hasErrors && (
        <div className="mb-6 space-y-2">
          {detail.errors.map((err, i) => (
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

      {/* Stats grid */}
      <div className="mb-8 grid grid-cols-2 gap-3 sm:grid-cols-3">
        <StatTile label="Calls" value={fmt(detail.calls)} />
        <StatTile label="Mean (ms)" value={fmt(detail.mean_exec_time_ms, 1)} />
        <StatTile label="Total (ms)" value={fmt(detail.total_exec_time_ms, 0)} />
        <StatTile label="Cache Hit %" value={detail.cache_hit_ratio != null ? `${fmt(detail.cache_hit_ratio, 1)}%` : '—'} />
        <StatTile label="Std Dev (ms)" value={fmt(detail.stddev_exec_time_ms, 1)} />
        <div className="rounded-lg border border-slate-700 bg-slate-800 p-4 flex flex-col gap-1">
          <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">Score</span>
          {detail.score != null ? (
            <ScoreBadge score={detail.score} />
          ) : (
            <span className="text-sm text-slate-500">—</span>
          )}
        </div>
      </div>

      {/* EXPLAIN plan */}
      <div>
        <h2 className="text-base font-semibold mb-3 text-slate-200">EXPLAIN Plan</h2>
        <PlanTree plan={detail.plan} />
      </div>
    </div>
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
