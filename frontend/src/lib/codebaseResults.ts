/**
 * Pure derivations for rendering codebase scan results — the numbered code
 * blocks and GitHub deep links used by components/codebase/ScanResults.
 */

import type { CodebaseFinding } from '@/types/codebase'

export interface CodeLine {
  no: number
  text: string
  hl: boolean
}

/**
 * Numbered source lines for a finding.
 *
 * Semgrep's `code` is exactly the matched region (the backend reads it back out
 * of the clone, since Semgrep redacts `extra.lines` without an account), so the
 * whole block is highlighted and line numbers start at `line_start`.
 */
export function buildCodeLines(finding: CodebaseFinding): CodeLine[] {
  const lines = (finding.code ?? '').split('\n')
  // A trailing newline in the snippet would otherwise render an empty line.
  if (lines.length > 1 && lines[lines.length - 1].trim() === '') lines.pop()
  // No snippet at all — render nothing rather than a phantom blank line.
  if (lines.length === 1 && lines[0].trim() === '') return []

  const first = finding.line_start > 0 ? finding.line_start : 1
  return lines.map((text, i) => ({ no: first + i, text, hl: true }))
}

/** GitHub blob URL for a repo-relative path, or null if the repo isn't "owner/name". */
export function githubBlobUrl(
  repoDisplay: string,
  branch: string | undefined,
  path: string,
  line: number,
): string | null {
  if (!/^[\w.-]+\/[\w.-]+$/.test(repoDisplay || '')) return null
  const ref = branch || 'HEAD'
  const safePath = path
    .split('/')
    .map((seg) => encodeURIComponent(seg))
    .join('/')
  return `https://github.com/${repoDisplay}/blob/${ref}/${safePath}${
    line ? `#L${line}` : ''
  }`
}

/**
 * The finding's own deep link, falling back to building one from the repo
 * display name when the backend could not (e.g. an unparseable URL).
 */
export function findingGitHubUrl(
  finding: CodebaseFinding,
  repoDisplay = '',
): string | null {
  if (finding.github_url) return finding.github_url
  return githubBlobUrl(repoDisplay, undefined, finding.file, finding.line_start)
}
