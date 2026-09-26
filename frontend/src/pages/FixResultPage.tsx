import { useState } from 'react'
import type { ReactNode } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { rollbackFix } from '@/api/fix'
import type { FixResultState } from '@/types/fix'
import { friendlyError } from '@/lib/errors'

// ---------------------------------------------------------------------------
// Small shared components
// ---------------------------------------------------------------------------

function Spinner({ size = 'md', tone = 'text-indigo-400' }: { size?: 'sm' | 'md'; tone?: string }) {
  const sz = size === 'sm' ? 'h-3.5 w-3.5' : 'h-5 w-5'
  return (
    <svg className={`${sz} animate-spin ${tone}`} viewBox="0 0 24 24" fill="none">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
    </svg>
  )
}

/** Before/after metric card, reused by the Health Score and Query Performance cards. */
function MetricCard({
  label,
  before,
  after,
  afterClass,
  beforeClass = 'text-slate-300',
  footer,
  tinted = false,
  index,
}: {
  label: string
  before: string
  after: string
  afterClass: string
  beforeClass?: string
  footer?: ReactNode
  tinted?: boolean
  index: number
}) {
  return (
    <div
      className={`rounded-xl border p-5 ${
        tinted ? 'border-emerald-500/30 bg-emerald-500/10' : 'border-slate-700 bg-slate-800/60'
      }`}
      style={{ animation: `slideFadeIn 0.3s ease-out ${0.15 + index * 0.1}s both` }}
    >
      <p className="text-xs font-semibold uppercase tracking-widest text-slate-400">{label}</p>
      <div className="mt-3 grid grid-cols-2 gap-4">
        <div className="flex flex-col gap-1">
          <span className="text-xs text-slate-500">Before</span>
          <span className={`text-3xl font-extrabold tabular-nums ${beforeClass}`}>{before}</span>
        </div>
        <div className="flex flex-col gap-1">
          <span className="text-xs text-slate-500">After</span>
          <span className={`text-3xl font-extrabold tabular-nums ${afterClass}`}>{after}</span>
        </div>
      </div>
      {footer && (
        <div className="mt-4 border-t border-slate-700/50 pt-3 text-sm">{footer}</div>
      )}
    </div>
  )
}

function PrimaryButton({
  children,
  onClick,
}: {
  children: ReactNode
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className="rounded-xl bg-indigo-600 px-6 py-3 text-sm font-bold text-white shadow-lg transition-colors hover:bg-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 focus:ring-offset-slate-900"
    >
      {children}
    </button>
  )
}

function GhostButton({
  children,
  onClick,
}: {
  children: ReactNode
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className="rounded-xl border border-slate-600 px-6 py-3 text-sm font-semibold text-slate-300 transition-colors hover:border-slate-400 hover:text-white"
    >
      {children}
    </button>
  )
}

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

function fmtScore(n: number | undefined): string {
  return n === undefined ? '—' : `${n}/100`
}

function fmtMs(n: number | undefined): string {
  return n === undefined ? '—' : `${n.toFixed(1)}ms`
}

function fmtSigned(n: number): string {
  return n >= 0 ? `+${n}` : `${n}`
}

function fmtUntil(iso: string | undefined): string | null {
  if (!iso) return null
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? null : d.toLocaleString()
}

// ---------------------------------------------------------------------------
// Success view
// ---------------------------------------------------------------------------

type RollbackState = 'idle' | 'pending' | 'done' | 'error'

