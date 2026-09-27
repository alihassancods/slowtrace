import { useCallback, useEffect, useRef, useState } from 'react'
import { CodebaseApiError, streamCodebaseScan } from '@/api/codebase'
import ErrorCard from '@/components/ErrorCard'
import ScanResults from '@/components/codebase/ScanResults'
import { friendlyError } from '@/lib/errors'
import {
  codebaseHttpErrorMessage,
  codebaseScanErrorMessage,
  INVALID_URL_MESSAGE,
} from '@/lib/codebaseErrors'
import { repoLabel } from '@/lib/codebaseDisplay'
import { parseGitHubRepo } from '@/lib/githubUrl'
import { useActiveConnection } from '@/lib/activeConnection'
import type { CodebaseProgressData, CodebaseScanResult } from '@/types/codebase'

const GENERIC_SCAN_ERROR =
  'The scan failed. Check the repository URL and try again.'
const TRUNCATED_SCAN_ERROR =
  'The scan ended before it finished. Please try again.'

/** Any "Analyzing…" copy from the backend flips the checklist to stage 3. */
const ANALYZING_RE = /analyz/i

function Spinner({ className = 'h-4 w-4' }: { className?: string }) {
  return (
    <svg
      className={`${className} animate-spin`}
      fill="none"
      viewBox="0 0 24 24"
      aria-hidden
    >
      <circle
        className="opacity-25"
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        strokeWidth="4"
      />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
    </svg>
  )
}

/** One checklist row: done ✅, active spinner, or a dim dot when not reached. */
function StepLine({
  state,
  text,
}: {
  state: 'done' | 'active' | 'pending'
  text: string
}) {
  const tone =
    state === 'done'
      ? 'text-slate-200'
      : state === 'active'
        ? 'text-white'
        : 'text-slate-600'
  return (
    <li className="flex items-center gap-3 text-sm">
      <span className="flex w-5 shrink-0 items-center justify-center text-base leading-none">
        {state === 'done' && <span aria-hidden>✅</span>}
        {state === 'active' && (
          <span className="text-indigo-400">
            <Spinner className="h-4 w-4" />
          </span>
        )}
        {state === 'pending' && (
          <span className="h-1.5 w-1.5 rounded-full bg-slate-600" />
        )}
      </span>
      <span className={tone}>{text}</span>
      {state === 'active' && (
        <span className="sr-only">in progress</span>
      )}
    </li>
  )
}

/**
 * Setup instructions, shown instead of a failure card when the server is simply
 * missing Semgrep — nothing the user did wrong, and the fix is one command.
 */
function SetupInstruction({ command }: { command?: string }) {
  const cmd = command || 'pip install semgrep'
  const [copied, setCopied] = useState(false)

  async function copy() {
    try {
      await navigator.clipboard.writeText(cmd)
    } catch {
      return // clipboard blocked — the command is still visible
    }
    setCopied(true)
  }

  return (
    <div className="mt-5 rounded-xl border border-sky-500/40 bg-sky-500/10 p-5">
      <p className="text-sm font-semibold text-sky-200">
        🔧 One-time setup needed on the server
      </p>
      <p className="mt-1.5 text-sm leading-relaxed text-sky-200/80">
        Codebase scanning runs Semgrep, which isn’t installed on this SlowTrace
        server yet. Run this where the backend is installed, then scan again —
        no restart needed.
      </p>
      <div className="mt-3 flex items-center gap-2">
        <code className="flex-1 overflow-x-auto whitespace-pre rounded-lg border border-slate-700 bg-slate-950/80 px-3 py-2 font-mono text-xs text-slate-200">
          {cmd}
        </code>
        <button
          type="button"
          onClick={copy}
          className="shrink-0 rounded-lg border border-slate-600 px-3 py-2 text-xs font-semibold text-slate-300 transition-colors hover:border-slate-400 hover:text-white"
        >
          {copied ? '✓ Copied!' : '📋 Copy'}
        </button>
      </div>
    </div>
  )
}

/** Map any thrown API failure to display copy. Never leaks raw text. */
function scanFailureMessage(err: unknown): string {
  if (err instanceof CodebaseApiError) {
    const mapped = codebaseScanErrorMessage({
      error: err.body?.error,
      message: err.body?.message,
    })
    if (mapped && err.body?.error) return mapped
    return codebaseHttpErrorMessage(err.status)
  }
  return friendlyError(err, 'Could not reach the SlowTrace server. Try again.')
}

