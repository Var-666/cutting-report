import type { ApiError, ApiResponse, RequestOptions } from '../types/api'
import { reportApiFailure } from './browserObservations.ts'
import { deviceUrl, getDeviceId } from './deviceContext.ts'
import { createRequestCancellation } from './requestCancellation.ts'
import { notifySessionExpired } from './sessionExpiry.ts'

const DEFAULT_HEADERS = { 'Content-Type': 'application/json' }

export function request(url: string, options: RequestOptions & { responseType: 'blob' }): Promise<{ blob: Blob; headers: Headers }>
export function request<T = unknown>(url: string, options?: RequestOptions): Promise<T>
export async function request<T = unknown>(url: string, options: RequestOptions = {}): Promise<T | { blob: Blob; headers: Headers }> {
  const lineId = options.headers?.['X-Production-Line'] ?? getDeviceId()
  url = deviceUrl(url, lineId)
  const cancellation = createRequestCancellation(options.signal)
  try {
    const response = await fetch(url, {
      ...options,
      credentials: 'same-origin',
      headers: {
        ...DEFAULT_HEADERS,
        ...(lineId ? { 'X-Production-Line': lineId } : {}),
        ...(options.headers || {}),
        ...cancellation.headers,
      },
    })
    const reportCode = response.headers.get('X-Report-Error') || response.headers.get('X-Welding-Error')
      || response.headers.get('X-Workload-Error') || response.headers.get('X-SQL-Resource-Error')
    if (!response.ok && !(response.status === 503 && reportCode === 'snapshot_refresh_busy')) reportApiFailure(url, response.status, response.headers.get('X-Request-ID') ?? cancellation.requestId)
    if (options.responseType === 'blob' && response.ok) return { blob: await response.blob(), headers: response.headers }
    let payload: ApiResponse<T>
    try {
      payload = await response.json()
    } catch (error) {
      if (error instanceof Error && error.name === 'AbortError') throw error
      throw new Error('服务器返回了无法识别的响应')
    }
    if (
      response.status === 401
      && response.headers.get('X-Session-Expired') === '1'
    ) {
      await notifySessionExpired().catch(() => undefined)
    }
    if (!response.ok || payload.success === false) {
      const message = payload.error?.message || payload.detail || '请求失败，请稍后重试'
      const error: ApiError = new Error(message)
      error.status = response.status
      const code = reportCode
      if (code) error.code = code
      const retryAfter = Number(response.headers.get('Retry-After'))
      if (Number.isFinite(retryAfter) && retryAfter > 0) error.retryAfterMs = retryAfter * 1000
      throw error
    }
    return payload.data
  } finally {
    cancellation.dispose()
  }
}
