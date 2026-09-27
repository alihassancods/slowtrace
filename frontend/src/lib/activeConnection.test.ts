import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const STORAGE_KEY = 'slowtrace.active-connection'

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

/** Fresh module state, to simulate a page reload against the same storage. */
async function loadStore() {
  vi.resetModules()
  return import('@/lib/activeConnection')
}

describe('activeConnection store', () => {
  let localStorage: Storage

  beforeEach(() => {
    localStorage = createLocalStorage()
    vi.stubGlobal('window', {
      localStorage,
      addEventListener: () => {},
      removeEventListener: () => {},
    })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('starts with no connection when storage is empty', async () => {
    const { getActiveConnection } = await loadStore()
    expect(getActiveConnection()).toBeNull()
  })

  it('persists an active connection and reads it back', async () => {
    const { getActiveConnection, setActiveConnection } = await loadStore()
    const connection = {
      id: 'conn-1',
      nickname: 'Production DB',
      dsn: 'postgresql://user:pw@host:5432/db',
      connectedAt: 123,
    }

    setActiveConnection(connection)

    expect(getActiveConnection()).toEqual(connection)
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? 'null')).toEqual(connection)
  })

  it('restores the connection after a reload', async () => {
    const first = await loadStore()
    first.setActiveConnection({
      id: 'conn-1',
      nickname: 'Production DB',
      dsn: 'postgresql://host/db',
      connectedAt: 123,
    })

    const second = await loadStore()
    expect(second.getActiveConnection()?.id).toBe('conn-1')
    expect(second.getActiveConnection()?.nickname).toBe('Production DB')
  })

  it('clears the persisted connection', async () => {
    const { getActiveConnection, setActiveConnection, clearActiveConnection } =
      await loadStore()
    setActiveConnection({ id: 'conn-1', nickname: '', dsn: '', connectedAt: 1 })

    clearActiveConnection()

    expect(getActiveConnection()).toBeNull()
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull()
  })

  it('ignores corrupt stored data', async () => {
    localStorage.setItem(STORAGE_KEY, '{not valid json')
    const { getActiveConnection } = await loadStore()
    expect(getActiveConnection()).toBeNull()
  })

  it('ignores stored entries without an id', async () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ nickname: 'no id here' }))
    const { getActiveConnection } = await loadStore()
    expect(getActiveConnection()).toBeNull()
  })
})
