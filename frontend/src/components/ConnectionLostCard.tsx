/**
 * Shown when the database cannot be reached at all. Offers a way back to the
 * connection setup so the user can correct their connection string.
 */
export default function ConnectionLostCard({ onReconnect }: { onReconnect: () => void }) {
  return (
    <div className="rounded-xl border border-amber-500/40 bg-amber-500/10 p-5">
      <div className="flex items-start gap-3">
        <span className="text-lg leading-none">⚠️</span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-amber-200">
            ⚠️ Lost connection to database.
          </p>
          <p className="mt-1 text-sm leading-relaxed text-amber-300/90">
            Check your connection string.
          </p>
          <button
            onClick={onReconnect}
            className="mt-4 rounded-lg bg-amber-600/80 px-4 py-2 text-xs font-semibold text-white transition-colors hover:bg-amber-500 focus:outline-none focus:ring-2 focus:ring-amber-400 focus:ring-offset-2 focus:ring-offset-slate-900"
          >
            Reconnect
          </button>
        </div>
      </div>
    </div>
  )
}
