import type { FixRecommendation } from '@/types/fix'

const SEVERITY_BADGE: Record<FixRecommendation['severity'], string> = {
  high: 'bg-red-500/20 text-red-300 border border-red-500/40',
  medium: 'bg-yellow-500/20 text-yellow-300 border border-yellow-500/40',
  low: 'bg-slate-500/20 text-slate-300 border border-slate-500/40',
}

export default function FixCard({ rec }: { rec: FixRecommendation }) {
  function handleCopy() {
    navigator.clipboard.writeText(rec.sql)
  }

  return (
    <div className="rounded-lg border border-slate-700 bg-slate-800 p-5 flex flex-col gap-3">
      {/* Title row */}
      <div className="flex items-start gap-3">
        <span
          className={`inline-block shrink-0 rounded px-2 py-0.5 text-xs font-semibold uppercase tracking-wide ${SEVERITY_BADGE[rec.severity]}`}
        >
          {rec.severity}
        </span>
        <span className="text-sm font-semibold text-white leading-snug">{rec.title}</span>
      </div>

      {/* Explanation */}
      <p className="text-sm text-slate-300 leading-relaxed">{rec.explanation}</p>

      {/* SQL snippet */}
      <div className="relative rounded-md border border-slate-700 bg-[#0f172a]">
        <button
          onClick={handleCopy}
          className="absolute top-2 right-2 rounded px-2 py-0.5 text-xs text-slate-400 bg-slate-700 hover:bg-slate-600 hover:text-slate-200 transition-colors"
        >
          Copy
        </button>
        <pre className="p-4 pr-16 text-xs font-mono text-slate-300 whitespace-pre-wrap break-all leading-relaxed overflow-x-auto">
          {rec.sql}
        </pre>
      </div>
    </div>
  )
}
