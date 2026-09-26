import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import ConnectionLostCard from '@/components/ConnectionLostCard'

// ---------------------------------------------------------------------------
// Types & data
// ---------------------------------------------------------------------------

type Framework = 'django' | 'fastapi' | 'express' | 'rails' | 'spring' | 'other'
type Step = 1 | 2 | 3

interface FrameworkMeta {
  id: Framework
  label: string
  emoji: string
}

const FRAMEWORKS: FrameworkMeta[] = [
  { id: 'django',   label: 'Django',     emoji: '🐍' },
  { id: 'fastapi',  label: 'FastAPI',    emoji: '⚡' },
  { id: 'express',  label: 'Express.js', emoji: '🟩' },
  { id: 'rails',    label: 'Rails',      emoji: '💎' },
  { id: 'spring',   label: 'Spring',     emoji: '🍃' },
  { id: 'other',    label: 'Other',      emoji: '🔧' },
]

// Each instruction is a list of blocks: 'shell' | 'code' | 'text'
type BlockType = 'shell' | 'code' | 'text'
interface Block { type: BlockType; content: string; label?: string }

const INSTRUCTIONS: Record<Framework, Block[]> = {
  django: [
    { type: 'text', content: 'Step 1 — Install the package' },
    { type: 'shell', content: 'pip install sqlcommenter' },
    { type: 'text', content: 'Step 2 — Add middleware to settings.py' },
    {
      type: 'code',
      content: `MIDDLEWARE = [\n    ...your existing middleware...\n    'google.cloud.sqlcommenter.django.middleware.SqlCommenter',\n]`,
    },
    { type: 'text', content: 'Step 3 — Redeploy your application' },
  ],
  fastapi: [
    { type: 'text', content: 'Step 1 — Install the package' },
    { type: 'shell', content: 'pip install sqlcommenter' },
    { type: 'text', content: 'Step 2 — Register the SQLAlchemy listener' },
    {
      type: 'code',
      content: `from sqlcommenter.sqlalchemy.executor import BeforeExecuteFactory\n\n@event.listens_for(engine, "before_cursor_execute", retval=True)\ndef before_cursor_execute(conn, cursor, statement,\n        parameters, context, executemany):\n    return BeforeExecuteFactory()(statement, parameters,\n                                  context, executemany)`,
    },
    { type: 'text', content: 'Step 3 — Redeploy your application' },
  ],
  express: [
    { type: 'text', content: 'Step 1 — Install the package' },
    { type: 'shell', content: 'npm install @google-cloud/sqlcommenter-sequelize' },
    { type: 'text', content: 'Step 2 — Wrap your Sequelize instance' },
    {
      type: 'code',
      content: `const {wrapSequelize} = require('@google-cloud/sqlcommenter-sequelize')\nwrapSequelize(sequelize)`,
    },
    { type: 'text', content: 'Step 3 — Redeploy your application' },
  ],
  rails: [
    { type: 'text', content: 'Step 1 — Add the gem to your Gemfile' },
    { type: 'shell', content: "gem 'marginalia'" },
    { type: 'text', content: 'Step 2 — Add an initializer' },
    {
      type: 'code',
      content: `# config/initializers/marginalia.rb\nMarginalia::Comment.components = [:controller, :action, :line]`,
    },
    { type: 'text', content: 'Step 3 — Run bundle install and redeploy' },
  ],
  spring: [
    { type: 'text', content: 'Step 1 — Add the dependency (Maven)' },
    {
      type: 'code',
      content: `<dependency>\n  <groupId>com.google.cloud</groupId>\n  <artifactId>sqlcommenter-spring-starter</artifactId>\n  <version>1.0.0</version>\n</dependency>`,
      label: 'pom.xml',
    },
    { type: 'text', content: 'Step 2 — Enable the auto-configuration' },
    {
      type: 'code',
      content: `# application.properties\nspring.sqlcommenter.enabled=true`,
    },
    { type: 'text', content: 'Step 3 — Redeploy your application' },
  ],
  other: [
    { type: 'text', content: 'SQLCommenter supports many ORMs and frameworks.' },
    {
      type: 'text',
      content:
        'Visit the SQLCommenter docs for your specific stack: https://google.github.io/sqlcommenter/',
    },
    {
      type: 'text',
      content:
        'Alternatively, add a /* file=\'yourfile.py\',line=\'42\' */ comment to your SQL queries manually.',
    },
    { type: 'code', content: `/* file='app/orders.py',line='42',action='index' */` },
  ],
}

