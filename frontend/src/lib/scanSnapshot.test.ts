import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ScanSnapshot } from '@/types/scan'

const STORAGE_KEY = 'slowtrace.scan-snapshots'

function createLocalStorage(): Storage {
  const store = new Map<string, string>()
  return {
    get length() {
      return store.size
    },
    clear: () => store.clear(),
    getItem: (key: string) => store.get(key) ?? null,
    key: (index: number) => Array.from(store.keys())[index] ?? null,
    removeItem: (key: string) => {
      store.delete(key)
    },
    setItem: (key: string, value: string) => {
      store.set(key, String(value))
    },
  }
}

function snapshot(connectionId: string): ScanSnapshot {
  return {
    connectionId,
    connectedData: { version: 'PostgreSQL 15.3', table_count: 4 },
    healthChecks: [{ check: 'connections', status: 'ok' }],
    slowQuerySummary: { count: 2, total_wasted_minutes: 3.5, human_description: 'two slow queries' },
    completeData: {
      health_score: 88,
      critical_count: 0,
      warning_count: 1,
      healthy_count: 7,
      quick_wins: [],
    },
    updatedAt: 1000,
  }
}

/** Fresh module state, to simulate a page reload against the same storage. */
async function loadStore() {
  vi.resetModules()
  return import('@/lib/scanSnapshot')
}

describe('scanSnapshot store', () => {
  let localStorage: Storage

  beforeEach(() => {
    localStorage = createLocalStorage()
    vi.stubGlobal('window', { localStorage })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('returns null when nothing has been scanned', async () => {
    const { getScanSnapshot } = await loadStore()
    expect(getScanSnapshot('conn-1')).toBeNull()
  })

  it('saves and reads back a finished scan', async () => {
    const { getScanSnapshot, saveScanSnapshot } = await loadStore()
    const saved = snapshot('conn-1')

    saveScanSnapshot(saved)

    expect(getScanSnapshot('conn-1')).toEqual(saved)
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}')['conn-1']).toEqual(saved)
  })

  it('restores snapshots after a reload', async () => {
    const first = await loadStore()
    first.saveScanSnapshot(snapshot('conn-1'))

    const second = await loadStore()
    expect(second.getScanSnapshot('conn-1')?.completeData?.health_score).toBe(88)
  })

  it('keeps a separate snapshot per connection', async () => {
    const { getScanSnapshot, saveScanSnapshot } = await loadStore()
    saveScanSnapshot(snapshot('conn-1'))
    saveScanSnapshot({ ...snapshot('conn-2'), completeData: null })

    expect(getScanSnapshot('conn-1')?.completeData?.health_score).toBe(88)
    expect(getScanSnapshot('conn-2')?.completeData).toBeNull()
  })

  it('clears only the requested connection', async () => {
    const { getScanSnapshot, saveScanSnapshot, clearScanSnapshot } = await loadStore()
    saveScanSnapshot(snapshot('conn-1'))
    saveScanSnapshot(snapshot('conn-2'))

    clearScanSnapshot('conn-1')

    expect(getScanSnapshot('conn-1')).toBeNull()
    expect(getScanSnapshot('conn-2')).not.toBeNull()
  })

  it('ignores corrupt stored data', async () => {
    localStorage.setItem(STORAGE_KEY, '{not valid json')
    const { getScanSnapshot } = await loadStore()
    expect(getScanSnapshot('conn-1')).toBeNull()
  })

  it('returns null for an empty connection id', async () => {
    const { saveScanSnapshot, getScanSnapshot } = await loadStore()
    saveScanSnapshot(snapshot('conn-1'))
    expect(getScanSnapshot('')).toBeNull()
  })
})
