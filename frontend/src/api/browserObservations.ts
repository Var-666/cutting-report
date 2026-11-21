import type { BrowserIssue } from './generated/types.gen'

let observing = false
const lastSent = new Map<BrowserIssue['kind'], number>()
const backendId = /^(?:web|req)_[A-Za-z0-9_-]{1,72}$/
const important = /^\/api\/v1\/(?:reports|analysis|welding|automation|cutting|workshop|leadership-report|equipment-report|energy-report)(?:[/?]|$)/

function submit(kind: BrowserIssue['kind'], requestId?: string) {
  if (!observing) return
  const now = Date.now()
  if (now - (lastSent.get(kind) ?? -Infinity) < 60_000) return
  lastSent.set(kind, now)
  const body: BrowserIssue = { kind }
  if (requestId && backendId.test(requestId)) body.request_id = requestId
  // Use fetch directly so observability never calls itself or triggers session handling.
  void fetch('/api/v1/operations/browser', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin', body: JSON.stringify(body), keepalive: true,
  }).catch(() => undefined)
}

export function reportApiFailure(url: string, status: number, requestId?: string) {
  if (status >= 500 && important.test(url)) submit('api', requestId)
}

function reportPromiseRejection(error: unknown) {
  if (!(error instanceof Error && (error.name === 'AbortError' || 'status' in error))) submit('promise')
}

export function reportAsyncError(error: unknown) {
  reportPromiseRejection(error)
  console.error(error)
}

export function observeBrowserErrors() {
  observing = true
  const error = () => submit('javascript')
  const rejection = (event: PromiseRejectionEvent) => reportPromiseRejection(event.reason)
  window.addEventListener('error', error)
  window.addEventListener('unhandledrejection', rejection)
  return {
    vue: () => submit('vue'),
    stop() {
      observing = false
      lastSent.clear()
      window.removeEventListener('error', error)
      window.removeEventListener('unhandledrejection', rejection)
    },
  }
}
