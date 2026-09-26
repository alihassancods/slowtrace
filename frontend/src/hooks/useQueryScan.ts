import { useState, useCallback, useRef } from 'react'
import type { ScanStep, SlowQuery, QueryReport } from '@/types/queries'
import { streamQueries } from '@/api/queries'

export type ScanState = 'idle' | 'scanning' | 'done' | 'error'

export interface UseQueryScanResult {
  state: ScanState
  steps: ScanStep[]
  queries: SlowQuery[]
  start: (dsn: string) => void
  reset: () => void
}

export function useQueryScan(): UseQueryScanResult {
  const [state, setState] = useState<ScanState>('idle')
  const [steps, setSteps] = useState<ScanStep[]>([])
  const [queries, setQueries] = useState<SlowQuery[]>([])
  const esRef = useRef<EventSource | null>(null)

  const reset = useCallback(() => {
    esRef.current?.close()
    esRef.current = null
    setState('idle')
    setSteps([])
    setQueries([])
  }, [])

  const start = useCallback((dsn: string) => {
    esRef.current?.close()
    setSteps([])
    setQueries([])
    setState('scanning')

    const es = streamQueries(
      dsn,
      (step: ScanStep) => {
        setSteps((prev) => [...prev, step])
      },
      (report: QueryReport) => {
        setSteps((prev) => [
          ...prev,
          { step: 'result', status: report.errors.length > 0 ? 'warning' : 'ok', data: report as unknown as Record<string, unknown> },
        ])
        setQueries(report.queries)
        setState(report.errors.some((e) => e.step === 'connect') ? 'error' : 'done')
        esRef.current = null
      },
      () => {
        setState('error')
        esRef.current = null
      },
    )

    esRef.current = es
  }, [])

  return { state, steps, queries, start, reset }
}
