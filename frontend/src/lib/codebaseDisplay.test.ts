import { describe, expect, it } from 'vitest'
import { filesLabel, repoLabel, scanDurationLabel } from '@/lib/codebaseDisplay'

describe('repoLabel', () => {
  it.each([
    ['https://github.com/myorg/myapp', 'myorg/myapp'],
    ['https://github.com/myorg/myapp.git', 'myorg/myapp'],
    ['https://github.com/myorg/myapp/', 'myorg/myapp'],
    ['git@github.com:myorg/myapp.git', 'myorg/myapp'],
    ['https://github.com/myorg/myapp/blob/main/x.py', 'myorg/myapp'],
  ])('derives owner/repo from %s', (url, expected) => {
    expect(repoLabel(url)).toBe(expected)
  })

  it('returns the fallback for anything not a GitHub URL', () => {
    expect(repoLabel('', 'a/b')).toBe('a/b')
    expect(repoLabel(undefined, 'x/y')).toBe('x/y')
    expect(repoLabel('https://gitlab.com/a/b')).toBe('')
  })
})

describe('filesLabel', () => {
  it('pluralises and hides unknown counts', () => {
    expect(filesLabel(1)).toBe('1 file')
    expect(filesLabel(128)).toBe('128 files')
    expect(filesLabel(0)).toBe('')
    expect(filesLabel(undefined)).toBe('')
  })
})

describe('scanDurationLabel', () => {
  it('uses the design sentence with the engine named', () => {
    expect(scanDurationLabel(23.4)).toBe('Scanned in 23.4 seconds using Semgrep')
  })

  it('switches to minutes past 60 seconds', () => {
    expect(scanDurationLabel(125.5)).toBe('Scanned in 2.1 minutes using Semgrep')
  })

  it('returns null when the duration is unknown or zero', () => {
    expect(scanDurationLabel(0)).toBeNull()
    expect(scanDurationLabel(undefined)).toBeNull()
    expect(scanDurationLabel(null)).toBeNull()
    expect(scanDurationLabel(-1)).toBeNull()
  })
})
