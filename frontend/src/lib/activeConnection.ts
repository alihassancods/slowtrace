/**
 * The user's currently active database connection, persisted to localStorage.
 *
 * A `connection_id` is issued by the backend after a successful
 * `POST /api/connections/test` and is required by every subsequent endpoint.
 * Keeping the most recent one here lets every page resolve "the database I am
 * working with" even when the URL carries no id — so navigation and reloads no
 * longer drop the session.
 */

import { useSyncExternalStore } from 'react'

const STORAGE_KEY = 'slowtrace.active-connection'

export interface ActiveConnection {
  /** Backend-issued connection id. */
  id: string
  /** Optional user-supplied label, e.g. "Production DB". */
  nickname: string
  /** The connection string that produced this connection. */
  dsn: string
  /** Unix timestamp (ms) of when the connection was established. */
  connectedAt: number
}

type Listener = () => void

const listeners = new Set<Listener>()
let snapshot: ActiveConnection | null = null
let loaded = false

function getStorage(): Storage | null {
  try {
    if (typeof window !== 'undefined' && window.localStorage) return window.localStorage
  } catch {
    // Access can throw in private mode or a sandboxed iframe.
  }
  return null
}

function parse(raw: string | null): ActiveConnection | null {
  if (!raw) return null
  try {
    const value = JSON.parse(raw) as Partial<ActiveConnection> | null
    if (value && typeof value.id === 'string' && value.id) {
      return {
        id: value.id,
        nickname: typeof value.nickname === 'string' ? value.nickname : '',
        dsn: typeof value.dsn === 'string' ? value.dsn : '',
        connectedAt:
          typeof value.connectedAt === 'number' ? value.connectedAt : Date.now(),
      }
    }
  } catch {
    // Corrupt entry — treat it as no connection.
  }
  return null
}

function refresh(): void {
  snapshot = parse(getStorage()?.getItem(STORAGE_KEY) ?? null)
  loaded = true
}

function emit(): void {
  for (const listener of listeners) listener()
}

/** Read the persisted connection. Stable reference; safe for useSyncExternalStore. */
export function getActiveConnection(): ActiveConnection | null {
  if (!loaded) refresh()
  return snapshot
}

/** Remember a connection across route changes, reloads and tabs. */
export function setActiveConnection(connection: ActiveConnection): void {
  snapshot = connection
  loaded = true
  try {
    getStorage()?.setItem(STORAGE_KEY, JSON.stringify(connection))
  } catch {
    // Write can fail on quota or in private mode — the in-memory copy still works.
  }
  emit()
}

/** Forget the connection (e.g. the user disconnects). */
export function clearActiveConnection(): void {
  snapshot = null
  loaded = true
  try {
    getStorage()?.removeItem(STORAGE_KEY)
  } catch {
    // Ignore.
  }
  emit()
}

function subscribe(listener: Listener): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

if (typeof window !== 'undefined') {
  // Keep tabs in sync — a connection made in one tab should appear in another.
  window.addEventListener('storage', (event) => {
    if (event.key === STORAGE_KEY) {
      refresh()
      emit()
    }
  })
}

/** React binding that re-renders when the active connection changes. */
export function useActiveConnection(): ActiveConnection | null {
  return useSyncExternalStore(subscribe, getActiveConnection, () => null)
}
