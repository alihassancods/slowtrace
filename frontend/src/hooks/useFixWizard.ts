import { useCallback, useEffect, useState } from 'react'
import type { FixReport } from '@/types/fix'
import { fetchFix } from '@/api/fix'

type FixState = 'idle' | 'loading' | 'loaded' | 'error'

export interface UseFixWizardResult {
  state: FixState
  report: FixReport | null
}

export function useFixWizard(
  dsn: string | null,
  queryid: string | null,
): UseFixWizardResult {
  const [state, setState] = useState<FixState>('idle')
  const [report, setReport] = useState<FixReport | null>(null)

  const load = useCallback((dsnArg: string, queryidArg: string) => {
    setState('loading')
    setReport(null)
    fetchFix(dsnArg, queryidArg)
      .then((data) => {
        setReport(data)
        setState('loaded')
      })
      .catch(() => {
        setState('error')
      })
  }, [])

  useEffect(() => {
    if (dsn && queryid) {
      load(dsn, queryid)
    }
  }, [dsn, queryid, load])

  return { state, report }
}
