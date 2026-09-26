import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { friendlyError } from '@/lib/errors'

type InputMode = 'string' | 'fields'

interface IndividualFields {
  host: string
  port: string
  database: string
  username: string
  password: string
}

function buildConnectionString(fields: IndividualFields): string {
  const { host, port, database, username, password } = fields
  const encodedPassword = encodeURIComponent(password)
  const encodedUsername = encodeURIComponent(username)
  return `postgresql://${encodedUsername}:${encodedPassword}@${host}:${port}/${database}`
}

export default function WelcomePage() {
  const navigate = useNavigate()

  const [inputMode, setInputMode] = useState<InputMode>('string')
  const [connectionString, setConnectionString] = useState('')
  const [fields, setFields] = useState<IndividualFields>({
    host: 'localhost',
    port: '5432',
    database: '',
    username: '',
    password: '',
  })
  const [nickname, setNickname] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function getConnectionString(): string {
    if (inputMode === 'string') return connectionString.trim()
    return buildConnectionString(fields)
  }

  function updateField(key: keyof IndividualFields, value: string) {
    setFields((prev) => ({ ...prev, [key]: value }))
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)

    const dsn = getConnectionString()
    if (!dsn) {
      setError('Please enter a connection string.')
      return
    }

    setLoading(true)

    try {
      const response = await fetch('/api/connections/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ connection_string: dsn, nickname: nickname.trim() }),
      })

      if (!response.ok || !response.body) {
        setError(friendlyError(new Error(`HTTP ${response.status}`)))
        setLoading(false)
        return
      }

      // Read the SSE stream to find the final `result` event
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      outer: while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const raw = line.slice(6).trim()
          if (!raw) continue

          let parsed: { step: string; status: string; data: Record<string, unknown> }
          try {
            parsed = JSON.parse(raw)
          } catch {
            continue
          }

          if (parsed.step === 'result') {
            const data = parsed.data
            if (data.success && data.connection_id) {
              navigate(`/scan/${data.connection_id}`)
            } else {
              const raw = data.message as string | undefined
              if (raw) console.error('Connection test failed:', raw)
              setError(
                'We could not connect to that database. Check the host, port, database name, username, and password, then try again.',
              )
            }
            break outer
          }
        }
      }
    } catch (err) {
      setError(friendlyError(err, 'We could not test that connection. Please try again.'))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex min-h-[calc(100vh-3.5rem)] items-center justify-center px-4 py-12">
      <div className="w-full max-w-md">
        {/* Hero */}
        <div className="mb-8 text-center">
          <h1 className="text-5xl font-extrabold tracking-tight text-white">
            ⚡ SlowTrace
          </h1>
          <p className="mt-4 text-lg leading-relaxed text-slate-300">
            Find which line of your code is{' '}
            <span className="text-indigo-400 font-semibold">slowing down your database</span>.{' '}
            Fix it in one click.
          </p>
        </div>

        {/* Feature bullets */}
        <ul className="mb-8 flex flex-col items-center gap-2 text-sm text-slate-400">
          <li className="flex items-center gap-2">
            <span className="text-emerald-400 font-bold">✓</span>
            Works with PostgreSQL
          </li>
          <li className="flex items-center gap-2">
            <span className="text-emerald-400 font-bold">✓</span>
            No agent installation required
          </li>
        </ul>

        {/* Connection form card */}
        <div className="rounded-2xl border border-slate-700 bg-slate-800/60 p-6 shadow-xl backdrop-blur">
          {/* Toggle */}
          <div className="mb-5 flex rounded-lg bg-slate-900/70 p-1">
            <button
              type="button"
              onClick={() => setInputMode('string')}
              className={`flex-1 rounded-md py-1.5 text-sm font-medium transition-colors ${
                inputMode === 'string'
                  ? 'bg-indigo-600 text-white shadow'
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              Connection String
            </button>
            <button
              type="button"
              onClick={() => setInputMode('fields')}
              className={`flex-1 rounded-md py-1.5 text-sm font-medium transition-colors ${
                inputMode === 'fields'
                  ? 'bg-indigo-600 text-white shadow'
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              Individual Fields
            </button>
          </div>

          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
            {/* Connection String mode */}
            {inputMode === 'string' && (
              <div>
                <label className="mb-1.5 block text-xs font-medium text-slate-400">
                  Connection String
                </label>
                <textarea
                  rows={3}
                  value={connectionString}
                  onChange={(e) => setConnectionString(e.target.value)}
                  placeholder="postgresql://user:password@host:5432/dbname"
                  disabled={loading}
                  className="w-full resize-none rounded-lg border border-slate-600 bg-slate-900 px-3 py-2.5 font-mono text-sm text-white placeholder-slate-600 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 disabled:opacity-50"
                />
              </div>
            )}

            {/* Individual Fields mode */}
            {inputMode === 'fields' && (
              <div className="flex flex-col gap-3">
                <div className="grid grid-cols-3 gap-3">
                  <div className="col-span-2">
                    <label className="mb-1.5 block text-xs font-medium text-slate-400">
                      Host
                    </label>
                    <input
                      type="text"
                      value={fields.host}
                      onChange={(e) => updateField('host', e.target.value)}
                      placeholder="localhost"
                      disabled={loading}
                      className={inputCls}
                    />
                  </div>
                  <div>
                    <label className="mb-1.5 block text-xs font-medium text-slate-400">
                      Port
                    </label>
                    <input
                      type="text"
                      value={fields.port}
                      onChange={(e) => updateField('port', e.target.value)}
                      placeholder="5432"
                      disabled={loading}
                      className={inputCls}
                    />
                  </div>
                </div>
                <div>
                  <label className="mb-1.5 block text-xs font-medium text-slate-400">
                    Database
                  </label>
                  <input
                    type="text"
                    value={fields.database}
                    onChange={(e) => updateField('database', e.target.value)}
                    placeholder="mydb"
                    disabled={loading}
                    className={inputCls}
                  />
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="mb-1.5 block text-xs font-medium text-slate-400">
                      Username
                    </label>
                    <input
                      type="text"
                      value={fields.username}
                      onChange={(e) => updateField('username', e.target.value)}
                      placeholder="postgres"
                      disabled={loading}
                      className={inputCls}
                    />
                  </div>
                  <div>
                    <label className="mb-1.5 block text-xs font-medium text-slate-400">
                      Password
                    </label>
                    <input
                      type="password"
                      value={fields.password}
                      onChange={(e) => updateField('password', e.target.value)}
                      placeholder="••••••••"
                      disabled={loading}
                      className={inputCls}
                    />
                  </div>
                </div>
              </div>
            )}

            {/* Nickname — always visible */}
            <div>
              <label className="mb-1.5 block text-xs font-medium text-slate-400">
                Nickname <span className="text-slate-600">(optional)</span>
              </label>
              <input
                type="text"
                value={nickname}
                onChange={(e) => setNickname(e.target.value)}
                placeholder="e.g. Production DB"
                disabled={loading}
                className={inputCls}
              />
            </div>

            {/* Inline error */}
            {error && (
              <p className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2.5 text-sm text-red-300">
                {error}
              </p>
            )}

            {/* Submit */}
            <button
              type="submit"
              disabled={loading}
              className="mt-1 w-full rounded-lg bg-blue-600 py-2.5 text-sm font-semibold text-white shadow transition-colors hover:bg-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 focus:ring-offset-slate-800 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {loading ? (
                <span className="flex items-center justify-center gap-2">
                  <svg
                    className="h-4 w-4 animate-spin"
                    fill="none"
                    viewBox="0 0 24 24"
                  >
                    <circle
                      className="opacity-25"
                      cx="12"
                      cy="12"
                      r="10"
                      stroke="currentColor"
                      strokeWidth="4"
                    />
                    <path
                      className="opacity-75"
                      fill="currentColor"
                      d="M4 12a8 8 0 018-8v8H4z"
                    />
                  </svg>
                  Testing connection…
                </span>
              ) : (
                'Test & Connect'
              )}
            </button>

            {/* Trust badge */}
            <p className="flex items-center justify-center gap-1.5 text-xs text-slate-500">
              <svg
                className="h-3.5 w-3.5 shrink-0"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M12 17a2 2 0 100-4 2 2 0 000 4zm6-8V7a6 6 0 10-12 0v2M5 9h14a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V11a2 2 0 012-2z"
                />
              </svg>
              Credentials are encrypted
            </p>
          </form>
        </div>
      </div>
    </div>
  )
}

const inputCls =
  'w-full rounded-lg border border-slate-600 bg-slate-900 px-3 py-2.5 text-sm text-white placeholder-slate-600 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 disabled:opacity-50'
