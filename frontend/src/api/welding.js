import { request } from './reports.js'

const paths = { dashboard: 'dashboard', daily: 'statistics/daily', parts: 'parts', jobs: 'jobs', equipment: 'equipment/summary' }

export function weldingQuery(values) {
  return Object.fromEntries(Object.entries(values).filter(([, value]) => value !== '' && value !== null && value !== undefined))
}

export function getWeldingReport(kind, values, signal) {
  return request(`/api/v1/welding/${paths[kind]}?${new URLSearchParams(weldingQuery(values))}`, { signal })
}
export function getWeldingPart(id, page, signal) {
  return request(`/api/v1/welding/parts/${encodeURIComponent(id)}?page=${page}&page_size=20`, { signal })
}
export function analyzeWelding(reportType, query, version, signal) {
  return request('/api/v1/welding/analysis', { method: 'POST', body: JSON.stringify({ report_type: reportType, query: weldingQuery(query), data_version: version }), signal })
}
export async function downloadWelding(url, filename, signal) {
  const { blob } = await request(url, { signal, responseType: 'blob' })
  signal?.throwIfAborted()
  const extension = filename.split('.').pop()
  const expected = { pdf: 'application/pdf', csv: 'text/csv', html: 'text/html' }[extension]
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
