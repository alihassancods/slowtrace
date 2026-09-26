import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import type { SlowQuery } from '@/types/queries'

type SortKey = keyof Pick<SlowQuery, 'calls' | 'mean_exec_time_ms' | 'total_exec_time_ms' | 'cache_hit_ratio' | 'score'>

function ScoreBadge({ score }: { score: number }) {
  const colour =
    score >= 70
      ? 'bg-red-500/20 text-red-300 border border-red-500/40'
      : score >= 40
      ? 'bg-yellow-500/20 text-yellow-300 border border-yellow-500/40'
      : 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40'
  return (
    <span className={`inline-block rounded px-2 py-0.5 text-xs font-semibold tabular-nums ${colour}`}>
      {score.toFixed(1)}
    </span>
  )
}

function fmt(n: number, decimals = 0): string {
  return n.toLocaleString(undefined, { maximumFractionDigits: decimals })
}

interface QueryTableProps {
  queries: SlowQuery[]
}

export default function QueryTable({ queries }: QueryTableProps) {
  const navigate = useNavigate()
  const [sortKey, setSortKey] = useState<SortKey>('score')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')

  function handleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(key)
      setSortDir('desc')
    }
  }

  const sorted = [...queries].sort((a, b) => {
    const diff = a[sortKey] - b[sortKey]
    return sortDir === 'asc' ? diff : -diff
  })

  function SortArrow({ col }: { col: SortKey }) {
    if (col !== sortKey) return <span className="ml-1 text-slate-600">↕</span>
    return <span className="ml-1 text-indigo-400">{sortDir === 'asc' ? '↑' : '↓'}</span>
  }

  const thClass = 'px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-400 cursor-pointer select-none hover:text-white'
  const tdClass = 'px-4 py-3 text-sm'

  if (queries.length === 0) {
    return (
      <div className="rounded-lg border border-slate-700 bg-slate-800 p-8 text-center text-slate-400">
        No slow queries found.
      </div>
    )
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-slate-700">
      <table className="w-full min-w-[720px] border-collapse text-white">
        <thead className="border-b border-slate-700 bg-slate-800/60">
          <tr>
            <th className={`${thClass} w-[40%]`}>Query</th>
            <th className={thClass} onClick={() => handleSort('calls')}>
              Calls<SortArrow col="calls" />
            </th>
            <th className={thClass} onClick={() => handleSort('mean_exec_time_ms')}>
              Mean (ms)<SortArrow col="mean_exec_time_ms" />
            </th>
            <th className={thClass} onClick={() => handleSort('total_exec_time_ms')}>
              Total (ms)<SortArrow col="total_exec_time_ms" />
            </th>
            <th className={thClass} onClick={() => handleSort('cache_hit_ratio')}>
              Cache %<SortArrow col="cache_hit_ratio" />
            </th>
            <th className={thClass} onClick={() => handleSort('score')}>
              Score<SortArrow col="score" />
            </th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((q) => (
            <tr
              key={q.queryid}
              className="border-b border-slate-700/50 bg-slate-900 transition-colors hover:bg-slate-800 cursor-pointer"
              onClick={() => navigate(`/dashboard/query/${q.queryid}`)}
            >
              <td className={`${tdClass} font-mono text-xs text-slate-300 max-w-[320px]`}>
                <span
                  className="block truncate"
                  title={q.query_fingerprint}
                >
                  {q.query_fingerprint.slice(0, 80)}
                  {q.query_fingerprint.length > 80 ? '…' : ''}
                </span>
              </td>
              <td className={`${tdClass} tabular-nums text-slate-300`}>{fmt(q.calls)}</td>
              <td className={`${tdClass} tabular-nums text-slate-300`}>{fmt(q.mean_exec_time_ms, 1)}</td>
              <td className={`${tdClass} tabular-nums text-slate-300`}>{fmt(q.total_exec_time_ms, 0)}</td>
              <td className={`${tdClass} tabular-nums text-slate-300`}>{fmt(q.cache_hit_ratio, 1)}%</td>
              <td className={tdClass}>
                <ScoreBadge score={q.score} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
