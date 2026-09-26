import type { FixReport, RollbackResponse } from '@/types/fix'

export async function fetchFix(dsn: string, queryid: string): Promise<FixReport> {
  const resp = await fetch(`/api/fix/${encodeURIComponent(dsn)}/${queryid}`)
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status}`)
  }
  return resp.json() as Promise<FixReport>
}

export async function rollbackFix(
  fixId: string,
  connectionId: string,
): Promise<RollbackResponse> {
  const resp = await fetch('/api/fixes/rollback', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ fix_id: fixId, connection_id: connectionId }),
  })
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({})) as { detail?: string }
    throw new Error(detail.detail ?? `HTTP ${resp.status}`)
  }
  return resp.json() as Promise<RollbackResponse>
}
