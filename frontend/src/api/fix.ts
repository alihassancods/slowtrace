import type { FixReport } from '@/types/fix'

export async function fetchFix(dsn: string, queryid: string): Promise<FixReport> {
  const resp = await fetch(`/api/fix/${encodeURIComponent(dsn)}/${queryid}`)
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status}`)
  }
  return resp.json() as Promise<FixReport>
}
