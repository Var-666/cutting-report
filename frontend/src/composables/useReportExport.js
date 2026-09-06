import { onBeforeUnmount, ref } from 'vue'
import { useRouter } from 'vue-router'
import { createReport, downloadReportPdf, waitForReport } from '../api/reportExport.js'

export function useReportExport() {
  const router = useRouter()
  const exporting = ref(false)
  const error = ref('')
  const progress = ref('')
  let controller

  async function exportReport(payload, format = 'html') {
    if (exporting.value) return
    controller = new AbortController()
    const signal = controller.signal
    exporting.value = true
    error.value = ''
    progress.value = '正在创建报告…'
    try {
      const job = await createReport(payload)
      signal.throwIfAborted()
      if (format === 'html') {
        progress.value = ''
        await router.push({ name: 'report-center', query: { job: job.report_id } })
      } else {
        await waitForReport(job.report_id, { signal, onStatus: (message) => { progress.value = message } })
        progress.value = '正在生成并下载 PDF…'
        await downloadReportPdf(job.report_id, { signal })
        progress.value = 'PDF 已生成，已开始下载。'
      }
    } catch (exception) {
      if (exception.name !== 'AbortError') error.value = exception.message
      progress.value = ''
    } finally {
      exporting.value = false
    }
  }

  onBeforeUnmount(() => controller?.abort())
  return { exporting, error, progress, exportReport }
}
