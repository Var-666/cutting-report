import { getDeviceId } from './deviceContext.ts'
// Session-local read cache. Never persist production data to browser storage.
interface PendingRead { controller: AbortController; force: boolean; settled: boolean; subscribers: number; promise: Promise<unknown> }
interface CacheOptions { ttl?: number; force?: boolean; signal?: AbortSignal }

export function createReportCache({ now = Date.now, limit = 128, scope = () => '' }: { now?: () => number; limit?: number; scope?: () => string } = {}) {
  const entries = new Map<string, { data: unknown; expires: number }>()
  const pending = new Map<string, PendingRead>()
  const aborted = () => new DOMException('请求已取消', 'AbortError')

  function subscribe(key: string, item: PendingRead, signal?: AbortSignal) {
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

  function read<T>(key: string, loader: (signal: AbortSignal) => Promise<T>, { ttl = 30000, force = false, signal }: CacheOptions = {}): Promise<T> {
    key = `${key}|device:${scope()}`
    if (signal?.aborted) return Promise.reject(aborted())
    const cached = entries.get(key)
    if (!force && cached && cached.expires > now()) {
      if (signal?.aborted) return Promise.reject(aborted())
      return Promise.resolve(cached.data as T)
    }
    if (cached) entries.delete(key)
    let item = pending.get(key)
    if (force && item && !item.force) {
      pending.delete(key)
      item.controller.abort()
      item = undefined
    }
    if (!item) {
      item = { controller: new AbortController(), force, settled: false, subscribers: 0, promise: Promise.resolve() }
      pending.set(key, item)
      const current = item
      item.promise = Promise.resolve().then(() => {
        if (current.controller.signal.aborted) throw aborted()
        return loader(current.controller.signal)
      }).then((data) => {
        if (current.controller.signal.aborted || pending.get(key) !== current) throw aborted()
        entries.set(key, { data, expires: now() + ttl })
        while (entries.size > limit) entries.delete(entries.keys().next().value as string)
        return data
      }).finally(() => {
        current.settled = true
        if (pending.get(key) === current) pending.delete(key)
      })
    }
    // Keep a shared request only while at least one page or login preload needs it.
    return subscribe(key, item, signal) as Promise<T>
  }

  return { read, invalidate, clear: () => invalidate() }
}

export const reportCache = createReportCache({ scope: getDeviceId })
