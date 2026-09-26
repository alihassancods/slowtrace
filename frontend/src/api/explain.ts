import type { QueryDetail } from '@/types/explain'

/** Legacy DSN-based endpoint (kept for backwards compatibility). */
export async function fetchQueryDetail(dsn: string, queryid: string): Promise<QueryDetail> {
  const resp = await fetch(`/api/explain/${encodeURIComponent(dsn)}/${queryid}`)
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status}`)
  }
  return resp.json() as Promise<QueryDetail>
}

/** New connection-id-based endpoint: GET /api/queries/{connectionId}/{queryid} */
export async function fetchQueryDetailById(
  connectionId: string,
  queryid: string,
): Promise<QueryDetail> {
  const resp = await fetch(`/api/queries/${connectionId}/${queryid}`)
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status}`)
  }
  return resp.json() as Promise<QueryDetail>
}
