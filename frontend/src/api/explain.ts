import type { QueryDetail } from '@/types/explain'

export async function fetchQueryDetail(dsn: string, queryid: string): Promise<QueryDetail> {
  const resp = await fetch(`/api/explain/${encodeURIComponent(dsn)}/${queryid}`)
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status}`)
  }
  return resp.json() as Promise<QueryDetail>
}
