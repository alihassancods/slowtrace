import { describe, expect, it } from 'vitest'
import {
  codebaseScanErrorMessage,
  isCodebaseErrorKind,
  kindFromStatus,
} from '@/lib/codebaseErrors'

describe('kindFromStatus', () => {
  it.each([
    [400, 'invalid_url'],
    [401, 'private'],
    [403, 'private'],
    [404, 'not_found'],
    [429, 'rate_limited'],
    [500, 'unknown'],
  ] as const)('maps HTTP %i to %s', (status, kind) => {
    expect(kindFromStatus(status)).toBe(kind)
  })
})

describe('isCodebaseErrorKind', () => {
  it('accepts known kinds only', () => {
    expect(isCodebaseErrorKind('private')).toBe(true)
    expect(isCodebaseErrorKind('scan_timeout')).toBe(true)
    expect(isCodebaseErrorKind('boom')).toBe(false)
    expect(isCodebaseErrorKind(undefined)).toBe(false)
  })
})

describe('codebaseScanErrorMessage', () => {
  it('returns null when there is no error', () => {
    expect(codebaseScanErrorMessage({})).toBeNull()
    expect(codebaseScanErrorMessage({ error: undefined, message: undefined })).toBeNull()
  })

  it('maps each backend error kind to its design copy', () => {
    const cases: Array<[string, string]> = [
      ['private', 'This repo is private. Add a GitHub token to scan it.'],
      ['not_found', 'Repository not found. Check the URL and try again.'],
      ['rate_limited', 'GitHub rate limit reached. Add a token to continue.'],
      ['invalid_url', 'Please enter a valid GitHub URL'],
      ['network', 'Could not reach GitHub. Check your connection and try again.'],
      ['scan_timeout', 'The scan took too long and was stopped. Try a smaller repository.'],
      ['semgrep_error', 'The scanner failed to run. Please try again.'],
    ]
    for (const [kind, copy] of cases) {
      expect(codebaseScanErrorMessage({ error: kind, message: 'backend prose' })).toBe(copy)
    }
  })

  it('sniffs kind from prose when no known kind is sent', () => {
    expect(
      codebaseScanErrorMessage({ error: 'GitHub rate limit reached for this host' }),
    ).toBe('GitHub rate limit reached. Add a token to continue.')
    expect(codebaseScanErrorMessage({ message: 'Repository not found.' })).toBe(
      'Repository not found. Check the URL and try again.',
    )
    expect(
      codebaseScanErrorMessage({ message: 'git clone timed out after 60s' }),
    ).toBe('The scan took too long and was stopped. Try a smaller repository.')
  })

  it('falls back to generic copy for anything unrecognised', () => {
    expect(codebaseScanErrorMessage({ error: 'totally unknown' })).toBe(
      'The scan failed. Check the repository URL and try again.',
    )
    expect(
      codebaseScanErrorMessage({ error: 'weird_kind', message: 'something odd' }),
    ).toBe('The scan failed. Check the repository URL and try again.')
  })

  it('never echoes the technical detail back to the user', () => {
    // `detail` is git/semgrep output and is not part of the lookup at all.
    const result = {
      error: 'private',
      message: 'x',
      detail: 'fatal: Authentication failed for ghp_SECRET',
    } as const
    const copy = codebaseScanErrorMessage(result)
    expect(copy).toBe('This repo is private. Add a GitHub token to scan it.')
    expect(copy).not.toContain('ghp_SECRET')
  })
})
