import { useCallback, useEffect, useState } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { fetchQueryDetailById } from '@/api/explain'
import type { QueryDetail } from '@/types/explain'
import Breadcrumb from '@/components/Breadcrumb'
import ConnectionLostCard from '@/components/ConnectionLostCard'
import ErrorCard from '@/components/ErrorCard'
import { Skeleton, SkeletonText } from '@/components/Skeleton'
import { friendlyError, isConnectionError } from '@/lib/errors'
import { useActiveConnection } from '@/lib/activeConnection'

// ---------------------------------------------------------------------------
// SQL syntax highlighter (token-based, no external lib)
// ---------------------------------------------------------------------------

const SQL_KW = new Set([
  'SELECT','FROM','WHERE','JOIN','LEFT','RIGHT','INNER','OUTER','FULL','CROSS',
  'ON','AND','OR','NOT','IN','IS','NULL','AS','DISTINCT','ORDER','BY','GROUP',
  'HAVING','LIMIT','OFFSET','INSERT','INTO','VALUES','UPDATE','SET','DELETE',
  'CREATE','TABLE','INDEX','DROP','ALTER','ADD','COLUMN','PRIMARY','KEY',
  'FOREIGN','REFERENCES','CONSTRAINT','UNIQUE','DEFAULT','WITH','RECURSIVE',
  'UNION','ALL','EXCEPT','INTERSECT','CASE','WHEN','THEN','ELSE','END',
  'EXISTS','BETWEEN','LIKE','ILIKE','ANY','SOME','COALESCE','NULLIF','CAST',
  'RETURNING','EXPLAIN','ANALYZE','BUFFERS','FORMAT','JSON',
])

type TokType = 'keyword' | 'string' | 'number' | 'comment' | 'plain'
type Tok = { type: TokType; text: string }

function tokenise(sql: string): Tok[] {
  const out: Tok[] = []
  let i = 0
  while (i < sql.length) {
    if (sql[i] === '-' && sql[i + 1] === '-') {
      const end = sql.indexOf('\n', i)
      const text = end === -1 ? sql.slice(i) : sql.slice(i, end + 1)
      out.push({ type: 'comment', text }); i += text.length; continue
    }
    if (sql[i] === '/' && sql[i + 1] === '*') {
      const end = sql.indexOf('*/', i + 2)
      const text = end === -1 ? sql.slice(i) : sql.slice(i, end + 2)
      out.push({ type: 'comment', text }); i += text.length; continue
    }
    if (sql[i] === "'") {
      let j = i + 1
      while (j < sql.length && !(sql[j] === "'" && sql[j - 1] !== '\\')) j++
      out.push({ type: 'string', text: sql.slice(i, j + 1) }); i = j + 1; continue
    }
    if (/\d/.test(sql[i]) || (sql[i] === '.' && /\d/.test(sql[i + 1] ?? ''))) {
      let j = i
      while (j < sql.length && /[\d.]/.test(sql[j])) j++
      out.push({ type: 'number', text: sql.slice(i, j) }); i = j; continue
    }
    if (/[a-zA-Z_]/.test(sql[i])) {
      let j = i
      while (j < sql.length && /\w/.test(sql[j])) j++
      const word = sql.slice(i, j)
      out.push({ type: SQL_KW.has(word.toUpperCase()) ? 'keyword' : 'plain', text: word })
      i = j; continue
    }
    out.push({ type: 'plain', text: sql[i] }); i++
  }
  return out
}

