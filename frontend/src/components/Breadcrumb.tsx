import { Link } from 'react-router-dom'

export interface BreadcrumbItem {
  label: string
  /** Omit on the current (last) crumb so it renders as plain text. */
  to?: string
}

/** Trail such as: Dashboard → Query Detail → Fix Suggestion */
export default function Breadcrumb({ items }: { items: BreadcrumbItem[] }) {
  return (
    <nav aria-label="Breadcrumb" className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
      {items.map((item, i) => (
        <span key={`${item.label}-${i}`} className="flex items-center gap-2">
          {i > 0 && <span className="select-none text-slate-600">→</span>}
          {item.to ? (
            <Link to={item.to} className="text-indigo-400 transition-colors hover:text-indigo-300">
              {item.label}
            </Link>
          ) : (
            <span className="font-medium text-slate-300">{item.label}</span>
          )}
        </span>
      ))}
    </nav>
  )
}
