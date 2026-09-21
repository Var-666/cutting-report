let handler = null
let handling = null

export function setSessionExpiredHandler(nextHandler) {
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
