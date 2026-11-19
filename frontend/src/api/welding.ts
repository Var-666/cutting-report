import type { WeldingAnalysis, WeldingPart, WeldingQuery, WeldingReport, WeldingReportKind } from '../types/welding'
import { request } from './client.ts'
import { getDeviceId } from './deviceContext.ts'
import { readReport, type ReportReadMode } from './reportRequests.ts'
import { throwIfAborted } from '../utils/abort.ts'

const paths = { dashboard: 'dashboard', daily: 'statistics/daily', parts: 'parts', jobs: 'jobs', equipment: 'equipment/summary' }

export function weldingQuery<T extends object>(values: T): Partial<T> {
  return Object.fromEntries(Object.entries(values).filter(([, value]) => value !== '' && value !== null && value !== undefined)) as Partial<T>
}

export function getWeldingReport<K extends WeldingReportKind>(kind: K, values: WeldingQuery, signal?: AbortSignal, force = false, { lineId = getDeviceId(), onPreparing, refresh = force, mode }: { lineId?: string; onPreparing?: () => void; refresh?: boolean; mode?: ReportReadMode } = {}) {
  const filters = { anomalies_only: false, page: 1, page_size: 20, sort_by: 'time', descending: true, ...weldingQuery(values) }
  if (kind === 'dashboard') Object.assign(filters, { page: 1, page_size: 10, sort_by: 'time', descending: true })
  const key = new URLSearchParams(Object.entries(filters).map(([name, value]) => [name, String(value)]))
  key.sort()
  return readReport(`welding:${lineId}:${kind}:${key}`, (sharedSignal, requestRefresh) => {
    if (!lineId) throw new Error('请先选择焊接产线')
    const query = new URLSearchParams(Object.entries({ ...weldingQuery(values), ...(requestRefresh ? { refresh: 'true' } : {}) }).map(([key, value]) => [key, String(value)]))
    return request<WeldingReport<K extends 'dashboard' ? 'jobs' : K>>(`/api/v1/welding/${paths[kind]}?${query}`, { signal: sharedSignal, headers: { 'X-Production-Line': lineId } })
  }, { signal, mode: mode ?? (force ? refresh ? 'refresh' : 'reload' : 'read'), cacheScope: lineId, onPreparing })
}
export function getWeldingPart(id: number, page: number, signal?: AbortSignal) {
  return request<WeldingPart>(`/api/v1/welding/parts/${encodeURIComponent(id)}?page=${page}&page_size=10`, { signal })
}
export function analyzeWelding(reportType: Exclude<WeldingReportKind, 'dashboard'>, query: WeldingQuery, version: string, signal?: AbortSignal) {
  return request<WeldingAnalysis>('/api/v1/welding/analysis', { method: 'POST', body: JSON.stringify({ report_type: reportType, query: weldingQuery(query), data_version: version }), signal })
}
export function getLatestWeldingAnalysis(reportType: Exclude<WeldingReportKind, 'dashboard'>, values: WeldingQuery, version: string, signal?: AbortSignal) {
  const query = new URLSearchParams(Object.entries({ ...weldingQuery(values), report_type: reportType, data_version: version })
    .map(([name, value]) => [name, String(value)]))
  return request<WeldingAnalysis | null>(`/api/v1/welding/analysis/latest?${query}`, { signal })
}
export async function downloadWelding(url: string, filename: string, signal?: AbortSignal) {
  throwIfAborted(signal)
  const { blob } = await request(url, { signal, responseType: 'blob' })
  throwIfAborted(signal)
  const extension = filename.split('.').pop()
  const formats: Record<string, string> = { pdf: 'application/pdf', csv: 'text/csv', html: 'text/html' }
  const expected = formats[extension || '']
  if (expected && !blob.type.includes(expected)) throw new Error('服务器返回的文件格式不正确，请重试')
  const objectUrl = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = objectUrl
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(objectUrl), 60000)
}