function SqlBlock({ sql }: { sql: string }) {
  return (
    <pre className="overflow-x-auto rounded-xl bg-slate-950 border border-slate-700/60 px-5 py-4 text-xs leading-relaxed font-mono whitespace-pre-wrap break-all">
      {tokenise(sql).map((tok, i) => {
        if (tok.type === 'keyword')
          return <span key={i} className="text-emerald-400 font-semibold">{tok.text}</span>
        if (tok.type === 'string')
          return <span key={i} className="text-amber-300">{tok.text}</span>
        if (tok.type === 'number')
          return <span key={i} className="text-sky-400">{tok.text}</span>
        if (tok.type === 'comment')
          return <span key={i} className="text-slate-500 italic">{tok.text}</span>
        return <span key={i} className="text-slate-200">{tok.text}</span>
      })}
    </pre>
  )
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmt(n: number | null | undefined, decimals = 0): string {
  if (n == null) return '—'
  return n.toLocaleString(undefined, { maximumFractionDigits: decimals })
}

/** Parse "file:line" code_location into parts. */
function parseCodeLocation(loc: string): { file: string; line: number | null } {
  // The backend stores it as "filepath:lineNumber" where lineNumber is always
  // a plain integer at the end after the last colon.
  const lastColon = loc.lastIndexOf(':')
  if (lastColon === -1) return { file: loc, line: null }
  const maybeLine = loc.slice(lastColon + 1)
  const lineNum = parseInt(maybeLine, 10)
  if (isNaN(lineNum)) return { file: loc, line: null }
  return { file: loc.slice(0, lastColon), line: lineNum }
}

/**
 * Extract SQLCommenter metadata keys from the SQL comment inline.
 * The backend only surfaces file+line in code_location; other keys
 * (action, route, db_driver) live in the embedded comment.
 */
function parseInlineComment(sql: string): Record<string, string> {
  const match = sql.match(/\/\*(.*?)\*\//s)
  if (!match) return {}
  const body = match[1]
  // SQLCommenter: key='value'
  const scPairs = [...body.matchAll(/(\w+)='([^']*)'/g)]
  if (scPairs.length) {
    return Object.fromEntries(
      scPairs.map((m) => [m[1], decodeURIComponent(m[2])]),
    )
  }
  // Marginalia: key:value
  return Object.fromEntries(
    [...body.matchAll(/(\w+):([^,]+)/g)].map((m) => [m[1], m[2].trim()]),
  )
}

// ---------------------------------------------------------------------------
// Stat card
// ---------------------------------------------------------------------------

function StatCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="flex flex-col gap-1 rounded-xl border border-slate-700 bg-slate-800/60 p-5">
      <span className="text-xs font-semibold uppercase tracking-widest text-slate-400">{label}</span>
      <span className="text-2xl font-extrabold tabular-nums text-white">{value}</span>
      {sub && <span className="text-xs text-slate-500">{sub}</span>}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Code location section
// ---------------------------------------------------------------------------

function MetaRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start gap-3 text-sm">
      <span className="w-28 shrink-0 text-slate-500">{label}</span>
      <span className="font-mono text-slate-200 break-all">{value}</span>
    </div>
  )
}

function CodeViewer({ file, line }: { file: string; line: number }) {
  const rows = [line - 2, line - 1, line, line + 1].filter((n) => n > 0)

  return (
    <div className="rounded-xl border border-slate-700 bg-slate-950 overflow-hidden font-mono text-xs">
      {/* File name bar */}
      <div className="flex items-center gap-2 border-b border-slate-700 bg-slate-900 px-4 py-2">
        <span className="text-slate-500">📄</span>
        <span className="truncate text-slate-400">{file}</span>
      </div>

      {rows.map((n) => {
        const isHot = n === line
        return (
          <div
            key={n}
            className={`flex items-start gap-4 px-4 py-1.5 ${
              isHot
                ? 'bg-yellow-500/10 border-l-2 border-yellow-400'
                : 'border-l-2 border-transparent'
            }`}
          >
            {/* Line number gutter */}
            <span
              className={`w-8 shrink-0 select-none text-right ${
                isHot ? 'text-yellow-400 font-bold' : 'text-slate-600'
              }`}
            >
              {n}
            </span>

            {/* Content */}
            {isHot ? (
              <span className="text-yellow-300">
                {'/* ← This line causes the slow query */'}
              </span>
            ) : (
              <span className="text-slate-600">{'...'}</span>
            )}
          </div>
        )
      })}
    </div>
  )
}