// ---------------------------------------------------------------------------
// Copy button
// ---------------------------------------------------------------------------

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  function handleCopy() {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      if (timerRef.current) clearTimeout(timerRef.current)
      timerRef.current = setTimeout(() => setCopied(false), 2000)
    })
  }

  return (
    <button
      onClick={handleCopy}
      className={`shrink-0 rounded-md px-2.5 py-1 text-xs font-semibold transition-all ${
        copied
          ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'
          : 'bg-slate-700 text-slate-300 border border-slate-600 hover:bg-slate-600'
      }`}
    >
      {copied ? 'Copied!' : 'Copy'}
    </button>
  )
}

// ---------------------------------------------------------------------------
// Code block with copy
// ---------------------------------------------------------------------------

function CodeBlock({ block }: { block: Block }) {
  const isShell = block.type === 'shell'
  return (
    <div className="rounded-xl border border-slate-700 bg-slate-950 overflow-hidden">
      {/* Title bar */}
      <div className="flex items-center justify-between gap-3 border-b border-slate-700/60 bg-slate-900 px-4 py-2">
        <span className="text-xs font-medium text-slate-400">
          {block.label ?? (isShell ? 'Terminal' : 'Code')}
        </span>
        <CopyButton text={block.content} />
      </div>
      <pre
        className={`overflow-x-auto px-4 py-3 text-xs leading-relaxed font-mono whitespace-pre ${
          isShell ? 'text-emerald-300' : 'text-slate-200'
        }`}
      >
        {isShell && <span className="select-none text-slate-600 mr-2">$</span>}
        {block.content}
      </pre>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Spinner
// ---------------------------------------------------------------------------

function Spinner({ size = 'md' }: { size?: 'sm' | 'md' | 'lg' }) {
  const sz = size === 'sm' ? 'h-4 w-4' : size === 'lg' ? 'h-8 w-8' : 'h-5 w-5'
  return (
    <svg className={`${sz} animate-spin text-indigo-400`} viewBox="0 0 24 24" fill="none">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
    </svg>
  )
}

// ---------------------------------------------------------------------------
// Step indicators
// ---------------------------------------------------------------------------

function StepDots({ current }: { current: Step }) {
  return (
    <div className="flex items-center gap-2 mb-8">
      {([1, 2, 3] as Step[]).map((n) => (
        <div
          key={n}
          className={`h-2 rounded-full transition-all duration-300 ${
            n === current
              ? 'w-6 bg-indigo-500'
              : n < current
                ? 'w-2 bg-indigo-400/60'
                : 'w-2 bg-slate-700'
          }`}
        />
      ))}
      <span className="ml-1 text-xs text-slate-500">Step {current} of 3</span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Step 1 — Framework selection
// ---------------------------------------------------------------------------

function Step1({ onSelect }: { onSelect: (fw: Framework) => void }) {
  return (
    <div>
      <h1 className="text-3xl font-extrabold text-white mb-2">🔗 Enable Code Tracing</h1>
      <p className="text-slate-400 mb-8 leading-relaxed">
        See exactly which line of your code causes each slow query.
      </p>

      <p className="mb-4 text-sm font-medium text-slate-300">
        Which framework does your app use?
      </p>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        {FRAMEWORKS.map((fw) => (
          <button
            key={fw.id}
            onClick={() => onSelect(fw.id)}
            className="flex flex-col items-center gap-2 rounded-xl border border-slate-700 bg-slate-800/60 px-4 py-5 transition-all hover:border-indigo-500/60 hover:bg-slate-700/60 hover:scale-[1.02] active:scale-100 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 focus:ring-offset-slate-900"
          >
            <span className="text-2xl leading-none">{fw.emoji}</span>
            <span className="text-sm font-semibold text-slate-200">{fw.label}</span>
          </button>
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Step 2 — Instructions
// ---------------------------------------------------------------------------

function Step2({
  framework,
  onDone,
  onBack,
}: {
  framework: Framework
  onDone: () => void
  onBack: () => void
}) {
  const fw = FRAMEWORKS.find((f) => f.id === framework)!
  const blocks = INSTRUCTIONS[framework]

  return (
    <div>
      <button
        onClick={onBack}
        className="mb-6 inline-flex items-center gap-1 text-sm text-indigo-400 hover:text-indigo-300 transition-colors"
      >
        ← Back
      </button>

      <h1 className="text-2xl font-extrabold text-white mb-1">
        {fw.emoji} {fw.label} Setup — 2 steps
      </h1>
      <p className="text-slate-400 mb-6 text-sm">
        Follow the steps below, then click "Check Status" to verify.
      </p>

      <div className="space-y-4">
        {blocks.map((block, i) => {
          if (block.type === 'text') {
            return (
              <p key={i} className="text-sm text-slate-300 font-medium">
                {block.content}
              </p>
            )
          }
          return <CodeBlock key={i} block={block} />
        })}
      </div>

      <div className="mt-8">
        <button
          onClick={onDone}
          className="w-full rounded-xl bg-indigo-600 py-3 text-sm font-bold text-white shadow transition-colors hover:bg-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 focus:ring-offset-slate-900"
        >
          ✅ I've done this — Check Status
        </button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Step 3 — Status check
// ---------------------------------------------------------------------------

const POLL_INTERVAL_MS = 3_000
const TIMEOUT_MS = 60_000

function Step3({
  connectionId,
  framework,
  onBack,
  onReconnect,
}: {
  connectionId: string
  framework: Framework
  onBack: () => void
  onReconnect: () => void
}) {
  type PollState = 'polling' | 'active' | 'timeout' | 'error'
  const [pollState, setPollState] = useState<PollState>('polling')
  const startedAt = useRef(Date.now())
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const failuresRef = useRef(0)
  const fw = FRAMEWORKS.find((f) => f.id === framework)!

  async function checkStatus() {
    // If no connectionId, can't poll — show a friendly message instead of erroring
    if (!connectionId) return

    try {
      const resp = await fetch(`/api/wizard/status/${connectionId}`)
      if (resp.ok) {
        const data = (await resp.json()) as { active: boolean }
        failuresRef.current = 0
        if (data.active) {
          clearInterval(intervalRef.current!)
          setPollState('active')
          return
        }
      } else if (resp.status >= 500) {
        failuresRef.current += 1
      }
    } catch (err: unknown) {
      // Transient failures are expected while the app redeploys — only give up
      // after several in a row.
      console.error('Wizard status check failed:', err)
      failuresRef.current += 1
    }

    if (failuresRef.current >= 3) {
      clearInterval(intervalRef.current!)
      setPollState('error')
      return
    }

    if (Date.now() - startedAt.current >= TIMEOUT_MS) {
      clearInterval(intervalRef.current!)
      setPollState('timeout')
    }
  }

  useEffect(() => {
    setPollState('polling')
    startedAt.current = Date.now()

    // First check immediately, then every POLL_INTERVAL_MS
    checkStatus()
    intervalRef.current = setInterval(checkStatus, POLL_INTERVAL_MS)

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connectionId])

  function handleRetry() {
    setPollState('polling')
    startedAt.current = Date.now()
    failuresRef.current = 0
    checkStatus()
    intervalRef.current = setInterval(checkStatus, POLL_INTERVAL_MS)
  }

  // ── No connectionId ───────────────────────────────────────────────────────
  if (!connectionId) {
    return (
      <div className="text-center py-8">
        <p className="text-slate-400 text-sm mb-4">
          No database connection found. Connect from the home page first.
        </p>
        <Link
          to="/"
          className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-500 transition-colors"
        >
          Connect a Database
        </Link>
      </div>
    )
  }

  // ── Active ────────────────────────────────────────────────────────────────
  if (pollState === 'active') {
    return (
      <div className="text-center py-4">
        <div className="mb-4 text-5xl">🎉</div>
        <h2 className="text-xl font-extrabold text-emerald-400 mb-2">
          ✅ Code Tracing is Active!
        </h2>
        <p className="text-sm text-slate-300 mb-1">
          SlowTrace can now trace every slow query back to your source code.
        </p>
        <p className="text-xs text-slate-500 mb-8">
          {fw.emoji} {fw.label} SQLCommenter tags detected in {' '}
          <span className="font-mono text-indigo-400">pg_stat_statements</span>.
        </p>
        <Link
          to={connectionId ? `/dashboard/${connectionId}` : '/dashboard'}
          className="inline-flex items-center gap-2 rounded-xl bg-indigo-600 px-6 py-3 text-sm font-bold text-white shadow-lg transition-colors hover:bg-indigo-500"
        >
          View Your Slow Queries →
        </Link>
      </div>
    )
  }

  // ── Connection lost / server error ────────────────────────────────────────
  if (pollState === 'error') {
    return (
      <div className="space-y-4">
        <ConnectionLostCard onReconnect={onReconnect} />
        <div className="flex flex-col items-center gap-3">
          <button
            onClick={handleRetry}
            className="rounded-xl bg-indigo-600 px-6 py-2.5 text-sm font-bold text-white transition-colors hover:bg-indigo-500"
          >
            Try Again
          </button>
          <button
            onClick={onBack}
            className="text-sm text-slate-500 transition-colors hover:text-slate-300"
          >
            ← Back to instructions
          </button>
        </div>
      </div>
    )
  }

  // ── Timeout ───────────────────────────────────────────────────────────────
  if (pollState === 'timeout') {
    return (
      <div className="text-center py-4">
        <div className="mb-4 text-4xl">⏱</div>
        <h2 className="text-lg font-bold text-yellow-400 mb-2">
          Taking longer than expected…
        </h2>
        <p className="text-sm text-slate-300 mb-1">
          Make sure you redeployed your application after adding SQLCommenter.
        </p>
        <p className="text-xs text-slate-500 mb-6">
          New queries need to run before tags appear in pg_stat_statements.
        </p>
        <div className="flex flex-col items-center gap-3">
          <button
            onClick={handleRetry}
            className="rounded-xl bg-indigo-600 px-6 py-2.5 text-sm font-bold text-white hover:bg-indigo-500 transition-colors"
          >
            Try Again
          </button>
          <button
            onClick={onBack}
            className="text-sm text-slate-500 hover:text-slate-300 transition-colors"
          >
            ← Back to instructions
          </button>
        </div>
      </div>
    )
  }

  // ── Polling ───────────────────────────────────────────────────────────────
  return (
    <div className="text-center py-8">
      <div className="flex justify-center mb-5">
        <Spinner size="lg" />
      </div>
      <h2 className="text-base font-semibold text-slate-200 mb-1">
        Checking for SQLCommenter tags…
      </h2>
      <p className="text-xs text-slate-500 mb-6">
        Querying <span className="font-mono text-indigo-400">pg_stat_statements</span> every 3
        seconds
      </p>
      <button
        onClick={onBack}
        className="text-sm text-slate-500 hover:text-slate-300 transition-colors"
      >
        ← Back to instructions
      </button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function WizardPage() {
  const { id: connectionId = '' } = useParams<{ id?: string }>()
  const navigate = useNavigate()

  const [step, setStep] = useState<Step>(1)
  const [framework, setFramework] = useState<Framework | null>(null)

  function handleSelectFramework(fw: Framework) {
    setFramework(fw)
    setStep(2)
  }

  function handleDone() {
    setStep(3)
  }

  function handleBack() {
    if (step === 3) {
      setStep(2)
    } else {
      setStep(1)
      setFramework(null)
    }
  }

  return (
    <div className="mx-auto max-w-2xl px-4 py-12">
      <Link
        to={connectionId ? `/dashboard/${connectionId}` : '/'}
        className="mb-4 inline-flex items-center gap-1 text-sm text-indigo-400 transition-colors hover:text-indigo-300"
      >
        ← Back to {connectionId ? 'Dashboard' : 'Home'}
      </Link>

      <StepDots current={step} />

      {step === 1 && <Step1 onSelect={handleSelectFramework} />}

      {step === 2 && framework && (
        <Step2 framework={framework} onDone={handleDone} onBack={handleBack} />
      )}

      {step === 3 && framework && (
        <>
          <h1 className="text-2xl font-extrabold text-white mb-6">Status Check</h1>
          <Step3
            connectionId={connectionId}
            framework={framework}
            onBack={() => setStep(2)}
            onReconnect={() => navigate('/')}
          />
        </>
      )}
    </div>
  )
}
