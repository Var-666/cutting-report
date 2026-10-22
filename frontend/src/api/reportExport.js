import { deviceUrl } from './deviceContext.js'
import { createReport, getReportStatus } from './reports.js'

export async function downloadReportPdf(reportId, { signal } = {}) {
  const response = await fetch(deviceUrl(`/api/v1/reports/${encodeURIComponent(reportId)}/download?format=pdf`), {
    credentials: 'same-origin', signal,
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}))
    throw new Error(payload?.error?.message || payload?.detail || 'PDF 下载失败，请稍后重试')
  }
  if (!response.headers.get('content-type')?.includes('application/pdf')) {
    throw new Error('服务器未返回 PDF 文件，请重试')
  }
  const blob = await response.blob()
  signal?.throwIfAborted()
  const disposition = response.headers.get('content-disposition') || ''
  const encodedName = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
  const plainName = disposition.match(/filename="([^"]+)"/i)?.[1]
  const filename = encodedName ? decodeURIComponent(encodedName) : (plainName || `${reportId}.pdf`)
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 60_000)
}

export async function waitForReport(reportId, { signal, onStatus = () => {}, timeoutMs = 300_000 } = {}) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    signal?.throwIfAborted()
    const job = await getReportStatus(reportId, { signal })
    signal?.throwIfAborted()
    onStatus(job.stage_message || '正在生成报告…')
    if (job.status === 'completed') return job
    if (job.status === 'failed') throw new Error(job.error?.message || '报告生成失败')
    await new Promise((resolve, reject) => {
      const abort = () => { clearTimeout(timer); reject(new DOMException('已取消导出等待', 'AbortError')) }
      const timer = setTimeout(() => { signal?.removeEventListener('abort', abort); resolve() }, 1000)
      signal?.addEventListener('abort', abort, { once: true })
    })
  }
  throw new Error('报告仍在生成，请稍后到“生成与历史”下载 PDF。')
}

export { createReport }
