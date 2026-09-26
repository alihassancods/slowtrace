import type { DashboardReport } from '@/types/dashboard'

export async function fetchDashboard(dsn: string): Promise<DashboardReport> {
  const resp = await fetch(`/api/dashboard/${encodeURIComponent(dsn)}`)
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status}`)
  }
  return resp.json() as Promise<DashboardReport>
}
