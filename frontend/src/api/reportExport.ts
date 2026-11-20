import type { ReadOptions } from '../types/api'
import { request } from './client.ts'
import { createReport, getReportStatus } from './cutting.ts'
import { throwIfAborted } from '../utils/abort.ts'
import { waitForReportPoll } from './reportRequests.ts'

export function createReportKey() {
  // getRandomValues is also available on the HTTP intranet.
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('')
}

export async function downloadReportPdf(reportId: string, { signal, deviceId }: ReadOptions & { deviceId?: string } = {}) {
  throwIfAborted(signal)
  const { blob, headers } = await request(`/api/v1/reports/${encodeURIComponent(reportId)}/download?format=pdf`, {
    signal, responseType: 'blob', headers: deviceId ? { 'X-Production-Line': deviceId } : undefined,
  })
  if (!headers.get('content-type')?.includes('application/pdf')) {
    throw new Error('服务器未返回 PDF 文件，请重试')
  }
  throwIfAborted(signal)
  if (!blob.size) throw new Error('PDF 文件为空，请重试')
  const disposition = headers.get('content-disposition') || ''
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

export async function waitForReport(reportId: string, { signal, deviceId, onStatus = () => {}, timeoutMs = 300_000 }: ReadOptions & { deviceId?: string; onStatus?: (message: string) => void; timeoutMs?: number } = {}) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    throwIfAborted(signal)
    let job
    try {
      job = await getReportStatus(reportId, { signal, deviceId })
    } catch (error) {
      throwIfAborted(signal)
      const status = (error as { status?: number })?.status
      if (status && status < 500 && status !== 408 && status !== 429) throw error
      onStatus('暂时无法读取进度，正在重新连接…')
      await waitForReportPoll(signal)
      continue
    }
    throwIfAborted(signal)
    onStatus(job.stage_message || '正在生成报告…')
    if (job.status === 'completed') return job
    if (job.status === 'failed') throw new Error(job.error?.message || '报告生成失败')
    await waitForReportPoll(signal)
  }
  throw new Error('已停止等待进度，可在“生成与历史”查看任务或下载 PDF。')
}

export { createReport }
