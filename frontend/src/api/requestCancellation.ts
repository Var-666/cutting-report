function randomRequestId() {
  // getRandomValues is available on the HTTP intranet.
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('')
}

export function createRequestCancellation(
  signal?: AbortSignal | null,
  {
    fetchImpl = globalThis.fetch,
    createId = randomRequestId,
  }: { fetchImpl?: typeof fetch; createId?: () => string } = {},
) {
  if (!signal) {
    return { headers: {} as Record<string, string>, dispose() {} }
  }

  const requestId = `web_${createId()}`.replace(/[^A-Za-z0-9._:-]/g, '')
  let cancellationSent = false

  function cancelServerRequest() {
    if (cancellationSent) return
    cancellationSent = true
    try {
      Promise.resolve(
        fetchImpl(`/api/v1/requests/${encodeURIComponent(requestId)}/cancel`, {
          method: 'POST',
          credentials: 'same-origin',
          keepalive: true,
          headers: { 'Content-Type': 'application/json' },
        }),
      ).catch(() => {})
    } catch {
      // The original request is already cancelled. Server-side cancellation is
      // deliberately best-effort and must not create another visible failure.
    }
  }

  signal.addEventListener('abort', cancelServerRequest, { once: true })
  if (signal.aborted) cancelServerRequest()

  return {
    headers: { 'X-Request-ID': requestId },
    requestId,
    dispose() {
      signal.removeEventListener('abort', cancelServerRequest)
    },
  }
}
