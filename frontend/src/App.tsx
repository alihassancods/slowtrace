import { BrowserRouter, Routes, Route, NavLink, useNavigate } from 'react-router-dom'
import WelcomePage from '@/pages/WelcomePage'
import ScanPage from '@/pages/ScanPage'
import DashboardPage from '@/pages/DashboardPage'
import QueryDetailPage from '@/pages/QueryDetailPage'
import FixPage from '@/pages/FixPage'
import WizardPage from '@/pages/WizardPage'
import CodebasePage from '@/pages/CodebasePage'
import FixResultPage from '@/pages/FixResultPage'
import { clearActiveConnection, useActiveConnection } from '@/lib/activeConnection'

function Shell() {
  const navigate = useNavigate()
  const connection = useActiveConnection()
  const connectionId = connection?.id

  const navLinks = [
    { to: '/', label: 'Home', end: true },
    { to: connectionId ? `/scan/${connectionId}` : '/scan', label: 'Scan', end: false },
    {
      to: connectionId ? `/dashboard/${connectionId}` : '/dashboard',
      label: 'Dashboard',
      end: false,
    },
    { to: '/codebase', label: '🔍 Codebase', end: false },
    {
      to: connectionId ? `/wizard/${connectionId}` : '/wizard',
      label: 'Wizard',
      end: false,
    },
  ]

  function handleDisconnect() {
    clearActiveConnection()
    navigate('/')
  }

  return (
    <div className="min-h-screen bg-[#0f172a] text-white flex flex-col">
      {/* Top navigation bar */}
      <header className="border-b border-slate-700 bg-[#0f172a]">
        <div className="mx-auto max-w-7xl px-6 h-14 flex items-center gap-8">
          <span className="text-lg font-bold tracking-tight">⚡ SlowTrace</span>
          <nav className="flex gap-6 text-sm">
            {navLinks.map(({ to, label, end }) => (
              <NavLink
                key={label}
                to={to}
                end={end}
                className={({ isActive }) =>
                  isActive
                    ? 'text-white font-semibold'
                    : 'text-slate-400 hover:text-white transition-colors'
                }
              >
                {label}
              </NavLink>
            ))}
          </nav>

          {connection && (
            <div className="ml-auto flex items-center gap-3 text-xs">
              <span className="flex items-center gap-2 text-slate-400">
                <span className="h-2 w-2 rounded-full bg-emerald-400" />
                <span className="max-w-[12rem] truncate">
                  {connection.nickname || 'Connected'}
                </span>
              </span>
              <button
                onClick={handleDisconnect}
                className="rounded-md border border-slate-600 px-2.5 py-1 font-medium text-slate-300 transition-colors hover:border-slate-400 hover:text-white"
              >
                Disconnect
              </button>
            </div>
          )}
        </div>
      </header>

      {/* Main content */}
      <main className="flex-1 mx-auto w-full max-w-7xl">
        <Routes>
          <Route path="/" element={<WelcomePage />} />
          <Route path="/scan" element={<ScanPage />} />
          <Route path="/scan/:id" element={<ScanPage />} />
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/dashboard/:id" element={<DashboardPage />} />
          <Route path="/dashboard/query/:id" element={<QueryDetailPage />} />
          <Route path="/codebase" element={<CodebasePage />} />
          <Route path="/query/:connectionId/:queryid" element={<QueryDetailPage />} />
          <Route path="/fix/:id" element={<FixPage />} />
          <Route path="/fix/:connectionId/:queryid" element={<FixPage />} />
          <Route path="/wizard" element={<WizardPage />} />
          <Route path="/wizard/:id" element={<WizardPage />} />
          <Route path="/result/:fixId" element={<FixResultPage />} />
        </Routes>
      </main>
    </div>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <Shell />
    </BrowserRouter>
  )
}
