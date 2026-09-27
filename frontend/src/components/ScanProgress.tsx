import type { ScanStep } from '@/types/queries'

const STEP_LABELS: Record<string, string> = {
  connect: 'Connect',
  check_extension: 'Check Extension',
  check_permissions: 'Check Permissions',
  fetch_queries: 'Fetch Queries',
  result: 'Complete',
}

const ORDERED_STEPS = ['connect', 'check_extension', 'check_permissions', 'fetch_queries', 'result']

interface ScanProgressProps {
  steps: ScanStep[]
  scanning: boolean
}

function StatusIcon({ status, active }: { status: ScanStep['status'] | null; active: boolean }) {
  if (active) {
    return (
      <span className="flex h-5 w-5 items-center justify-center">
        <span className="h-4 w-4 animate-spin rounded-full border-2 border-indigo-400 border-t-transparent" />
      </span>
    )
  }
  if (status === 'ok' || status === 'warning') {
    return (
      <span className="flex h-5 w-5 items-center justify-center rounded-full bg-emerald-500 text-xs font-bold text-white">
        ✓
      </span>
    )
  }
  if (status === 'fail') {
    return (
      <span className="flex h-5 w-5 items-center justify-center rounded-full bg-red-500 text-xs font-bold text-white">
        ✗
      </span>
    )
  }
  return (
    <span className="flex h-5 w-5 items-center justify-center rounded-full border border-slate-600" />
  )
}

export default function ScanProgress({ steps, scanning }: ScanProgressProps) {
  const done = new Map(steps.map((s) => [s.step, s]))
  const lastDone = steps[steps.length - 1]?.step ?? null
  const lastDoneIdx = lastDone ? ORDERED_STEPS.indexOf(lastDone) : -1

  return (
    <div className="rounded-lg border border-slate-700 bg-slate-800 p-6">
      <h2 className="mb-4 text-sm font-semibold uppercase tracking-wider text-slate-400">
        Scan Progress
      </h2>
      <ol className="space-y-3">
        {ORDERED_STEPS.map((stepKey, idx) => {
          const event = done.get(stepKey)
          const isActive = scanning && idx === lastDoneIdx + 1
          const status = event?.status ?? null

          return (
            <li key={stepKey} className="flex items-start gap-3">
              <StatusIcon status={status} active={isActive} />
              <div className="flex-1 min-w-0">
                <span
                  className={
                    status != null || isActive
                      ? 'text-sm text-white'
                      : 'text-sm text-slate-500'
                  }
                >
                  {STEP_LABELS[stepKey] ?? stepKey}
                </span>
                {event && event.status === 'warning' && typeof event.data.message === 'string' && (
                  <p className="mt-0.5 text-xs text-yellow-400">
                    {event.data.message}
                  </p>
                )}
                {event && event.status === 'fail' && typeof event.data.message === 'string' && (
                  <p className="mt-0.5 text-xs text-red-400">
                    {event.data.message}
                  </p>
                )}
                {stepKey === 'fetch_queries' && event?.status === 'ok' && typeof event.data.count === 'number' && (
                  <p className="mt-0.5 text-xs text-slate-400">
                    {event.data.count} queries found
                  </p>
                )}
              </div>
            </li>
          )
        })}
      </ol>
    </div>
  )
}
