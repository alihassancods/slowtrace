import { BrowserRouter, Routes, Route, NavLink } from 'react-router-dom'
import WelcomePage from '@/pages/WelcomePage'
import ScanPage from '@/pages/ScanPage'
import DashboardPage from '@/pages/DashboardPage'
import QueryDetailPage from '@/pages/QueryDetailPage'
import FixPage from '@/pages/FixPage'
import WizardPage from '@/pages/WizardPage'

const navLinks = [
  { to: '/', label: 'Home', end: true },
  { to: '/scan', label: 'Scan', end: false },
  { to: '/dashboard', label: 'Dashboard', end: false },
  { to: '/wizard', label: 'Wizard', end: false },
]

export default function App() {
  return (
    <BrowserRouter>
      <div className="min-h-screen bg-[#0f172a] text-white flex flex-col">
        {/* Top navigation bar */}
        <header className="border-b border-slate-700 bg-[#0f172a]">
          <div className="mx-auto max-w-7xl px-6 h-14 flex items-center gap-8">
            <span className="text-lg font-bold tracking-tight">⚡ SlowTrace</span>
            <nav className="flex gap-6 text-sm">
              {navLinks.map(({ to, label, end }) => (
                <NavLink
                  key={to}
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
          </div>
        </header>

        {/* Main content */}
        <main className="flex-1 mx-auto w-full max-w-7xl">
          <Routes>
            <Route path="/" element={<WelcomePage />} />
            <Route path="/scan" element={<ScanPage />} />
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/dashboard/query/:id" element={<QueryDetailPage />} />
            <Route path="/fix/:id" element={<FixPage />} />
            <Route path="/wizard" element={<WizardPage />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  )
}
