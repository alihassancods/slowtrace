import { useCallback, useEffect, useState } from 'react'
import type { QueryDetail } from '@/types/explain'
import { fetchQueryDetail } from '@/api/explain'

type DetailState = 'idle' | 'loading' | 'loaded' | 'error'

export interface UseQueryDetailResult {
  state: DetailState
  detail: QueryDetail | null
  load: (dsn: string, queryid: string) => void
}

export function useQueryDetail(
  dsn: string | null,
  queryid: string | null,
): UseQueryDetailResult {
  const [state, setState] = useState<DetailState>('idle')
  const [detail, setDetail] = useState<QueryDetail | null>(null)

  const load = useCallback((dsnArg: string, queryidArg: string) => {
    setState('loading')
    setDetail(null)
    fetchQueryDetail(dsnArg, queryidArg)
      .then((data) => {
        setDetail(data)
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

  return { state, detail, load }
}
