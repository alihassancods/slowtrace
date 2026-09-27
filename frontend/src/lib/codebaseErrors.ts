/**
 * Turn a codebase-scan failure into the exact display copy the design calls for.
 *
 * The backend always answers with a kind plus a message:
 *   { success: false, error: "private", message: "...", detail: "..." }
 * `detail` is git/semgrep output and is never rendered. When no kind is present
 * we fall back to sniffing the message, then to the HTTP status.
 */

import type {
  CodebaseErrorKind,
  CodebaseScanResult,
} from '@/types/codebase'

export const INVALID_URL_MESSAGE = 'Please enter a valid GitHub URL'

const MESSAGES: Record<CodebaseErrorKind, string> = {
  invalid_url: INVALID_URL_MESSAGE,
  private: 'This repo is private. Add a GitHub token to scan it.',
  not_found: 'Repository not found. Check the URL and try again.',
  rate_limited: 'GitHub rate limit reached. Add a token to continue.',
  network: 'Could not reach GitHub. Check your connection and try again.',
  scan_timeout: 'The scan took too long and was stopped. Try a smaller repository.',
  semgrep_not_installed: 'Semgrep is not installed on the server.',
  semgrep_error: 'The scanner failed to run. Please try again.',
  rules_missing:
    'The analysis rules are missing from this installation. Contact an administrator.',
  unknown: 'The scan failed. Check the repository URL and try again.',
}

const GENERIC_FALLBACK = MESSAGES.unknown

const KINDS = Object.keys(MESSAGES) as CodebaseErrorKind[]

/** True when a backend `error` value is one of our known kinds. */
export function isCodebaseErrorKind(value: unknown): value is CodebaseErrorKind {
  return typeof value === 'string' && (KINDS as string[]).includes(value)
}

/** Map an HTTP status (when no explicit kind is present) to an error kind. */
export function kindFromStatus(status: number): CodebaseErrorKind {
  if (status === 401 || status === 403) return 'private'
  if (status === 404) return 'not_found'
  if (status === 429) return 'rate_limited'
  if (status === 400) return 'invalid_url'
  return 'unknown'
}

/**
 * Resolve display copy from a scan error payload:
 *   1. a known `error` kind → its mapped message
 *   2. keyword sniffing of `error`/`message` for prose-only backends
 *   3. generic fallback
 */
export function codebaseScanErrorMessage(
  result: Pick<CodebaseScanResult, 'error' | 'message'>,
): string | null {
  const { error, message } = result
  if (!error && !message) return null

  if (isCodebaseErrorKind(error)) return MESSAGES[error]

  const sniffed = kindFromMessage(`${error ?? ''} ${message ?? ''}`.trim())
  if (sniffed) return MESSAGES[sniffed]

  return GENERIC_FALLBACK
}

/** Display copy for a bare HTTP failure (no structured error in the body). */
export function codebaseHttpErrorMessage(status: number): string {
  return MESSAGES[kindFromStatus(status)] ?? GENERIC_FALLBACK
}

/** Best-effort keyword sniffing for backends that only send prose. */
function kindFromMessage(text: string): CodebaseErrorKind | null {
  const lowered = text.toLowerCase()
  if (lowered.includes('rate limit') || lowered.includes('too many requests'))
    return 'rate_limited'
  if (lowered.includes('authentication failed') || lowered.includes('could not read username'))
    return 'private'
  if (lowered.includes('private') || lowered.includes('access denied')) return 'private'
  if (lowered.includes('timed out') || lowered.includes('timeout')) return 'scan_timeout'
  if (lowered.includes('could not resolve host') || lowered.includes('connection'))
    return 'network'
  if (lowered.includes('not found') || lowered.includes('does not exist'))
    return 'not_found'
  if (lowered.includes('valid github') || lowered.includes('could not parse'))
    return 'invalid_url'
  return null
}
