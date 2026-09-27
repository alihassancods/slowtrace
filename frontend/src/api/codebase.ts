import type {
  CodebaseScanRequest,
  CodebaseScanResult,
  CodebaseStageEvent,
} from '@/types/codebase'

/**
 * Thrown when the scan endpoint answers non-OK before any stream starts.
 * Keeps the HTTP status plus the structured body when the backend sent one, so
 * the caller can map `error` to display copy (see lib/codebaseErrors.ts).
 */
export class CodebaseApiError extends Error {
  constructor(
    readonly status: number,
    readonly body?: CodebaseScanResult,
  ) {
    super(`HTTP ${status}`)
    this.name = 'CodebaseApiError'
  }
}

/**
 * Start a scan and yield each SSE stage as it arrives.
 *
 * `EventSource` is deliberately not used: it can only issue GET requests, and
 * the scan endpoint is a POST with a JSON body, so the stream is consumed from
 * `fetch` + a ReadableStream instead.
 */
export async function* streamCodebaseScan(
  request: CodebaseScanRequest,
  signal?: AbortSignal,
): AsyncGenerator<CodebaseStageEvent> {
  const resp = await fetch('/api/codebase/scan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(request),
    signal,
  })

  if (!resp.ok) {
    throw new CodebaseApiError(resp.status, await errorBody(resp))
  }
  if (!resp.body) {
    throw new CodebaseApiError(resp.status || 502)
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      // Frames are separated by a blank line; keep the trailing partial frame.
      let boundary = buffer.indexOf('\n\n')
      while (boundary !== -1) {
        const frame = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)
        const parsed = parseSseFrame(frame)
        if (parsed) yield parsed
        boundary = buffer.indexOf('\n\n')
      }
    }
    const tail = parseSseFrame(buffer)
    if (tail) yield tail
  } finally {
    reader.releaseLock()
  }
}

/**
 * Parse one `event:` / `data:` frame. Comment (`:`) and other fields ignored.
 * Exported for tests; the stream reader below is the only production caller.
 */
export function parseSseFrame(frame: string): CodebaseStageEvent | null {
  let event = ''
  const dataLines: string[] = []
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
  }
  if (dataLines.length === 0) return null

  let payload: unknown
  try {
    payload = JSON.parse(dataLines.join('\n'))
  } catch {
    return null // a truncated frame must not kill the stream
  }

  const record = payload as Record<string, unknown>
  // The backend sends both a named event and a `stage` field; prefer `stage`.
  const stage = (record.stage as string) ?? event
  if (stage !== 'started' && stage !== 'progress' && stage !== 'complete' && stage !== 'error') {
    return null
  }
  return { stage, data: (record.data ?? record) as CodebaseStageEvent['data'] }
}

/** Best-effort structured error body; a proxy may answer HTML instead. */
async function errorBody(resp: Response): Promise<CodebaseScanResult | undefined> {
  const body = await resp.json().catch(() => null)
  if (body && typeof body === 'object') {
    return {
      success: false,
      error: typeof body.error === 'string' ? body.error : undefined,
      message: typeof body.message === 'string' ? body.message : undefined,
    }
  }
  return undefined
}

/**
 * Fetch a cached scan without re-scanning (the stream's `complete` payload is
 * cached server-side keyed by repo url).
 */
export async function fetchCachedResults(
  repoUrl: string,
): Promise<CodebaseScanResult | null> {
  const resp = await fetch(
    `/api/codebase/results?repo_url=${encodeURIComponent(repoUrl)}`,
  )
  const body = (await resp.json().catch(() => null)) as {
    success?: boolean
    data?: CodebaseScanResult
  } | null
  if (!resp.ok || !body?.success || !body.data) return null
  return body.data
}
