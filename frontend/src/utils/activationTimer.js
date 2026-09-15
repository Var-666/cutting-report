export function createActivationTimer({
  setTimer = (callback, delay) => globalThis.setTimeout(callback, delay),
  clearTimer = (timer) => globalThis.clearTimeout(timer),
} = {}) {
  let active = true
  let timer = null

  function cancel() {
    if (timer !== null) clearTimer(timer)
    timer = null
  }

  function schedule(callback, delay) {
    cancel()
    if (!active) return
    timer = setTimer(() => {
      timer = null
      if (active) callback()
    }, delay)
  }

  function activate() {
    active = true
  }

  function deactivate() {
    active = false
    cancel()
  }

  return {
    activate,
    cancel,
    deactivate,
    isActive: () => active,
    isPending: () => timer !== null,
    schedule,
  }
}

export function createActivationBinding(attach, detach) {
  let attached = false

  function activate() {
    if (attached) return
    attached = true
    attach()
  }

  function deactivate() {
    if (!attached) return
    attached = false
    detach()
  }

  return { activate, deactivate, isActive: () => attached }
}
