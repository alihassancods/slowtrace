import type { ReactNode } from 'react'

/**
 * Animated grey placeholder box. Compose these to mirror a page's real layout
 * so the page never flashes a blank screen while data loads.
 */
export function Skeleton({ className = '' }: { className?: string }) {
  return <div className={`animate-pulse rounded bg-slate-700/60 ${className}`} />
}

/** A stack of text-like placeholder lines (last line shortened). */
export function SkeletonText({
  lines = 3,
  className = '',
}: {
  lines?: number
  className?: string
}) {
  return (
    <div className={`space-y-2 ${className}`}>
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton key={i} className={`h-3 ${i === lines - 1 ? 'w-2/3' : 'w-full'}`} />
      ))}
    </div>
  )
}

/** A bordered card shell holding placeholder content. */
export function SkeletonCard({
  className = '',
  children,
}: {
  className?: string
  children?: ReactNode
}) {
  return (
    <div className={`rounded-xl border border-slate-700 bg-slate-800/60 p-5 ${className}`}>
      {children}
    </div>
  )
}
