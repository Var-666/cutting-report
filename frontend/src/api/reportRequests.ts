import type { ApiError } from '../types/api'
import { throwIfAborted } from '../utils/abort'
import { reportCache, REPORT_VIEW_CACHE_TTL } from './reportCache'
import { getDeviceId } from './deviceContext'

export type ReportReadMode = 'read' | 'reload' | 'refresh' | 'poll'
export interface ReportReadOptions {
  signal?: AbortSignal
  mode?: ReportReadMode
  cacheScope?: string
  onPreparing?: () => void
}
export const REPORT_POLL_INTERVAL = 5000
export const REPORT_POLL_LIMIT = 75

export function reportIsRefreshing(value: unknown): boolean {
  if (!value || typeof value !== 'object' || !('meta' in value)) return false
  const meta = value.meta
  if (!meta || typeof meta !== 'object' || !('report_data' in meta)) return false
  const state = meta.report_data
  return Boolean(state && typeof state === 'object' && 'refreshing' in state && state.refreshing
    && !('refresh_failed' in state && state.refresh_failed))
}

export async function pollReportUpdates<T>(initial: T, load: () => Promise<T>, update: (value: T) => void,
  signal?: AbortSignal): Promise<void> {
  let value = initial
  for (let attempt = 0; reportIsRefreshing(value); attempt++) {
    if (attempt >= REPORT_POLL_LIMIT) throw new Error('后台仍在处理，已停止自动检查，请手动刷新。')
    await waitForReportPoll(signal)
    value = await load()
    throwIfAborted(signal)
    update(value)
  }
}

export function waitForReportPoll(signal?: AbortSignal): Promise<void> {
  throwIfAborted(signal)
  return new Promise((resolve, reject) => {
    const cancel = () => { clearTimeout(timer); signal?.removeEventListener('abort', cancel); reject(new DOMException('请求已取消', 'AbortError')) }
    const timer = setTimeout(() => { signal?.removeEventListener('abort', cancel); resolve() }, REPORT_POLL_INTERVAL)
    signal?.addEventListener('abort', cancel, { once: true })
    if (signal?.aborted) cancel()
  })
}

export function readReport<T>(key: string, load: (signal: AbortSignal, refresh: boolean, scope: string) => Promise<T>,
  { signal, mode = 'read', cacheScope, onPreparing }: ReportReadOptions = {}): Promise<T> {
  const scope = cacheScope ?? getDeviceId()
  return reportCache.read(key, async sharedSignal => {
    let refresh = mode === 'refresh'
    for (let attempt = 0; ; attempt++) {
      throwIfAborted(sharedSignal)
      try { return await load(sharedSignal, refresh, scope) }
      catch (caught) {
        throwIfAborted(sharedSignal)
        const error = caught as ApiError
        if (error?.status !== 503 || error.code !== 'snapshot_refresh_busy' || attempt >= REPORT_POLL_LIMIT) throw caught
        refresh = false
        onPreparing?.()
        await waitForReportPoll(sharedSignal)
      }
    }
  }, { signal, force: mode !== 'read', cacheScope: scope, ttl: REPORT_VIEW_CACHE_TTL })
}
