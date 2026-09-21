function fallbackRequestId() {
  return `${Date.now().toString(36)}_${Math.random().toString(36).slice(2)}`
}

export function createRequestCancellation(
  signal,
  {
    fetchImpl = globalThis.fetch,
    createId = () => globalThis.crypto?.randomUUID?.() || fallbackRequestId(),
  } = {},
) {
  if (!signal) {
    return { headers: {}, dispose() {} }
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
