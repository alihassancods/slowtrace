import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams, Link } from 'react-router-dom'
import ConnectionLostCard from '@/components/ConnectionLostCard'
import EmptyState from '@/components/EmptyState'
import ErrorCard from '@/components/ErrorCard'
import { Skeleton } from '@/components/Skeleton'
import { useActiveConnection } from '@/lib/activeConnection'
import { getScanSnapshot, saveScanSnapshot } from '@/lib/scanSnapshot'
import type {
  ScanCompleteData as CompleteData,
  ScanConnectedData as ConnectedData,
  ScanHealthCheck as HealthCheckData,
  ScanSlowQuerySummary as SlowQueriesData,
  ScanSnapshot,
  ScanSseEvent as SseEvent,
} from '@/types/scan'

// ---------------------------------------------------------------------------
// Total event count used to drive the progress bar.
// 1 connected + 8 health_checks + 1 slow_queries + 1 complete = 11
// ---------------------------------------------------------------------------
const TOTAL_EVENTS = 11

// Friendly display names for health check keys
const CHECK_LABELS: Record<string, string> = {
  connections: 'Open connections',
  cache_hit_ratio: 'Cache hit ratio',
  replication_lag: 'Replication lag',
  table_bloat: 'Table bloat',
  lock_contention: 'Lock contention',
  long_transactions: 'Long-running transactions',
  index_usage: 'Index usage',
  dead_tuples: 'Dead tuples (autovacuum)',
}

function checkLabel(key: string): string {
  return CHECK_LABELS[key] ?? key.replace(/_/g, ' ')
}

