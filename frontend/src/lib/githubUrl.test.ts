import { describe, expect, it } from 'vitest'
import { parseGitHubRepo } from '@/lib/githubUrl'

describe('parseGitHubRepo', () => {
  it('parses the standard HTTPS form', () => {
    expect(parseGitHubRepo('https://github.com/myorg/myapp')).toEqual({
      owner: 'myorg',
      repo: 'myapp',
    })
  })

  it('accepts a scheme-less URL', () => {
    expect(parseGitHubRepo('github.com/myorg/myapp')).toEqual({
      owner: 'myorg',
      repo: 'myapp',
    })
  })

  it('strips the .git suffix and trailing slash', () => {
    expect(parseGitHubRepo('https://github.com/myorg/myapp.git')).toEqual({
      owner: 'myorg',
      repo: 'myapp',
    })
    expect(parseGitHubRepo('https://github.com/myorg/myapp/')).toEqual({
      owner: 'myorg',
      repo: 'myapp',
    })
  })

  it('accepts the SSH form', () => {
    expect(parseGitHubRepo('git@github.com:myorg/myapp.git')).toEqual({
      owner: 'myorg',
      repo: 'myapp',
    })
  })

  it('trims surrounding whitespace', () => {
    expect(parseGitHubRepo('  https://github.com/myorg/myapp  ')).toEqual({
      owner: 'myorg',
      repo: 'myapp',
    })
  })

  it('ignores extra path segments beyond owner/repo', () => {
    expect(parseGitHubRepo('https://github.com/myorg/myapp/tree/main/src')).toEqual({
      owner: 'myorg',
      repo: 'myapp',
    })
  })

  it('accepts the www. host', () => {
    expect(parseGitHubRepo('https://www.github.com/myorg/myapp')).toEqual({
      owner: 'myorg',
      repo: 'myapp',
    })
  })

  it.each([
    '',
    '   ',
    'not a url',
    'https://github.com/myorg',
    'myorg/myapp',
    'https://gitlab.com/myorg/myapp',
    'git@gitlab.com:myorg/myapp.git',
    'https://example.com/',
  ])('rejects %j', (input) => {
    expect(parseGitHubRepo(input)).toBeNull()
  })
})
