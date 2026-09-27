import { useEffect, useMemo, useRef, useState } from 'react'
import type {
  CodebaseFinding,
  CodebaseScanResult,
  CodebaseSeverity,
  CodebaseSmellGroup,
} from '@/types/codebase'
import { filesLabel, repoLabel, scanDurationLabel } from '@/lib/codebaseDisplay'
import { buildCodeLines, findingGitHubUrl, type CodeLine } from '@/lib/codebaseResults'

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface ScanResultsProps {
  /** The `complete` stage payload. */
  result: CodebaseScanResult
  /** "owner/repo" — used for the header and GitHub fallback links. */
  repoDisplay: string
  /** Wall-clock seconds the scan took, or null if unknown. */
  durationSeconds?: number | null
  /** Re-run the scan against the same repository. */
  onRescan: () => void
  /** Clear everything and return to the connect form. */
  onScanAnother: () => void
}

// ---------------------------------------------------------------------------
// Severity presentation
// ---------------------------------------------------------------------------

interface SeverityMeta {
  emoji: string
  label: string
  dot: string
  cardBorder: string
  badge: string
  statTile: string
}

const SEVERITY: Record<CodebaseSeverity, SeverityMeta> = {
  critical: {
    emoji: '🔴',
    label: 'Critical',
    dot: 'bg-red-500',
    cardBorder: 'border-l-red-500',
    badge: 'border-red-500/40 bg-red-500/10 text-red-300',
    statTile: 'text-red-400',
  },
  warning: {
    emoji: '🟡',
    label: 'Warnings',
    dot: 'bg-amber-400',
    cardBorder: 'border-l-amber-500',
    badge: 'border-amber-500/40 bg-amber-500/10 text-amber-300',
    statTile: 'text-amber-400',
  },
  info: {
    emoji: '🔵',
    label: 'Info',
    dot: 'bg-sky-400',
    cardBorder: 'border-l-sky-400',
    badge: 'border-sky-500/40 bg-sky-500/10 text-sky-300',
    statTile: 'text-sky-400',
  },
}

const SEVERITY_RANK: Record<CodebaseSeverity, number> = {
  critical: 0,
  warning: 1,
  info: 2,
}

function asSeverity(value: string | undefined): CodebaseSeverity {
  return value === 'critical' || value === 'info' ? value : 'warning'
}

/**
 * Short, opinionated impact lines per smell type. The backend now ships an
 * `impact` string per rule, so this is only used for smell ids the backend has
 * no copy for (public registry rules report "general").
 */
const IMPACT_COPY: Record<string, string> = {
  n_plus_one:
    'These will destroy your database at scale. Works fine with 100 users, catastrophic at 10,000.',
  select_star:
    "You're pulling entire rows when you need a couple of columns — wasted memory, and no index-only scans.",
  missing_pagination:
    "No LIMIT means one call can return your whole table. Fine today, an outage tomorrow as the data grows.",
  lazy_loading:
    'Every relationship access fires another query — an N+1 hiding behind an innocent attribute.',
  string_sql:
    'Building SQL from strings is an injection risk and defeats the plan cache.',
  inefficient_exists:
    'Counting rows to decide whether any exist reads the whole result set for one yes/no answer.',
}

// ---------------------------------------------------------------------------
// Derived display data
// ---------------------------------------------------------------------------

function groupImpact(group: CodebaseSmellGroup): string {
  const first = group.findings[0]
  return (
    IMPACT_COPY[group.smell_id] ||
    group.impact ||
    first?.message ||
    'Detected by Semgrep during the repository scan.'
  )
}

// ---------------------------------------------------------------------------
// Small sub-components
// ---------------------------------------------------------------------------

function StatTile({ value, label, tone }: { value: number; label: string; tone: string }) {
  return (
    <div className="rounded-xl border border-slate-700 bg-slate-900/60 px-5 py-4 text-center">
      <p className={`text-3xl font-extrabold leading-none ${tone}`}>{value}</p>
      <p className="mt-2 text-xs font-semibold uppercase tracking-widest text-slate-500">
        {label}
      </p>
    </div>
  )
}