type Phase = 'idle' | 'scanning' | 'done' | 'error'

export default function CodebasePage() {
  const connection = useActiveConnection()

  const [repoUrl, setRepoUrl] = useState('')
  const [token, setToken] = useState('')
  const [fieldError, setFieldError] = useState<string | null>(null)

  const [phase, setPhase] = useState<Phase>('idle')
  const [result, setResult] = useState<CodebaseScanResult | null>(null)
  /** Kept as the raw payload so `error` kind + `install_command` survive. */
  const [failure, setFailure] = useState<CodebaseScanResult | null>(null)
  const [repoDisplay, setRepoDisplay] = useState('')
  /** Advisory note from the stream, e.g. a very large repository. */
  const [warning, setWarning] = useState<string | null>(null)

  // Checklist progress, driven entirely by the stream's stage events.
  const [connected, setConnected] = useState(false)
  const [analyzing, setAnalyzing] = useState(false)
  const [elapsed, setElapsed] = useState(0)

  const abortRef = useRef<AbortController | null>(null)
  useEffect(() => () => abortRef.current?.abort(), [])

  const beginScan = useCallback(() => {
    const repo = parseGitHubRepo(repoUrl)
    if (!repo) {
      setFieldError(INVALID_URL_MESSAGE)
      return
    }
    setFieldError(null)
    setFailure(null)
    setResult(null)
    setWarning(null)
    setConnected(false)
    setAnalyzing(false)
    setElapsed(0)
    setRepoDisplay(`${repo.owner}/${repo.repo}`)
    setPhase('scanning')

    const controller = new AbortController()
    abortRef.current = controller

    void (async () => {
      let finished = false
      try {
        const stream = streamCodebaseScan(
          {
            repo_url: repoUrl.trim(),
            github_token: token.trim() || undefined,
            connection_id: connection?.id,
          },
          controller.signal,
        )

        for await (const event of stream) {
          if (controller.signal.aborted) break
          switch (event.stage) {
            case 'started':
              setConnected(true)
              break
            case 'progress': {
              const data = event.data as CodebaseProgressData
              setConnected(true)
              if (typeof data.elapsed_seconds === 'number') {
                setElapsed(data.elapsed_seconds)
              }
              if (data.warning && data.message) {
                setWarning(data.message)
                break // an advisory note is not a phase change
              }
              if (ANALYZING_RE.test(data.message ?? '')) setAnalyzing(true)
              break
            }
            case 'complete': {
              const scan = event.data as CodebaseScanResult
              finished = true
              setResult(scan)
              setRepoDisplay(repoLabel(scan.repo_url, `${repo.owner}/${repo.repo}`))
              if (scan.success === false) {
                setFailure(scan)
                setPhase('error')
              } else {
                if (scan.size_warning) setWarning(scan.size_warning)
                setPhase('done')
              }
              break
            }
            case 'error': {
              finished = true
              setFailure(event.data as CodebaseScanResult)
              setPhase('error')
              break
            }
          }
        }
        if (!finished && !controller.signal.aborted) {
          setFailure({ success: false, message: TRUNCATED_SCAN_ERROR })
          setPhase('error')
        }
      } catch (err) {
        if (controller.signal.aborted) return
        setFailure({ success: false, message: scanFailureMessage(err) })
        setPhase('error')
      } finally {
        if (abortRef.current === controller) abortRef.current = null
      }
    })()
  }, [connection?.id, repoUrl, token])

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    beginScan()
  }

  function resetToForm() {
    abortRef.current?.abort()
    setPhase('idle')
    setResult(null)
    setFailure(null)
    setWarning(null)
    setFieldError(null)
    setRepoDisplay('')
    setConnected(false)
    setAnalyzing(false)
    setElapsed(0)
    setRepoUrl('')
    setToken('')
  }

  const showForm = phase === 'idle' || phase === 'error'
  const failureMessage =
    codebaseScanErrorMessage({ error: failure?.error, message: failure?.message }) ??
    GENERIC_SCAN_ERROR

  return (
    <div className="mx-auto max-w-3xl px-4 py-10">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-white">🔍 Codebase Analysis</h1>
        <p className="mt-2 text-sm leading-relaxed text-slate-400">
          Find database performance problems in your code before they hit
          production.
        </p>
      </div>

      {/* Connect form */}
      {showForm && (
        <form
          onSubmit={handleSubmit}
          className="rounded-2xl border border-slate-700 bg-slate-800/60 p-6 shadow-xl"
          noValidate
        >
          <div className="flex flex-col gap-4">
            <div>
              <label
                htmlFor="repo-url"
                className="mb-1.5 block text-sm font-medium text-slate-300"
              >
                GitHub Repository URL:
              </label>
              <input
                id="repo-url"
                type="text"
                value={repoUrl}
                onChange={(e) => {
                  setRepoUrl(e.target.value)
                  if (fieldError) setFieldError(null)
                }}
                placeholder="https://github.com/username/repository"
                autoComplete="off"
                spellCheck={false}
                aria-invalid={fieldError ? true : undefined}
                className="w-full rounded-lg border border-slate-600 bg-slate-900 px-3 py-2.5 font-mono text-sm text-white placeholder-slate-600 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
            </div>

            <div>
              <label
                htmlFor="github-token"
                className="mb-1.5 block text-sm font-medium text-slate-300"
              >
                GitHub Token{' '}
                <span className="font-normal text-slate-500">
                  (optional, for private repos):
                </span>
              </label>
              <input
                id="github-token"
                type="password"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder="ghp_xxxxxxxxxxxxxxxxxxxx"
                autoComplete="off"
                className="w-full rounded-lg border border-slate-600 bg-slate-900 px-3 py-2.5 font-mono text-sm text-white placeholder-slate-600 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
              <p className="mt-1.5 text-xs text-slate-500">
                Private repo? Get token at{' '}
                <a
                  href="https://github.com/settings/tokens"
                  target="_blank"
                  rel="noreferrer"
                  className="text-indigo-400 hover:underline"
                >
                  github.com/settings
                </a>
              </p>
            </div>

            {fieldError && (
              <p className="text-sm text-red-400" role="alert">
                {fieldError}
              </p>
            )}

            <button
              type="submit"
              className="mt-1 inline-flex w-full items-center justify-center gap-2 rounded-lg bg-indigo-600 px-5 py-2.5 text-sm font-bold text-white shadow transition-colors hover:bg-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 focus:ring-offset-slate-800"
            >
              🔍 Scan Repository
            </button>
          </div>
        </form>
      )}

      {/* Failure — form stays available above so inputs can be fixed */}
      {phase === 'error' && failure && (
        failure.error === 'semgrep_not_installed' ? (
          <SetupInstruction command={failure.install_command} />
        ) : (
          <div className="mt-5">
            <ErrorCard title="Scan failed" message={failureMessage} onRetry={beginScan} />
          </div>
        )
      )}

      {/* Live scan status */}
      {phase === 'scanning' && (
        <div className="mt-6 rounded-xl border border-slate-700 bg-slate-800/60 p-5">
          <h2 className="mb-4 text-sm font-semibold uppercase tracking-widest text-slate-400">
            {repoDisplay || 'Repository'}
          </h2>
          <ul className="space-y-3" aria-live="polite">
            <StepLine
              state={connected ? 'done' : 'active'}
              text="Repository connected"
            />
            <StepLine
              state={!connected ? 'pending' : analyzing ? 'done' : 'active'}
              text="Cloning repository..."
            />
            <StepLine
              state={analyzing ? 'active' : 'pending'}
              text="Analyzing with Semgrep..."
            />
          </ul>
          {warning && (
            <p className="mt-4 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-300">
              ⚠️ {warning}
            </p>
          )}
          <p className="mt-4 flex items-center gap-2 text-xs text-slate-500">
            <span className="text-indigo-400">
              <Spinner className="h-3 w-3" />
            </span>
            {elapsed > 0 ? `${elapsed}s elapsed` : 'Starting…'} — this can take a
            minute on larger repositories.
          </p>
        </div>
      )}

      {/* Results */}
      {phase === 'done' && result && (
        <div className="mt-6">
          <ScanResults
            result={result}
            repoDisplay={repoDisplay}
            durationSeconds={result.scan_duration_seconds}
            onRescan={beginScan}
            onScanAnother={resetToForm}
          />
        </div>
      )}
    </div>
  )
}
