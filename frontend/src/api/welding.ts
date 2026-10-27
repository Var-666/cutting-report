import type { WeldingAnalysis, WeldingPart, WeldingQuery, WeldingReport, WeldingReportKind } from '../types/welding'
import { request } from './client.ts'
import { throwIfAborted } from '../utils/abort.ts'

const paths = { dashboard: 'dashboard', daily: 'statistics/daily', parts: 'parts', jobs: 'jobs', equipment: 'equipment/summary' }

export function weldingQuery<T extends object>(values: T): Partial<T> {
  return Object.fromEntries(Object.entries(values).filter(([, value]) => value !== '' && value !== null && value !== undefined)) as Partial<T>
}

export function getWeldingReport<K extends WeldingReportKind>(kind: K, values: WeldingQuery, signal?: AbortSignal, force = false) {
  return request<WeldingReport<K extends 'dashboard' ? 'jobs' : K>>(`/api/v1/welding/${paths[kind]}?${new URLSearchParams(Object.entries({ ...weldingQuery(values), ...(force ? { refresh: 'true' } : {}) }).map(([key, value]) => [key, String(value)]))}`, { signal })
}
export function getWeldingPart(id: number, page: number, signal?: AbortSignal) {
  return request<WeldingPart>(`/api/v1/welding/parts/${encodeURIComponent(id)}?page=${page}&page_size=20`, { signal })
}
export function analyzeWelding(reportType: Exclude<WeldingReportKind, 'dashboard'>, query: WeldingQuery, version: string, signal?: AbortSignal) {
  return request<WeldingAnalysis>('/api/v1/welding/analysis', { method: 'POST', body: JSON.stringify({ report_type: reportType, query: weldingQuery(query), data_version: version }), signal })
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
