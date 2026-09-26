import { useState } from 'react'
import { useQueryScan } from '@/hooks/useQueryScan'
import ScanProgress from '@/components/ScanProgress'
import QueryTable from '@/components/QueryTable'

export default function ScanPage() {
  const [dsn, setDsn] = useState('')
  const { state, steps, queries, start, reset } = useQueryScan()

  function handleScan(e: React.FormEvent) {
    e.preventDefault()
    if (dsn.trim()) {
      localStorage.setItem('slowtrace_dsn', dsn.trim())
      start(dsn.trim())
    }
  }

  const scanning = state === 'scanning'

  return (
    <div className="p-8 max-w-5xl mx-auto">
      <h1 className="text-3xl font-bold mb-2">Slow Query Analysis</h1>
      <p className="text-slate-400 mb-8">
        Connect to a PostgreSQL database and scan for slow queries via{' '}
        <code className="text-indigo-400">pg_stat_statements</code>.
      </p>

      {/* DSN input — always visible unless scanning */}
      {state !== 'scanning' && (
        <form onSubmit={handleScan} className="mb-8 flex gap-3">
          <input
            type="text"
            value={dsn}
            onChange={(e) => setDsn(e.target.value)}
            placeholder="postgresql://user:pass@host:5432/dbname"
            disabled={scanning}
            className="flex-1 rounded-lg border border-slate-600 bg-slate-800 px-4 py-2.5 text-sm text-white placeholder-slate-500 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 disabled:opacity-50"
          />
          <button
            type="submit"
            disabled={scanning || !dsn.trim()}
            className="rounded-lg bg-indigo-600 px-5 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Scan
          </button>
          {(state === 'done' || state === 'error') && (
            <button
              type="button"
              onClick={reset}
              className="rounded-lg border border-slate-600 px-5 py-2.5 text-sm font-semibold text-slate-300 transition-colors hover:border-slate-400 hover:text-white"
            >
              Reset
            </button>
          )}
        </form>
      )}

      {/* Scanning state: show progress + cancel */}
      {state === 'scanning' && (
        <div className="mb-8">
          <div className="mb-4 flex items-center justify-between">
            <p className="text-sm text-slate-400">
              Scanning <span className="font-mono text-indigo-400">{dsn}</span>…
            </p>
            <button
              type="button"
              onClick={reset}
              className="text-xs text-slate-500 hover:text-slate-300 transition-colors"
            >
              Cancel
            </button>
          </div>
          <ScanProgress steps={steps} scanning={scanning} />
        </div>
      )}

      {/* Progress shown after scan too (collapsed) */}
      {(state === 'done' || state === 'error') && steps.length > 0 && (
        <div className="mb-8">
          <ScanProgress steps={steps} scanning={false} />
        </div>
      )}

      {/* Results table */}
      {state === 'done' && (
        <div>
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-lg font-semibold">
              Results —{' '}
              <span className="text-indigo-400">{queries.length}</span> slow{' '}
              {queries.length === 1 ? 'query' : 'queries'}
            </h2>
            <span className="text-xs text-slate-500">Sorted by score · click a row for details</span>
          </div>
          <QueryTable queries={queries} />
        </div>
      )}

      {/* Error state */}
      {state === 'error' && (
        <div className="rounded-lg border border-red-500/40 bg-red-500/10 p-4 text-sm text-red-300">
          Scan failed. Check the connection string and try again.
        </div>
      )}
    </div>
  )
}
