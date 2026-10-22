import { getDeviceId } from './deviceContext.js'
// Session-local read cache. Never persist production data to browser storage.
export function createReportCache({ now = Date.now, limit = 128, scope = () => '' } = {}) {
  const entries = new Map()
  const pending = new Map()
  const aborted = () => new DOMException('请求已取消', 'AbortError')

  function subscribe(key, item, signal) {
    if (signal?.aborted) return Promise.reject(aborted())
    item.subscribers += 1
    return new Promise((resolve, reject) => {
      let released = false
      const release = () => {
        if (released) return
        released = true
        item.subscribers -= 1
        if (!item.settled && item.subscribers === 0 && pending.get(key) === item) {
          pending.delete(key)
          item.controller.abort()
        }
      }
      const cancel = () => {
        release()
        reject(aborted())
      }
      signal?.addEventListener('abort', cancel, { once: true })
      item.promise.then(resolve, reject).finally(() => {
        signal?.removeEventListener('abort', cancel)
        release()
      })
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
    key = `${key}|device:${scope()}`
    if (signal?.aborted) return Promise.reject(aborted())
    const cached = entries.get(key)
    if (!force && cached && cached.expires > now()) {
      if (signal?.aborted) return Promise.reject(aborted())
      return Promise.resolve(cached.data)
    }
    if (cached) entries.delete(key)
    let item = pending.get(key)
    if (force && item && !item.force) {
      pending.delete(key)
      item.controller.abort()
      item = null
    }
    if (!item) {
      item = { controller: new AbortController(), force, settled: false, subscribers: 0 }
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
        item.settled = true
        if (pending.get(key) === item) pending.delete(key)
      })
    }
    // Keep a shared request only while at least one page or login preload needs it.
    return subscribe(key, item, signal)
  }

  return { read, invalidate, clear: () => invalidate() }
}

export const reportCache = createReportCache({ scope: getDeviceId })