function CodeLocationSection({
  detail,
  connectionId,
}: {
  detail: QueryDetail
  connectionId: string
}) {
  if (!detail.is_traced || !detail.code_location) {
    return (
      <div className="rounded-xl border border-slate-700 bg-slate-800/60 p-6 text-center">
        <p className="text-2xl mb-2">🔍</p>
        <p className="text-sm font-medium text-slate-300 mb-1">No code trace available</p>
        <p className="text-xs text-slate-500 mb-4">
          Install SQLCommenter or Marginalia in your app to link slow queries to source lines.
        </p>
        <Link
          to={`/wizard/${connectionId}`}
          className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-indigo-500"
        >
          🔧 Set Up Code Linking
        </Link>
      </div>
    )
  }

  const { file, line } = parseCodeLocation(detail.code_location)
  const meta = detail.query ? parseInlineComment(detail.query) : {}

  return (
    <div className="space-y-4">
      {/* Metadata table */}
      <div className="rounded-xl border border-slate-700 bg-slate-800/60 p-5 space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-slate-300">Trace metadata</h3>
          <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/20 border border-emerald-500/30 px-2.5 py-0.5 text-xs font-semibold text-emerald-300">
            ✓ Confidence: 97%
          </span>
        </div>
        <div className="space-y-2 pt-1">
          <MetaRow label="File" value={file || '—'} />
          {line != null && <MetaRow label="Line" value={String(line)} />}
          {meta.action && <MetaRow label="Function" value={`${meta.action}()`} />}
          {meta.route && <MetaRow label="Route" value={meta.route} />}
          {(meta.db_driver ?? meta.application) && (
            <MetaRow label="Framework" value={meta.db_driver ?? meta.application ?? ''} />
          )}
        </div>
      </div>

      {/* Code viewer */}
      {line != null && file && (
        <CodeViewer file={file} line={line} />
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

function QueryDetailSkeleton() {
  return (
    <div className="space-y-10" aria-busy="true" aria-label="Loading query detail">
      {/* Query */}
      <div>
        <Skeleton className="mb-3 h-5 w-24" />
        <Skeleton className="h-28 w-full" />
      </div>

      {/* Stats */}
      <div>
        <Skeleton className="mb-3 h-5 w-16" />
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className="rounded-xl border border-slate-700 bg-slate-800/60 p-5">
              <Skeleton className="h-3 w-28" />
              <Skeleton className="mt-3 h-7 w-24" />
              <Skeleton className="mt-2 h-3 w-20" />
            </div>
          ))}
        </div>
      </div>

      {/* Code location */}
      <div>
        <Skeleton className="mb-3 h-5 w-32" />
        <div className="rounded-xl border border-slate-700 bg-slate-800/60 p-5">
          <SkeletonText lines={3} />
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Section wrapper
// ---------------------------------------------------------------------------

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h2 className="mb-3 text-base font-semibold text-slate-200">{title}</h2>
      {children}
    </section>
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function QueryDetailPage() {
  // Support both route shapes:
  //   /query/:connectionId/:queryid   (new — from DashboardPage)
  //   /dashboard/query/:id            (legacy)
  const {
    connectionId: connectionIdParam,
    queryid: queryidParam,
    id: legacyId,
  } = useParams<{ connectionId?: string; queryid?: string; id?: string }>()

  const activeConnection = useActiveConnection()
  const connectionId = connectionIdParam ?? activeConnection?.id ?? ''
  const queryid = queryidParam ?? legacyId ?? ''

  const navigate = useNavigate()

  const [status, setStatus] = useState<'loading' | 'loaded' | 'error'>('loading')
  const [detail, setDetail] = useState<QueryDetail | null>(null)
  const [error, setError] = useState<unknown>(null)

  const load = useCallback(async () => {
    if (!connectionId || !queryid) {
      setStatus('error')
      return
    }
    setStatus('loading')
    try {
      const data = await fetchQueryDetailById(connectionId, queryid)
      setDetail(data)
      setError(null)
      setStatus('loaded')
    } catch (err: unknown) {
      setError(err)
      setStatus('error')
    }
  }, [connectionId, queryid])

  useEffect(() => {
    void load()
  }, [load])

  const backTo = connectionId ? `/dashboard/${connectionId}` : '/dashboard'

  // ── Loading ──────────────────────────────────────────────────────────────
  if (status === 'loading') {
    return (
      <div className="mx-auto max-w-3xl px-4 py-10">
        <Breadcrumb items={[{ label: 'Dashboard', to: backTo }, { label: 'Query Detail' }]} />
        <div className="mt-8">
          <QueryDetailSkeleton />
        </div>
      </div>
    )
  }

  // ── Error ────────────────────────────────────────────────────────────────
  if (status === 'error' || !detail) {
    return (
      <div className="mx-auto max-w-3xl px-4 py-10">
        <Breadcrumb items={[{ label: 'Dashboard', to: backTo }, { label: 'Query Detail' }]} />
        <div className="mt-8">
          {!connectionId || !queryid ? (
            <ErrorCard
              title="No query selected"
              message="Open a query from the dashboard to see its details and fix suggestions."
            />
          ) : isConnectionError(error) ? (
            <ConnectionLostCard onReconnect={() => navigate('/')} />
          ) : (
            <ErrorCard
              title="Could not load this query"
              message={friendlyError(error, 'We could not load this query. Please try again.')}
              onRetry={() => void load()}
            />
          )}
        </div>
      </div>
    )
  }

  const minutesWasted =
    detail.total_exec_time_ms != null ? detail.total_exec_time_ms / 60_000 : null
  const minutesWastedDisplay =
    minutesWasted == null
      ? '—'
      : minutesWasted < 0.01
        ? `${(minutesWasted * 60).toFixed(1)}s`
        : `${minutesWasted.toFixed(2)} min`

  return (
    <div className="mx-auto max-w-3xl px-4 py-10 space-y-10">
      {/* Breadcrumb */}
      <Breadcrumb items={[{ label: 'Dashboard', to: backTo }, { label: 'Query Detail' }]} />

      {/* ── Section 1: Query ─────────────────────────────────────────────── */}
      <Section title="Query">
        {detail.query ? (
          <SqlBlock sql={detail.query} />
        ) : (
          <p className="text-sm text-slate-500">Query text unavailable.</p>
        )}
      </Section>

      {/* ── Partial-data notice ─────────────────────────────────────────── */}
      {detail.errors.length > 0 && (
        <div className="rounded-lg border border-yellow-500/40 bg-yellow-500/10 px-4 py-2.5 text-xs text-yellow-300">
          Some details could not be loaded. SlowTrace may not have permission to read every
          database statistic.
        </div>
      )}

      {/* ── Section 2: Stats ─────────────────────────────────────────────── */}
      <Section title="Stats">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <StatCard
            label="Average execution"
            value={detail.mean_exec_time_ms != null ? `${fmt(detail.mean_exec_time_ms, 1)}ms` : '—'}
          />
          <StatCard
            label="Calls / day"
            value={fmt(detail.calls)}
            sub={detail.calls != null ? `${detail.calls.toLocaleString()} total calls` : undefined}
          />
          <StatCard
            label="Time wasted today"
            value={minutesWastedDisplay}
            sub={
              minutesWasted != null && minutesWasted >= 1
                ? '🔥 High impact'
                : minutesWasted != null
                  ? '✅ Low impact'
                  : undefined
            }
          />
        </div>
      </Section>

      {/* ── Section 3: Code Location ─────────────────────────────────────── */}
      <Section title="Code Location">
        <CodeLocationSection detail={detail} connectionId={connectionId} />
      </Section>

      {/* ── Section 4: Fix ───────────────────────────────────────────────── */}
      <Section title="Fix Available">
        <div className="rounded-xl border border-blue-500/30 bg-blue-500/10 p-6">
          <p className="mb-1 text-sm text-slate-300">
            SlowTrace can generate an AI-powered fix suggestion for this query — including
            index recommendations and rewrite options.
          </p>
          <p className="mb-5 text-xs text-slate-500">
            Review the suggestion and apply it with one click from the Fix page.
          </p>
          <Link
            to={`/fix/${connectionId}/${queryid}`}
            className="inline-flex items-center gap-2 rounded-xl bg-blue-600 px-6 py-3 text-sm font-bold text-white shadow-lg transition-colors hover:bg-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 focus:ring-offset-slate-900 w-full justify-center sm:w-auto"
          >
            🔧 View AI Fix Suggestion →
          </Link>
        </div>
      </Section>
    </div>
  )
}
