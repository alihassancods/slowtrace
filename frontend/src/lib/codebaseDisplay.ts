/**
 * Human-readable labels for codebase scan data.
 *
 * Framework/language detection is gone with the custom scanner — Semgrep rules
 * are language-tagged individually — so this module is only about turning a
 * repository URL into a short display name.
 */

const GH_OWNER_REPO = /github\.com[/:]([^/]+)\/([^/#?]+)/i

/** "owner/repo" from any GitHub URL form (https, ssh, .git suffix), else fallback. */
export function repoLabel(repoUrl?: string, fallback = ''): string {
  const match = GH_OWNER_REPO.exec(repoUrl || '')
  if (!match) return fallback
  return `${match[1]}/${match[2].replace(/\.git$/i, '')}`
}

/** "N files" with correct pluralisation, or "" when the count is unknown. */
export function filesLabel(count?: number): string {
  if (!count || count < 1) return ''
  return `${count} ${count === 1 ? 'file' : 'files'}`
}

/**
 * The exact sentence the design asks for, e.g.
 * "Scanned in 23.4 seconds using Semgrep" — naming the engine explains a wait.
 * Returns null when the duration is unknown or zero.
 */
export function scanDurationLabel(seconds?: number | null): string | null {
  if (seconds == null || seconds <= 0) return null
  const spoken =
    seconds >= 60
      ? `${(seconds / 60).toFixed(1)} minutes`
      : `${seconds.toFixed(1)} seconds`
  return `Scanned in ${spoken} using Semgrep`
}
