import { toError } from '../utils/errors'
import { throwIfAborted } from '../utils/abort.ts'
import type { ExportFormat } from '../types/api'
import type { ReportCreateRequest } from '../types/reports'
import { onBeforeUnmount, onDeactivated, ref } from 'vue'
import { useRouter } from 'vue-router'
import { createReport, downloadReportPdf, waitForReport } from '../api/reportExport.ts'

export function useReportExport() {
  const router = useRouter()
  const exporting = ref(false)
  const error = ref('')
  const progress = ref('')
  let controller: AbortController | undefined
  let lastRequest: { payload: ReportCreateRequest; format: ExportFormat } | null = null

  async function exportReport(payload: ReportCreateRequest, format: ExportFormat = 'html') {
    if (exporting.value) return
    lastRequest = { payload, format }
    controller = new AbortController()
    const signal = controller.signal
    exporting.value = true
    error.value = ''
    progress.value = '正在创建报告…'
    try {
      const job = await createReport(payload)
      throwIfAborted(signal)
      if (format === 'html') {
        progress.value = ''
        await router.push({ name: 'report-center', query: { job: job.report_id } })
      } else {
        await waitForReport(job.report_id, { signal, onStatus: (message) => { progress.value = message } })
        progress.value = '正在生成并下载 PDF…'
        await downloadReportPdf(job.report_id, { signal })
        progress.value = 'PDF 已生成，已开始下载。'
      }
    } catch (caught) {
    const exception = toError(caught)
      if (exception.name !== 'AbortError') error.value = exception.message
      progress.value = ''
    } finally {
      exporting.value = false
    }
  }

  function retryExport() {
    if (!lastRequest || exporting.value) return
    return exportReport(lastRequest.payload, lastRequest.format)
  }

  const pause = () => controller?.abort()
  onDeactivated(pause)
  onBeforeUnmount(pause)
  return { exporting, error, progress, exportReport, retryExport }
}
