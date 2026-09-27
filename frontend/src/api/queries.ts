import type { ScanStep, QueryReport } from '@/types/queries'

export function streamQueries(
  dsn: string,
  onEvent: (step: ScanStep) => void,
  onDone: (report: QueryReport) => void,
  onError: (err: Event) => void,
): EventSource {
  const encoded = encodeURIComponent(dsn)
  const es = new EventSource(`/api/queries/${encoded}/stream`)

  es.onmessage = (e: MessageEvent) => {
    const step: ScanStep = JSON.parse(e.data)
    if (step.step === 'result') {
      onDone(step.data as unknown as QueryReport)
      es.close()
    } else {
      onEvent(step)
    }
  }

  es.onerror = (e: Event) => {
    es.close()
    onError(e)
  }

  return es
}
