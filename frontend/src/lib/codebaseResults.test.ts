import { describe, expect, it } from 'vitest'
import {
  buildCodeLines,
  findingGitHubUrl,
  githubBlobUrl,
} from '@/lib/codebaseResults'
import type { CodebaseFinding } from '@/types/codebase'

function finding(overrides: Partial<CodebaseFinding>): CodebaseFinding {
  return {
    rule_id: 'django-n-plus-one-loop',
    smell_id: 'n_plus_one',
    file: 'shop/views.py',
    line_start: 23,
    line_end: 23,
    code: '    items = order.items.all()',
    message: "N+1 query detected. 'order.items.all()' inside a loop",
    raw_severity: 'ERROR',
    fix_code: "queryset = queryset.prefetch_related('items')",
    category: 'performance',
    github_url: 'https://github.com/myorg/myapp/blob/HEAD/shop/views.py#L23',
    name: 'N+1 Query Pattern',
    severity: 'critical',
    impact: 'Multiplies database queries by row count',
    docs_url: 'https://docs.djangoproject.com/en/stable/ref/models/querysets/',
    ...overrides,
  }
}

describe('buildCodeLines', () => {
  it('numbers a single-line match from line_start and highlights it', () => {
    expect(buildCodeLines(finding({}))).toEqual([
      { no: 23, text: '    items = order.items.all()', hl: true },
    ])
  })

  it('keeps every line of a multi-line match, numbered in order', () => {
    const lines = buildCodeLines(
      finding({
        line_start: 10,
        line_end: 12,
        code: 'for order in orders:\n    items = order.items.all()\n    total += 1',
      }),
    )
    expect(lines.map((l) => l.no)).toEqual([10, 11, 12])
    expect(lines.every((l) => l.hl)).toBe(true)
    expect(lines[1].text).toBe('    items = order.items.all()')
  })

  it('drops the empty line left by a trailing newline', () => {
    const lines = buildCodeLines(finding({ code: 'a = 1\nb = 2\n' }))
    expect(lines.map((l) => l.no)).toEqual([23, 24])
  })

  it('preserves leading indentation for rendering', () => {
    const [line] = buildCodeLines(finding({ code: '\t\titems = order.items.all()' }))
    expect(line.text).toBe('\t\titems = order.items.all()')
  })

  it('returns nothing for an empty snippet instead of a phantom line', () => {
    expect(buildCodeLines(finding({ code: '' }))).toEqual([])
  })

  it('tolerates a missing code field', () => {
    const bare = { ...finding({}), code: undefined as unknown as string }
    expect(buildCodeLines(bare)).toEqual([])
  })

  it('clamps a non-positive line number to 1', () => {
    expect(buildCodeLines(finding({ line_start: 0 }))[0].no).toBe(1)
  })
})

describe('githubBlobUrl', () => {
  it('builds a blob URL with a line anchor', () => {
    expect(
      githubBlobUrl('myorg/myapp', 'main', 'views/order_views.py', 23),
    ).toBe(
      'https://github.com/myorg/myapp/blob/main/views/order_views.py#L23',
    )
  })

  it("defaults the branch to 'HEAD' when unknown", () => {
    expect(githubBlobUrl('myorg/myapp', undefined, 'a.py', 1)).toBe(
      'https://github.com/myorg/myapp/blob/HEAD/a.py#L1',
    )
  })

  it('URL-encodes path segments but keeps slashes', () => {
    expect(
      githubBlobUrl('myorg/myapp', 'main', 'src/my dir/app model.py', 3),
    ).toBe(
      'https://github.com/myorg/myapp/blob/main/src/my%20dir/app%20model.py#L3',
    )
  })

  it('returns null when the repo is not owner/name', () => {
    expect(githubBlobUrl('', 'main', 'a.py', 1)).toBeNull()
    expect(githubBlobUrl('not a repo', 'main', 'a.py', 1)).toBeNull()
    expect(githubBlobUrl('a/b/c', 'main', 'a.py', 1)).toBeNull()
  })
})

describe('findingGitHubUrl', () => {
  it('prefers the link the backend already built', () => {
    expect(findingGitHubUrl(finding({}), 'other/repo')).toBe(
      'https://github.com/myorg/myapp/blob/HEAD/shop/views.py#L23',
    )
  })

  it('falls back to building one from the repo display name', () => {
    expect(findingGitHubUrl(finding({ github_url: '' }), 'myorg/myapp')).toBe(
      'https://github.com/myorg/myapp/blob/HEAD/shop/views.py#L23',
    )
  })

  it('returns null when neither source is usable', () => {
    expect(findingGitHubUrl(finding({ github_url: '' }), '')).toBeNull()
  })
})
