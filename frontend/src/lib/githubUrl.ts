/**
 * GitHub repository URL parsing + validation, mirroring the backend's
 * `_parse_owner_repo` in `agent/codebase/github_scanner.py` so client-side
 * and server-side parsing agree.
 */

export interface GitHubRepoRef {
  owner: string
  repo: string
}

const GITHUB_HOST = /^(www\.)?github\.com$/i

/**
 * Parse a GitHub repo URL into `{owner, repo}` or return null.
 *
 * Accepts:
 *   https://github.com/myorg/myapp
 *   github.com/myorg/myapp
 *   https://github.com/myorg/myapp.git
 *   git@github.com:myorg/myapp.git
 *
 * Anything not hosted on github.com (including a bare "owner/repo") is
 * rejected — the design requires the full GitHub URL form.
 */
export function parseGitHubRepo(url: string): GitHubRepoRef | null {
  const trimmed = (url || '').trim()
  if (!trimmed) return null

  let parts: string[]

  const ssh = /^git@github\.com:(.+)$/i.exec(trimmed)
  if (ssh) {
    parts = ssh[1].split('/').filter(Boolean)
  } else {
    const segments = stripScheme(trimmed).split('/').filter(Boolean)
    if (segments.length < 3 || !GITHUB_HOST.test(segments[0])) return null
    parts = segments.slice(1)
  }

  if (parts.length < 2) return null
  const owner = parts[0]
  let repo = parts[1]
  if (repo.endsWith('.git')) repo = repo.slice(0, -4)
  if (!owner || !repo) return null
  return { owner, repo }
}

/** "https://github.com/o/r" → "github.com/o/r"; bare forms pass through. */
function stripScheme(value: string): string {
  const match = /^[a-z]+:\/\//i.exec(value)
  return match ? value.slice(match[0].length) : value
}