function SuccessView({ state, fixId }: { state: FixResultState; fixId: string }) {
  const navigate = useNavigate()
  const [rollbackState, setRollbackState] = useState<RollbackState>('idle')
  const [rollbackError, setRollbackError] = useState<string | null>(null)

  const improvement =
    state.health_improvement
    ?? (state.health_after !== undefined && state.health_before !== undefined
      ? state.health_after - state.health_before
      : undefined)

  const speedup = state.speedup_factor
  const timeSaved = state.time_saved_per_day_minutes
  const rollbackUntil = fmtUntil(state.rollback_available_until)
  const canRollback = Boolean(state.can_rollback) && Boolean(state.connectionId) && Boolean(fixId)

  async function handleUndo() {
    if (!canRollback) return
    setRollbackState('pending')
    setRollbackError(null)
    try {
      const res = await rollbackFix(fixId, state.connectionId)
      if (res.status === 'success') {
        setRollbackState('done')
      } else {
        console.error('Rollback failed:', res.message)
        setRollbackState('error')
        setRollbackError('We could not roll back this fix. It may already be applied to your database.')
      }
    } catch (err: unknown) {
      console.error('Rollback failed:', err)
      setRollbackState('error')
      setRollbackError(friendlyError(err, 'We could not roll back this fix. Please try again.'))
    }
  }

  return (
    <div className="space-y-6">
      {/* Celebration */}
      <div style={{ animation: 'popIn 0.45s ease-out both' }}>
        <h1 className="text-3xl font-extrabold tracking-tight text-white sm:text-4xl">
          ✅ Fix Applied Successfully
        </h1>
        <p className="mt-2 text-sm text-slate-400">
          Your database is faster. Here is the measured impact.
        </p>
      </div>

      {/* Health Score */}
      <MetricCard
        index={0}
        label="Health Score"
        tinted
        before={fmtScore(state.health_before)}
        beforeClass="text-slate-400"
        after={fmtScore(state.health_after)}
        afterClass="text-emerald-400"
        footer={
          improvement !== undefined ? (
            <span className="font-bold text-emerald-400">
              {fmtSigned(improvement)} points
            </span>
          ) : null
        }
      />

      {/* Query Performance */}
      <MetricCard
        index={1}
        label="Query Performance"
        before={fmtMs(state.query_time_before_ms)}
        beforeClass="text-red-400"
        after={fmtMs(state.query_time_after_ms)}
        afterClass="text-emerald-400"
        footer={
          speedup !== undefined ? (
            <span className="font-bold text-emerald-400">
              ⚡ {speedup.toFixed(1)}× faster
            </span>
          ) : null
        }
      />

      {/* Time saved */}
      {timeSaved !== undefined && (
        <div
          className="flex items-center gap-3 rounded-xl border border-slate-700 bg-slate-800/40 px-5 py-4"
          style={{ animation: 'slideFadeIn 0.3s ease-out 0.35s both' }}
        >
          <span className="text-lg">⏱</span>
          <p className="text-sm text-slate-300">
            <span className="font-bold tabular-nums text-emerald-400">
              {timeSaved.toFixed(1)}
            </span>{' '}
            minutes saved per day
          </p>
        </div>
      )}

      {/* Rollback (subtle) */}
      {canRollback && (
        <div
          className="space-y-3 rounded-xl border border-slate-700 bg-slate-800/40 p-4"
          style={{ animation: 'slideFadeIn 0.3s ease-out 0.45s both' }}
        >
          <p className="text-sm text-slate-500">Changed your mind? You can undo this fix.</p>
          {rollbackUntil && (
            <p className="text-xs text-slate-600">Rollback available until {rollbackUntil}</p>
          )}
          <div className="flex flex-wrap items-center gap-3">
            <button
              onClick={() => void handleUndo()}
              disabled={rollbackState === 'pending' || rollbackState === 'done'}
              className="inline-flex items-center gap-1.5 rounded-lg bg-slate-700 px-3 py-1.5 text-xs font-semibold text-slate-200 transition-colors hover:bg-slate-600 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {rollbackState === 'pending' ? (
                <>
                  <Spinner size="sm" tone="text-slate-300" /> Rolling back…
                </>
              ) : rollbackState === 'done' ? (
                '✓ Rolled back'
              ) : (
                '↩ Undo Fix'
              )}
            </button>
            {rollbackState === 'done' && (
              <span className="text-xs font-medium text-emerald-400">
                ✓ Fix rolled back — your database is back to its previous state.
              </span>
            )}
            {rollbackState === 'error' && rollbackError && (
              <span className="text-xs text-red-400">{rollbackError}</span>
            )}
          </div>
        </div>
      )}

      <div style={{ animation: 'slideFadeIn 0.3s ease-out 0.55s both' }}>
        <PrimaryButton onClick={() => navigate('/dashboard')}>View Dashboard →</PrimaryButton>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Auto-rolled-back view