function formatRows(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function ProgressBar({ pct }: { pct: number }) {
  return (
    <div className="relative h-2 w-full overflow-hidden rounded-full bg-slate-700">
      <div
        className="absolute inset-y-0 left-0 rounded-full bg-indigo-500 transition-all duration-500 ease-out"
        style={{ width: `${pct}%` }}
      />
    </div>
  )
}

function ConnectedCard({ data }: { data: ConnectedData }) {
  if (data.error) {
    return (
      <div className="rounded-xl border border-red-500/40 bg-red-500/10 p-4 text-sm text-red-300">
        Connection error: {data.error}
      </div>
    )
  }
  // Extract short version string e.g. "PostgreSQL 15.3 on …" → "15.3"
  const versionShort = data.version
    ? (data.version.match(/PostgreSQL\s+([\d.]+)/i)?.[1] ?? data.version.split(' ')[1] ?? data.version)
    : '—'

  return (
    <div className="rounded-xl border border-slate-700 bg-slate-800/60 p-5">
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-widest text-slate-400">
        Database
      </h2>
      <ul className="space-y-2 text-sm text-slate-200">
        <li className="flex items-center gap-2">
          <span>✅</span>
          <span>PostgreSQL {versionShort}</span>
        </li>
        <li className="flex items-center gap-2">
          <span>📦</span>
          <span>{data.size ?? '—'} database</span>
        </li>
        <li className="flex items-center gap-2">
          <span>📋</span>
          <span>{data.table_count ?? 0} tables discovered</span>
        </li>
        <li className="flex items-center gap-2">
          <span>👥</span>
          <span>{formatRows(data.total_rows ?? 0)} total rows</span>
        </li>
      </ul>
    </div>
  )
}

function HealthRow({ check, index }: { check: HealthCheckData; index: number }) {
  const icon = check.status === 'ok' ? '✅' : check.status === 'warning' ? '⚠️' : '🔴'
  const valueColor =
    check.status === 'ok'
      ? 'text-emerald-400'
      : check.status === 'warning'
        ? 'text-yellow-400'
        : 'text-red-400'

  const value = check.message ?? check.status

  return (
    <li
      className="flex items-start gap-3 text-sm opacity-0"
      style={{
        animation: `slideFadeIn 0.3s ease-out ${index * 0.15}s forwards`,
      }}
    >
      <span className="mt-0.5 shrink-0 text-base leading-none">{icon}</span>
      <span className="min-w-0 flex-1 text-slate-300">{checkLabel(check.check)}</span>
      <span className={`shrink-0 text-right font-medium ${valueColor}`}>{value}</span>
    </li>
  )
}

function SlowQueriesCard({ data }: { data: SlowQueriesData }) {
  const count = data.count
  const minutes = data.total_wasted_minutes

  const wastedText =
    minutes >= 60
      ? `${(minutes / 60).toFixed(1)}h wasted today`
      : minutes >= 1
        ? `${minutes.toFixed(1)} min wasted today`
        : `${(minutes * 60).toFixed(0)}s wasted today`

  return (
    <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-5">
      <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1">
        <span className="text-base font-semibold text-amber-300">
          🐌 {count} slow {count === 1 ? 'query' : 'queries'} found
        </span>
        <span className="text-sm font-medium text-amber-400">💸 {wastedText}</span>
      </div>
      <p className="text-sm leading-relaxed text-slate-300">{data.human_description}</p>
    </div>
  )
}

function CompleteCard({ data }: { data: CompleteData }) {
  const scoreColor =
    data.health_score >= 80
      ? 'text-emerald-400'
      : data.health_score >= 50
        ? 'text-yellow-400'
        : 'text-red-400'

  return (
    <div className="rounded-xl border border-indigo-500/40 bg-indigo-500/10 p-5">
      <div className="mb-4 flex flex-wrap items-center gap-4">
        <div>
          <span className="text-xs font-semibold uppercase tracking-widest text-slate-400">
            Health score
          </span>
          <p className={`text-3xl font-extrabold ${scoreColor}`}>{data.health_score}</p>
        </div>
        <div className="flex gap-4 text-sm">
          <span className="text-emerald-400">✅ {data.healthy_count} healthy</span>
          {data.warning_count > 0 && (
            <span className="text-yellow-400">⚠️ {data.warning_count} warnings</span>
          )}
          {data.critical_count > 0 && (
            <span className="text-red-400">🔴 {data.critical_count} critical</span>
          )}
        </div>
      </div>

      {data.quick_wins.length > 0 && (
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-widest text-slate-400">
            Quick wins
          </p>
          <ul className="space-y-1.5">
            {data.quick_wins.map((w) => (
              <li key={w.check} className="text-sm text-slate-300">
                <span className="font-medium text-indigo-300">{checkLabel(w.check)}: </span>
                {w.fix}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

function ScanSkeleton() {
  return (
    <div className="space-y-5" aria-busy="true" aria-label="Scanning database">
      {/* Database card */}
      <div className="rounded-xl border border-slate-700 bg-slate-800/60 p-5">
        <Skeleton className="h-3 w-24" />
        <div className="mt-4 space-y-3">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="flex items-center gap-3">
              <Skeleton className="h-4 w-4 shrink-0 rounded-full" />
              <Skeleton className="h-3 w-full max-w-xs" />
            </div>
          ))}
        </div>
      </div>

      {/* Health checks card */}
      <div className="rounded-xl border border-slate-700 bg-slate-800/60 p-5">
        <Skeleton className="h-3 w-28" />
        <div className="mt-4 space-y-3">
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <div key={i} className="flex items-center justify-between gap-4">
              <Skeleton className="h-3 w-48" />
              <Skeleton className="h-3 w-20" />
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

type Phase = 'idle' | 'ready' | 'connecting' | 'scanning' | 'done' | 'error'

export default function ScanPage() {
  const { id: routeConnectionId } = useParams<{ id?: string }>()
  const activeConnection = useActiveConnection()
  // Fall back to the persisted connection so bare /scan still works after a
  // nav click, reload or bookmark.
  const connectionId = routeConnectionId ?? activeConnection?.id ?? ''
  const navigate = useNavigate()

  // Nickname from the persisted connection, falling back to router state.
  const [historyNickname] = useState<string>(() => {
    try {
      return (history.state as { usr?: { nickname?: string } })?.usr?.nickname ?? ''
    } catch {
      return ''
    }
  })
  const nickname = activeConnection?.nickname || historyNickname

  // A scan runs only when the user asks for one (runId > 0). Revisits render
  // the persisted snapshot instead of re-fetching.
  const [runId, setRunId] = useState(0)
  const [snapshot, setSnapshot] = useState<ScanSnapshot | null>(() =>
    getScanSnapshot(connectionId),
  )
  const [phase, setPhase] = useState<Phase>(() => {
    if (!connectionId) return 'idle'
    return getScanSnapshot(connectionId) ? 'done' : 'ready'
  })
  const [progress, setProgress] = useState(() =>
    getScanSnapshot(connectionId) ? 100 : 0,
  )
  const [errorKind, setErrorKind] = useState<'connection' | 'error' | null>(null)
  const [justFinished, setJustFinished] = useState(false)

  // Received data — seeded from the persisted snapshot, if any.
  const [connectedData, setConnectedData] = useState<ConnectedData | null>(
    () => getScanSnapshot(connectionId)?.connectedData ?? null,
  )
  const [healthChecks, setHealthChecks] = useState<HealthCheckData[]>(
    () => getScanSnapshot(connectionId)?.healthChecks ?? [],
  )
  const [slowQueriesData, setSlowQueriesData] = useState<SlowQueriesData | null>(
    () => getScanSnapshot(connectionId)?.slowQuerySummary ?? null,
  )
  const [completeData, setCompleteData] = useState<CompleteData | null>(
    () => getScanSnapshot(connectionId)?.completeData ?? null,
  )

  const eventsReceived = useRef(0)
  const esRef = useRef<EventSource | null>(null)
  // Mirrors streamed data so the snapshot written on completion is complete.
  const collectedRef = useRef<{
    connectedData: ConnectedData | null
    healthChecks: HealthCheckData[]
    slowQuerySummary: SlowQueriesData | null
  }>({ connectedData: null, healthChecks: [], slowQuerySummary: null })

  const startScan = () => setRunId((n) => n + 1)

  useEffect(() => {
    // No connection, or the user has not asked for a scan — show cached data.
    if (!connectionId || runId === 0) return

    setPhase('connecting')
    setProgress(0)
    setErrorKind(null)
    setJustFinished(false)
    setConnectedData(null)
    setHealthChecks([])
    setSlowQueriesData(null)
    setCompleteData(null)
    collectedRef.current = { connectedData: null, healthChecks: [], slowQuerySummary: null }
    eventsReceived.current = 0

    let finished = false
    const es = new EventSource(`/api/scan/${connectionId}`)
    esRef.current = es

    function bump() {
      eventsReceived.current += 1
      // Cap at TOTAL_EVENTS - 1 so complete event can push to 100
      const pct = Math.min(
        Math.round((eventsReceived.current / TOTAL_EVENTS) * 100),
        99,
      )
      setProgress(pct)
    }

    es.onmessage = (e: MessageEvent) => {
      let parsed: SseEvent
      try {
        parsed = JSON.parse(e.data) as SseEvent
      } catch {
        return
      }

      setPhase('scanning')
      bump()

      const { stage, data } = parsed

      if (stage === 'connected') {
        const connected = data as ConnectedData
        collectedRef.current.connectedData = connected
        setConnectedData(connected)
        if (connected.error) {
          finished = true
          setErrorKind('connection')
          setPhase('error')
          es.close()
        }
      } else if (stage === 'health_check') {
        const check = data as HealthCheckData
        collectedRef.current.healthChecks = [...collectedRef.current.healthChecks, check]
        setHealthChecks(collectedRef.current.healthChecks)
      } else if (stage === 'slow_queries') {
        const raw = data as {
          queries?: unknown[]
          total_wasted_minutes?: number
          human_description?: string
        }
        const summary: SlowQueriesData = {
          count: raw.queries?.length ?? 0,
          total_wasted_minutes: raw.total_wasted_minutes ?? 0,
          human_description: raw.human_description ?? '',
        }
        collectedRef.current.slowQuerySummary = summary
        setSlowQueriesData(summary)
      } else if (stage === 'complete') {
        const complete = data as unknown as CompleteData
        setCompleteData(complete)
        setProgress(100)
        setPhase('done')
        setJustFinished(true)
        finished = true
        es.close()

        // Persist the finished scan so revisiting it needs no API call.
        const saved: ScanSnapshot = {
          connectionId,
          connectedData: collectedRef.current.connectedData,
          healthChecks: collectedRef.current.healthChecks,
          slowQuerySummary: collectedRef.current.slowQuerySummary,
          completeData: complete,
          updatedAt: Date.now(),
        }
        saveScanSnapshot(saved)
        setSnapshot(saved)

        // Navigate to dashboard after 1 second
        setTimeout(() => {
          navigate(`/dashboard/${connectionId}`)
        }, 1000)
      }
    }

    es.onerror = () => {
      if (!finished) {
        setErrorKind('error')
        setPhase('error')
      }
      es.close()
    }

    return () => {
      es.close()
      esRef.current = null
    }
  }, [connectionId, runId, navigate])

  // ── No connectionId: nothing connected yet ──────────────────────────────
  if (!connectionId) {
    return (
      <div className="p-8 max-w-2xl mx-auto">
        <h1 className="text-2xl font-bold mb-2">Database Scan</h1>
        <p className="text-slate-400">
          Connect from the{' '}
          <Link to="/" className="text-indigo-400 hover:underline">
            home page
          </Link>{' '}
          to start a scan.
        </p>
      </div>
    )
  }

  const isScanning = phase === 'connecting' || phase === 'scanning'

  const headingText = isScanning
    ? phase === 'connecting'
      ? 'Connecting…'
      : `Scanning${nickname ? ` ${nickname}` : ''}…`
    : phase === 'done'
      ? 'Scan complete!'
      : phase === 'error'
        ? 'Scan stopped'
        : 'Database Scan'

  const hasResults =
    Boolean(connectedData) || healthChecks.length > 0 || Boolean(completeData)
  const showSkeleton = phase === 'connecting' || (phase === 'scanning' && !hasResults)

  return (
    <>
      {/* Keyframe animation injected once */}
      <style>{`
        @keyframes slideFadeIn {
          from { opacity: 0; transform: translateX(-12px); }
          to   { opacity: 1; transform: translateX(0); }
        }
      `}</style>

      <div className="mx-auto max-w-2xl px-4 py-10">
        {/* Back to dashboard */}
        <Link
          to={`/dashboard/${connectionId}`}
          className="mb-4 inline-flex items-center gap-1 text-sm text-indigo-400 transition-colors hover:text-indigo-300"
        >
          ← Back to Dashboard
        </Link>

        {/* Header */}
        <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold text-white">
              {headingText}
            </h1>
            {phase === 'scanning' && (
              <p className="mt-1 text-sm text-slate-400">
                Analysing your database health in real time…
              </p>
            )}
            {phase === 'ready' && (
              <p className="mt-1 text-sm text-slate-400">
                Nothing is fetched until you start a scan.
              </p>
            )}
            {phase === 'done' && snapshot && (
              <p className="mt-1 text-xs text-slate-500">
                Last scanned {new Date(snapshot.updatedAt).toLocaleString()}
              </p>
            )}
          </div>
          {phase === 'done' && (
            <button
              onClick={startScan}
              className="shrink-0 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-indigo-500"
            >
              ↺ Rescan
            </button>
          )}
        </div>

        {/* Progress bar */}
        {(isScanning || justFinished) && (
          <div className="mb-8">
            <ProgressBar pct={progress} />
            <p className="mt-1.5 text-right text-xs text-slate-500">{progress}%</p>
          </div>
        )}

        {/* Skeleton — shown until the first results arrive */}
        {showSkeleton && (
          <div className="mb-5">
            <ScanSkeleton />
          </div>
        )}

        {/* Error states */}
        {phase === 'error' && (
          <div className="mb-5">
            {errorKind === 'connection' ? (
              <ConnectionLostCard onReconnect={() => navigate('/')} />
            ) : (
              <ErrorCard
                title="Could not finish the scan"
                message="The scan stopped before it finished. Check that your database is reachable, then try again."
                onRetry={startScan}
              />
            )}
          </div>
        )}

        {/* No scan yet — the only way a scan starts is this button */}
        {phase === 'ready' && (
          <EmptyState
            icon="🔍"
            title="Ready to scan"
            description="SlowTrace will check your database health and find slow queries. Nothing is fetched until you start."
            action={
              <button
                onClick={startScan}
                className="rounded-lg bg-indigo-600 px-5 py-2.5 text-sm font-bold text-white shadow transition-colors hover:bg-indigo-500"
              >
                🔍 Scan Database
              </button>
            }
          />
        )}

        {/* Results */}
        <div className="space-y-5">
          {/* Connected card */}
          {connectedData && <ConnectedCard data={connectedData} />}

          {/* Health checks */}
          {healthChecks.length > 0 && (
            <div className="rounded-xl border border-slate-700 bg-slate-800/60 p-5">
              <h2 className="mb-4 text-sm font-semibold uppercase tracking-widest text-slate-400">
                Health checks
              </h2>
              <ul className="space-y-3">
                {healthChecks.map((check, i) => (
                  <HealthRow key={`${check.check}-${i}`} check={check} index={i} />
                ))}
              </ul>
            </div>
          )}

          {/* Slow queries */}
          {slowQueriesData &&
            (slowQueriesData.count === 0 ? (
              <EmptyState
                icon="🎉"
                title="🎉 No slow queries detected! Your database is performing well."
              />
            ) : (
              <SlowQueriesCard data={slowQueriesData} />
            ))}

          {/* Complete */}
          {completeData && <CompleteCard data={completeData} />}

          {/* Done banner */}
          {justFinished && (
            <div className="rounded-xl border border-emerald-500/40 bg-emerald-500/10 px-5 py-3 text-sm font-medium text-emerald-300">
              ✅ Scan complete — redirecting to dashboard…
            </div>
          )}
        </div>
      </div>
    </>
  )
}
