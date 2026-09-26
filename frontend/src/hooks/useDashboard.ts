import { useCallback, useEffect, useState } from 'react'
import type { DashboardReport } from '@/types/dashboard'
import { fetchDashboard } from '@/api/dashboard'

type DashboardState = 'idle' | 'loading' | 'loaded' | 'error'

export interface UseDashboardResult {
  state: DashboardState
  report: DashboardReport | null
  load: (dsn: string) => void
}

export function useDashboard(dsn: string | null): UseDashboardResult {
  const [state, setState] = useState<DashboardState>('idle')
  const [report, setReport] = useState<DashboardReport | null>(null)

  const load = useCallback((dsnArg: string) => {
    setState('loading')
    setReport(null)
    fetchDashboard(dsnArg)
      .then((data) => {
        setReport(data)
        setState('loaded')
      })
      .catch(() => {
        setState('error')
      })
  }, [])

  useEffect(() => {
    if (dsn) {
      load(dsn)
    }
  }, [dsn, load])

  return { state, report, load }
}
