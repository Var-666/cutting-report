let handler: (() => void | Promise<void>) | null = null
let handling: Promise<void> | null = null

export function setSessionExpiredHandler(nextHandler: (() => void | Promise<void>) | null) {
  const previous = handler
  handler = typeof nextHandler === 'function' ? nextHandler : null
  return () => {
    if (handler === nextHandler) handler = previous
  }
}

export function notifySessionExpired() {
  if (!handler) return Promise.resolve()
  if (!handling) {
    const currentHandler = handler
    handling = Promise.resolve()
      .then(() => currentHandler())
      .finally(() => { handling = null })
  }
  return handling
}