function SeverityBadge({ severity }: { severity: CodebaseSeverity }) {
  const meta = SEVERITY[severity]
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-semibold ${meta.badge}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${meta.dot}`} />
      {meta.label.replace(/s$/, '')}
    </span>
  )
}

/** A numbered, dark code block with the matching lines marked with ▶. */
function CodeBlock({ lines }: { lines: CodeLine[] }) {
  return (
    <div className="mt-2 overflow-x-auto rounded-lg border border-slate-700 bg-slate-950/80 py-2 font-mono text-xs leading-relaxed">
      {lines.map((line) => (
        <div
          key={line.no}
          className={`flex items-start gap-2 px-2 ${line.hl ? 'bg-white/[0.07]' : ''}`}
        >
          <span className="w-3 shrink-0 select-none text-center text-indigo-400">
            {line.hl ? '▶' : ''}
          </span>
          <span
            className={`w-9 shrink-0 select-none text-right tabular-nums ${
              line.hl ? 'font-semibold text-slate-300' : 'text-slate-600'
            }`}
          >
            {line.no}
          </span>
          <span
            className={`whitespace-pre pr-4 ${line.hl ? 'text-slate-100' : 'text-slate-400'}`}
          >
            {line.text || ' '}
          </span>
        </div>
      ))}
    </div>
  )
}

function CopyButton({ text, label = 'Copy Fix' }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false)
  const timer = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(timer.current), [])

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(text)
    } catch {
      // Clipboard API can be blocked (non-secure context) — the button just no-ops.
      return
    }
    setCopied(true)
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setCopied(false), 1600)
  }

  return (
    <button
      type="button"
      onClick={handleCopy}
      className={`mt-3 inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-semibold transition-colors ${
        copied
          ? 'border-emerald-500/50 bg-emerald-500/10 text-emerald-300'
          : 'border-slate-600 text-slate-300 hover:border-slate-400 hover:text-white'
      }`}
    >
      {copied ? '✓ Copied!' : `📋 ${label}`}
    </button>
  )
}

/** "file.py — line 23" plus an "Open in GitHub →" link when resolvable. */
function FindingLink({
  finding,
  repoDisplay,
  inline = true,
}: {
  finding: CodebaseFinding
  repoDisplay: string
  inline?: boolean
}) {
  const url = findingGitHubUrl(finding, repoDisplay)
  const label = (
    <>
      <span className="break-all font-mono">{finding.file}</span>
      <span className="text-slate-500">
        {' '}
        — line {finding.line_start}
        {finding.line_end > finding.line_start ? `-${finding.line_end}` : ''}
      </span>
    </>
  )
  return (
    <div className={inline ? 'flex flex-wrap items-center gap-x-3 gap-y-1' : ''}>
      {url ? (
        <a
          href={url}
          target="_blank"
          rel="noreferrer"
          className="text-sm text-indigo-400 underline-offset-2 hover:underline"
        >
          {label}
        </a>
      ) : (
        <span className="text-sm text-slate-300">{label}</span>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Finding block (one occurrence)
// ---------------------------------------------------------------------------

function FindingBlock({
  finding,
  repoDisplay,
}: {
  finding: CodebaseFinding
  repoDisplay: string
}) {
  const lines = useMemo(() => buildCodeLines(finding), [finding])
  const url = findingGitHubUrl(finding, repoDisplay)
  return (
    <div>
      <FindingLink finding={finding} repoDisplay={repoDisplay} />
      {lines.length > 0 && <CodeBlock lines={lines} />}
      <p className="mt-2 text-sm leading-relaxed text-slate-400">{finding.message}</p>

      {/* The fix belongs to the rule that fired, not the smell group: several
          rules share a smell_id (e.g. prefetch_related and select_related are
          both n_plus_one), so a group-level fix would be wrong for some rows. */}
      {finding.fix_code && (
        <div className="mt-3 rounded-lg border border-slate-700 bg-slate-900/50 p-4">
          <p className="text-xs font-semibold uppercase tracking-widest text-indigo-300">
            The fix
          </p>
          <pre className="mt-2 overflow-x-auto whitespace-pre rounded-md bg-slate-950/80 p-3 font-mono text-xs leading-relaxed text-slate-200">
            {finding.fix_code}
          </pre>
          <CopyButton text={finding.fix_code} />
        </div>
      )}

      {url && (
        <a
          href={url}
          target="_blank"
          rel="noreferrer"
          className="mt-3 inline-block text-sm font-semibold text-indigo-400 hover:underline"
        >
          Open in GitHub →
        </a>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Issue card (Section 3)
// ---------------------------------------------------------------------------

function IssueCard({
  group,
  repoDisplay,
  expanded,
  highlighted,
  onToggle,
}: {
  group: CodebaseSmellGroup
  repoDisplay: string
  expanded: boolean
  highlighted: boolean
  onToggle: () => void
}) {
  const severity = asSeverity(group.severity)
  const meta = SEVERITY[severity]
  const shown = expanded ? group.findings : group.findings.slice(0, 1)
  const extra = group.findings.length - 1

  return (
    <article
      id={`issue-${group.smell_id}`}
      className={`scroll-mt-6 rounded-xl border border-slate-700 border-l-4 bg-slate-800/60 p-5 transition-shadow duration-500 ${meta.cardBorder} ${
        highlighted ? 'st-flash ring-2 ring-indigo-400/70' : ''
      }`}
    >
      <div className="flex items-center gap-2.5">
        <span aria-hidden>{meta.emoji}</span>
        <h3 className="text-sm font-bold text-white">{group.name}</h3>
        <span className="text-sm text-slate-400">
          · {group.count} {group.count === 1 ? 'location' : 'locations'}
        </span>
      </div>
      <hr className="my-3 border-slate-700" />
      <p className="text-sm leading-relaxed text-slate-300">{groupImpact(group)}</p>

      <div className="mt-4 space-y-6">
        {shown.map((f) => (
          <FindingBlock
            key={`${f.file}:${f.line_start}:${f.rule_id}`}
            finding={f}
            repoDisplay={repoDisplay}
          />
        ))}
      </div>

      {extra > 0 && (
        <button
          type="button"
          onClick={onToggle}
          className="mt-3 inline-flex items-center gap-1 rounded-md border border-slate-600 px-3 py-1.5 text-xs font-semibold text-slate-300 transition-colors hover:border-slate-400 hover:text-white"
        >
          {expanded ? 'Show fewer' : `+ ${extra} more location${extra === 1 ? '' : 's'}`}
        </button>
      )}
    </article>
  )
}

// ---------------------------------------------------------------------------
// Priority card (Section 2)
// ---------------------------------------------------------------------------

function PriorityCard({
  group,
  repoDisplay,
  onFixNow,
}: {
  group: CodebaseSmellGroup
  repoDisplay: string
  onFixNow: (smellId: string) => void
}) {
  const severity = asSeverity(group.severity)
  const meta = SEVERITY[severity]
  const first = group.findings[0]
  const lines = useMemo(() => (first ? buildCodeLines(first) : []), [first])
  if (!first) return null

  return (
    <div
      className={`rounded-xl border border-slate-700 border-l-4 bg-slate-800/60 p-4 ${meta.cardBorder}`}
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <SeverityBadge severity={severity} />
        <span className="text-sm font-semibold text-slate-100">{group.name}</span>
        <span className="text-xs text-slate-500">
          {group.count} {group.count === 1 ? 'location' : 'locations'}
        </span>
      </div>
      <p className="mt-2 text-sm leading-relaxed text-slate-400">
        {groupImpact(group)}
      </p>
      <div className="mt-3">
        <FindingLink finding={first} repoDisplay={repoDisplay} />
      </div>
      {lines.length > 0 && <CodeBlock lines={lines} />}
      <div className="mt-3 flex items-center gap-3">
        <button
          type="button"
          onClick={() => onFixNow(group.smell_id)}
          className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-xs font-bold text-white shadow transition-colors hover:bg-indigo-500"
        >
          ⚡ Fix Now
        </button>
        <a
          href={first.github_url || findingGitHubUrl(first, repoDisplay) || '#'}
          target="_blank"
          rel="noreferrer"
          className="text-xs font-semibold text-indigo-400 hover:underline"
        >
          Open in GitHub →
        </a>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function ScanResults({
  result,
  repoDisplay,
  durationSeconds,
  onRescan,
  onScanAnother,
}: ScanResultsProps) {
  // Groups ordered: critical → warning → info, then most locations first.
  const groups = useMemo(() => {
    return Object.values(result.grouped_by_type ?? {})
      .filter((g) => g.findings && g.findings.length > 0)
      .sort((a, b) => {
        const rank = SEVERITY_RANK[asSeverity(a.severity)] - SEVERITY_RANK[asSeverity(b.severity)]
        if (rank !== 0) return rank
        if (b.count !== a.count) return b.count - a.count
        return a.name.localeCompare(b.name)
      })
  }, [result.grouped_by_type])

  const priority = useMemo(() => {
    const given = result.priority_fixes ?? []
    return given.length > 0 ? given : groups.slice(0, 3)
  }, [result.priority_fixes, groups])

  const counts = useMemo(() => {
    const files = new Set<string>()
    for (const f of result.findings ?? []) files.add(f.file)
    return {
      critical: result.critical_count ?? 0,
      warning: result.warning_count ?? 0,
      info: result.info_count ?? 0,
      total: result.total_findings ?? (result.findings?.length ?? 0),
      files: files.size,
    }
  }, [result])

  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  const [highlightedId, setHighlightedId] = useState<string | null>(null)
  const flashTimer = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(flashTimer.current), [])

  function toggleGroup(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function handleFixNow(id: string) {
    setExpanded((prev) => (prev.has(id) ? prev : new Set(prev).add(id)))
    setHighlightedId(id)
    window.clearTimeout(flashTimer.current)
    flashTimer.current = window.setTimeout(() => setHighlightedId(null), 2000)
    requestAnimationFrame(() => {
      document.getElementById(`issue-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
  }

  const repo = repoDisplay || repoLabel(result.repo_url)
  const fileCount = result.files_scanned ?? 0
  const metaLine = [filesLabel(fileCount), scanDurationLabel(durationSeconds)]
    .filter(Boolean)
    .join(' · ')

  const header = (
    <SummaryHeader
      repo={repo}
      metaLine={metaLine}
      counts={counts}
      sizeWarning={result.size_warning ?? undefined}
      onRescan={onRescan}
      onScanAnother={onScanAnother}
    />
  )

  // Zero issues — a celebration, not a warning. Nothing was misconfigured; the
  // scan ran to completion and the code is clean. Unless there was nothing to
  // scan at all, which is a different message.
  if (counts.total === 0) {
    return (
      <div className="space-y-5">
        {header}
        <div className="rounded-xl border border-emerald-500/40 bg-emerald-500/10 p-8 text-center">
          <p className="text-2xl font-bold text-emerald-200">✅ Clean codebase!</p>
          {fileCount === 0 ? (
            <p className="mx-auto mt-3 max-w-md text-sm leading-relaxed text-emerald-100">
              No source files were scanned, so this is not a clean bill of health —
              the repository may be empty or entirely excluded by the ignore rules.
            </p>
          ) : (
            <>
              <p className="mt-3 text-sm leading-relaxed text-emerald-100">
                Semgrep found no ORM anti-patterns in {fileCount}{' '}
                {fileCount === 1 ? 'file' : 'files'}.
              </p>
              <p className="mx-auto mt-1.5 max-w-md text-sm leading-relaxed text-emerald-300/80">
                Your code follows database best practices.
              </p>
            </>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-8">
      <style>{`
        @keyframes stFlash {
          0%   { box-shadow: 0 0 0 0 rgba(129,140,248,0.0); }
          20%  { box-shadow: 0 0 0 4px rgba(129,140,248,0.45); }
          100% { box-shadow: 0 0 0 0 rgba(129,140,248,0.0); }
        }
        .st-flash { animation: stFlash 1.8s ease-out 1; }
      `}</style>

      {/* Section 1 — Summary header */}
      {header}

      {/* Section 2 — Priority fixes */}
      {priority.length > 0 && (
        <section>
          <h2 className="mb-1 text-sm font-semibold uppercase tracking-widest text-slate-400">
            Fix these first
          </h2>
          <p className="mb-4 text-sm text-slate-500">
            Highest impact — start here to get the biggest wins.
          </p>
          <div className="space-y-4">
            {priority.map((group) => (
              <PriorityCard
                key={group.smell_id}
                group={group}
                repoDisplay={repo}
                onFixNow={handleFixNow}
              />
            ))}
          </div>
        </section>
      )}

      {/* Section 3 — Issues by type */}
      <section>
        <h2 className="mb-4 text-sm font-semibold uppercase tracking-widest text-slate-400">
          Issues by type
        </h2>
        <div className="space-y-5">
          {groups.map((group) => (
            <IssueCard
              key={group.smell_id}
              group={group}
              repoDisplay={repo}
              expanded={expanded.has(group.smell_id)}
              highlighted={highlightedId === group.smell_id}
              onToggle={() => toggleGroup(group.smell_id)}
            />
          ))}
        </div>
      </section>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Summary header (Section 1)
// ---------------------------------------------------------------------------

interface Counts {
  critical: number
  warning: number
  info: number
  total: number
  files: number
}

function SummaryHeader({
  repo,
  metaLine,
  counts,
  sizeWarning,
  onRescan,
  onScanAnother,
}: {
  repo: string
  metaLine: string
  counts: Counts
  sizeWarning?: string
  onRescan: () => void
  onScanAnother: () => void
}) {
  return (
    <section className="rounded-2xl border border-slate-700 bg-slate-800/60 p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h2 className="text-xl font-bold text-white">
            🔍 Codebase Analysis{repo ? ` — ${repo}` : ''}
          </h2>
          {metaLine && <p className="mt-1 text-sm text-slate-400">{metaLine}</p>}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            onClick={onRescan}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white shadow transition-colors hover:bg-indigo-500"
          >
            ↺ Re-scan
          </button>
          <button
            type="button"
            onClick={onScanAnother}
            className="rounded-lg border border-slate-600 px-4 py-2 text-sm font-medium text-slate-300 transition-colors hover:border-slate-400 hover:text-white"
          >
            Scan another
          </button>
        </div>
      </div>

      {sizeWarning && (
        <div className="mt-4 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-2.5 text-sm text-amber-300">
          ⚠️ {sizeWarning}
        </div>
      )}

      <div className="mt-5 grid grid-cols-3 gap-3">
        <StatTile value={counts.critical} label="Critical" tone={SEVERITY.critical.statTile} />
        <StatTile value={counts.warning} label="Warnings" tone={SEVERITY.warning.statTile} />
        <StatTile value={counts.info} label="Info" tone={SEVERITY.info.statTile} />
      </div>

      <p className="mt-4 text-sm text-slate-400">
        <span className="font-semibold text-slate-200">{counts.total}</span>{' '}
        {counts.total === 1 ? 'issue' : 'issues'} across{' '}
        <span className="font-semibold text-slate-200">{counts.files}</span>{' '}
        {counts.files === 1 ? 'file' : 'files'}
      </p>
    </section>
  )
}
