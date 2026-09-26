/**
 * Turn any thrown value into short, plain-English display copy.
 *
 * Raw backend messages, status codes, and stack traces are never surfaced to
 * the user — callers get a human sentence, or their own fallback.
 */

const CONNECTION_PATTERN =
  /failed to fetch|networkerror|load failed|econnrefused|connection refused|connection reset|timed? ?out/i

/** Extract the HTTP status code from an error thrown by the API helpers. */
function statusOf(err: unknown): number | null {
  if (!(err instanceof Error)) return null
  const match = /HTTP (\d{3})/.exec(err.message)
  return match ? Number(match[1]) : null
}

/** True when the failure looks like the database or server is unreachable. */
export function isConnectionError(err: unknown): boolean {
  const status = statusOf(err)
  if (status === 502 || status === 503 || status === 504) return true
  if (err instanceof Error && CONNECTION_PATTERN.test(err.message)) return true
  return false
}

/** Map an error to display copy. Never leaks raw error text. */
export function friendlyError(
  err: unknown,
  fallback = 'Something went wrong. Please try again.',
): string {
  const status = statusOf(err)

  if (status === 404) {
    return 'We could not find that item. It may have been cleared from the database statistics.'
  }
  if (status === 502 || status === 503 || status === 504) {
    return 'Could not reach your database. Check that it is running and that your connection string is correct.'
  }
  if (status === 400 || status === 422) {
    return 'That request was rejected. Check your connection settings and try again.'
  }
  if (status === 401 || status === 403) {
    return 'SlowTrace was not allowed to read that data. Check the permissions of the database user.'
  }
  if (status !== null && status >= 500) {
    return 'SlowTrace hit an unexpected problem. Please try again in a moment.'
  }
  if (err instanceof Error && CONNECTION_PATTERN.test(err.message)) {
    return 'Could not reach the SlowTrace server. Check your network connection and try again.'
  }
  return fallback
}