// ---------------------------------------------------------------------------

function AutoRolledBackView({ state }: { state: FixResultState }) {
  const navigate = useNavigate()
  return (
    <div className="space-y-6">
      <div
        className="rounded-xl border border-yellow-500/40 bg-yellow-500/10 p-6"
        style={{ animation: 'slideFadeIn 0.3s ease-out both' }}
      >
        <h1 className="text-2xl font-bold text-yellow-300 sm:text-3xl">
          ⚠️ Fix Rolled Back Automatically
        </h1>
        <p className="mt-3 text-sm leading-relaxed text-slate-300">
          Health score dropped after applying the fix. SlowTrace automatically reversed it to
          protect your database.
        </p>
        <p className="mt-3 text-sm text-yellow-200">
          <span className="font-semibold">Reason:</span>{' '}
          {state.reason ?? 'The fix degraded database health and was reversed.'}
        </p>
      </div>

      <PrimaryButton onClick={() => navigate('/dashboard')}>View Dashboard →</PrimaryButton>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Failed view
// ---------------------------------------------------------------------------

function FailedView({ state }: { state: FixResultState }) {
  const navigate = useNavigate()
  function handleRetry() {
    if (state.connectionId && state.queryid) {
      navigate(`/fix/${state.connectionId}/${state.queryid}`)
    } else {
      navigate(-1)
    }
  }
  return (
    <div className="space-y-6">
      <div
        className="rounded-xl border border-red-500/40 bg-red-500/10 p-6"
        style={{ animation: 'slideFadeIn 0.3s ease-out both' }}
      >
        <h1 className="text-2xl font-bold text-red-300 sm:text-3xl">❌ Fix Failed</h1>
        <p className="mt-3 text-sm leading-relaxed text-red-200">
          {state.error ?? 'The fix could not be applied.'}
        </p>
      </div>

      <div className="flex flex-wrap gap-3">
        <PrimaryButton onClick={handleRetry}>↻ Try Again</PrimaryButton>
        <GhostButton onClick={() => navigate('/dashboard')}>View Dashboard →</GhostButton>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Fallback — no router state (direct open / page refresh)
// ---------------------------------------------------------------------------

function UnavailableView() {
  const navigate = useNavigate()
  return (
    <div className="rounded-xl border border-slate-700 bg-slate-800/60 p-8 text-center">
      <h1 className="text-xl font-bold text-white">Result unavailable</h1>
      <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-slate-400">
        This page shows the outcome of a fix immediately after it has been applied. Apply a fix
        from the query page to see its results here.
      </p>
      <div className="mt-6">
        <PrimaryButton onClick={() => navigate('/dashboard')}>View Dashboard →</PrimaryButton>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function FixResultPage() {
  const { fixId } = useParams<{ fixId: string }>()
  const location = useLocation()
  const state = location.state as FixResultState | null

  return (
    <>
      {/* Keyframe animations injected once */}
      <style>{`
        @keyframes slideFadeIn {
          from { opacity: 0; transform: translateY(8px); }
          to   { opacity: 1; transform: translateY(0); }
        }
        @keyframes popIn {
          from { opacity: 0; transform: scale(0.92) translateY(8px); }
          60%  { opacity: 1; transform: scale(1.02) translateY(0); }
          to   { opacity: 1; transform: scale(1) translateY(0); }
        }
      `}</style>

      <div className="mx-auto max-w-3xl px-4 py-10">
        {!state || !fixId ? (
          <UnavailableView />
        ) : state.status === 'success' ? (
          <SuccessView state={state} fixId={fixId} />
        ) : state.status === 'auto_rolled_back' ? (
          <AutoRolledBackView state={state} />
        ) : (
          <FailedView state={state} />
        )}
      </div>
    </>
  )
}
