import type { ReactNode } from 'react'

/** Neutral "nothing here" panel with an optional call to action. */
export default function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon?: string
  title: string
  description?: string
  action?: ReactNode
}) {
  return (
    <div className="rounded-xl border border-slate-700 bg-slate-800/60 p-8 text-center">
      {icon && <p className="mb-3 text-3xl leading-none">{icon}</p>}
      <p className="text-sm font-semibold text-slate-200">{title}</p>
      {description && (
        <p className="mx-auto mt-1.5 max-w-md text-sm leading-relaxed text-slate-500">
          {description}
        </p>
      )}
      {action && <div className="mt-5 flex justify-center">{action}</div>}
    </div>
  )
}
