// Session-local read cache. Never persist production data to browser storage.
export function createReportCache({ now = Date.now, limit = 128 } = {}) {
  const entries = new Map()
  const pending = new Map()
  const aborted = () => new DOMException('请求已取消', 'AbortError')

  function subscribe(promise, signal) {
    if (!signal) return promise
    if (signal.aborted) return Promise.reject(aborted())
    return new Promise((resolve, reject) => {
      const cancel = () => reject(aborted())
      signal.addEventListener('abort', cancel, { once: true })
      promise.then(resolve, reject).finally(() => signal.removeEventListener('abort', cancel))
    })
  }

  function invalidate(prefix = '') {
    for (const key of entries.keys()) if (key.startsWith(prefix)) entries.delete(key)
    for (const [key, item] of pending) {
      if (key.startsWith(prefix)) {
        pending.delete(key)
        item.controller.abort()
      }
    }
  }

  function read(key, loader, { ttl = 30000, force = false, signal } = {}) {
    if (signal?.aborted) return Promise.reject(aborted())
    const cached = entries.get(key)
    if (!force && cached && cached.expires > now()) return subscribe(Promise.resolve(cached.data), signal)
    if (cached) entries.delete(key)
    let item = pending.get(key)
    if (force && item && !item.force) {
      pending.delete(key)
      item.controller.abort()
      item = null
    }
    if (!item) {
      item = { controller: new AbortController(), force }
      pending.set(key, item)
      item.promise = Promise.resolve().then(() => {
        if (item.controller.signal.aborted) throw aborted()
        return loader(item.controller.signal)
      }).then((data) => {
        if (item.controller.signal.aborted || pending.get(key) !== item) throw aborted()
        entries.set(key, { data, expires: now() + ttl })
        while (entries.size > limit) entries.delete(entries.keys().next().value)
        return data
      }).finally(() => {
        if (pending.get(key) === item) pending.delete(key)
      })
    }
    // A page leaving cancels only its subscription, not the login prefetch.
    return subscribe(item.promise, signal)
  }

  return { read, invalidate, clear: () => invalidate() }
}

export const reportCache = createReportCache()
