import { toError } from '../utils/errors'
import { throwIfAborted } from '../utils/abort.ts'
import type { ExportFormat } from '../types/api'
import type { ReportCreateRequest } from '../types/reports'
import { onBeforeUnmount, onDeactivated, ref } from 'vue'
import { useRouter } from 'vue-router'
import { createReport, createReportKey, downloadReportPdf, waitForReport } from '../api/reportExport.ts'
import { getDeviceId } from '../api/deviceContext.ts'

type ExportRequest = { payload: ReportCreateRequest; format: ExportFormat; deviceId: string; idempotencyKey: string;
  reportId?: string; htmlReady?: boolean }

export function useReportExport() {
  const router = useRouter()
  const exporting = ref(false)
  const error = ref('')
  const progress = ref('')
  const retryLabel = ref('确认创建结果')
  let controller: AbortController | undefined
  let lastRequest: ExportRequest | null = null

  function exportReport(payload: ReportCreateRequest, format: ExportFormat = 'html') {
    if (exporting.value) return
    const deviceId = getDeviceId()
    if (lastRequest && !lastRequest.reportId && lastRequest.deviceId === deviceId &&
        JSON.stringify(lastRequest.payload) === JSON.stringify(payload)) {
      lastRequest.format = format
    } else {
      lastRequest = { payload, format, deviceId, idempotencyKey: createReportKey() }
    }
    return deliver(lastRequest)
  }

  async function deliver(request: ExportRequest) {
    controller = new AbortController()
    const signal = controller.signal
    exporting.value = true
    error.value = ''
    progress.value = request.reportId ? '正在查询报告进度…' : '正在创建报告…'
    try {
      if (!request.reportId) {
        const job = await createReport(request.payload, request)
        request.reportId = job.report_id
      }
      throwIfAborted(signal)
      if (request.format === 'html') {
        progress.value = ''
        await router.push({ name: 'report-center', query: { job: request.reportId } })
      } else {
        if (!request.htmlReady) {
          await waitForReport(request.reportId, { signal, deviceId: request.deviceId, onStatus: message => { progress.value = message } })
          request.htmlReady = true
        }
        progress.value = 'HTML 已归档，正在准备 PDF…'
        await downloadReportPdf(request.reportId, { signal, deviceId: request.deviceId })
        progress.value = 'PDF 已就绪，已交给浏览器下载。'
      }
    } catch (caught) {
      const exception = toError(caught)
      if (exception.name !== 'AbortError') {
        if ((exception as { status?: number }).status === 404) request.htmlReady = false
        retryLabel.value = request.htmlReady ? '重试下载 PDF' : request.reportId ? '查看任务进度' : '确认创建结果'
        error.value = request.htmlReady ? `${exception.message}。已归档的 HTML 可在“生成与历史”下载。` : exception.message
      }
      progress.value = ''
    } finally {
      exporting.value = false
    }
  }

  function retryExport() {
    if (!lastRequest || exporting.value) return
    return deliver(lastRequest)
  }

  const pause = () => controller?.abort()
  onDeactivated(pause)
  onBeforeUnmount(pause)
  return { exporting, error, progress, retryLabel, exportReport, retryExport }
}
