/**
 * Red error card. The caller supplies plain-English copy (see lib/errors.ts) —
 * this component is never given a raw error string or stack trace.
 */
export default function ErrorCard({
  title = 'Something went wrong',
  message,
  onRetry,
  retryLabel = 'Try Again',
}: {
  title?: string
  message: string
  onRetry?: () => void
  retryLabel?: string
}) {
  return (
    <div className="rounded-xl border border-red-500/40 bg-red-500/10 p-5">
      <div className="flex items-start gap-3">
        <span className="text-lg leading-none">⚠️</span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-red-200">{title}</p>
          <p className="mt-1 text-sm leading-relaxed text-red-300/90">{message}</p>
          {onRetry && (
            <button
              onClick={onRetry}
              className="mt-4 rounded-lg bg-red-600/80 px-4 py-2 text-xs font-semibold text-white transition-colors hover:bg-red-500 focus:outline-none focus:ring-2 focus:ring-red-400 focus:ring-offset-2 focus:ring-offset-slate-900"
            >
              {retryLabel}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
