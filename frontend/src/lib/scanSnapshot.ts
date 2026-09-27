/**
 * Persisted scan results, keyed by connection id and stored in localStorage.
 *
 * The scan page reads from here on mount so revisiting a scanned database shows
 * the previous results instead of re-running the scan. A scan is only run when
 * the user explicitly asks for one.
 */

import type { ScanSnapshot } from '@/types/scan'

const STORAGE_KEY = 'slowtrace.scan-snapshots'

type SnapshotMap = Record<string, ScanSnapshot>

let cache: SnapshotMap | null = null

function getStorage(): Storage | null {
  try {
    if (typeof window !== 'undefined' && window.localStorage) return window.localStorage
  } catch {
    // Access can throw in private mode or a sandboxed iframe.
  }
  return null
}

function parse(raw: string | null): SnapshotMap {
  if (!raw) return {}
  try {
    const value = JSON.parse(raw) as unknown
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      return value as SnapshotMap
    }
  } catch {
    // Corrupt entry — start over rather than crash the page.
  }
  return {}
}

function load(): SnapshotMap {
  if (!cache) cache = parse(getStorage()?.getItem(STORAGE_KEY) ?? null)
  return cache
}

function write(map: SnapshotMap): void {
  cache = map
  try {
    getStorage()?.setItem(STORAGE_KEY, JSON.stringify(map))
  } catch {
    // Write can fail on quota or in private mode — the in-memory copy still works.
  }
}

/** The last persisted scan for a connection, or null if it was never scanned. */
export function getScanSnapshot(connectionId: string): ScanSnapshot | null {
  if (!connectionId) return null
  return load()[connectionId] ?? null
}

/** Remember a finished scan so it can be shown again without re-fetching. */
export function saveScanSnapshot(snapshot: ScanSnapshot): void {
  write({ ...load(), [snapshot.connectionId]: snapshot })
}

/** Drop a connection's scan result (e.g. it is no longer reachable). */
export function clearScanSnapshot(connectionId: string): void {
  const map = { ...load() }
  delete map[connectionId]
  write(map)
}
