import type { Ref } from 'vue'
import type { CuttingDispatch } from '../types/cutting'
import { computed, ref } from 'vue'
import { listCuttingDispatches } from '../api/dispatch'
import { toError } from '../utils/errors'

export function useCuttingDispatchHistory({ pageActive, isBusy }: { pageActive: Ref<boolean>; isBusy: () => boolean }) {
  let historyController: AbortController | null = null
  const histories = ref<CuttingDispatch[]>([])
  const historyPage = ref(1)
  const historyPageSize = 10
  const historyTotal = ref(0)
  const historyLoading = ref(false)
  const historyError = ref('')
  const historyPageCount = computed(() => Math.max(1, Math.ceil(historyTotal.value / historyPageSize)))

  async function loadHistory({ signal, page: targetPage = historyPage.value }: { signal?: AbortSignal; page?: number } = {}) {
    if (!pageActive.value || signal?.aborted) return
    historyController?.abort()
    const controller = new AbortController()
    historyController = controller
    const abort = () => controller.abort()
    signal?.addEventListener('abort', abort, { once: true })
    historyLoading.value = true
    historyError.value = ''
    try {
      let data = await listCuttingDispatches({ page: targetPage, pageSize: historyPageSize }, { signal: controller.signal })
      if (controller.signal.aborted || !pageActive.value || historyController !== controller) return
      const lastPage = Math.max(1, Math.ceil(data.total / historyPageSize))
      if (targetPage > lastPage) {
        targetPage = lastPage
        data = await listCuttingDispatches({ page: targetPage, pageSize: historyPageSize }, { signal: controller.signal })
      }
      if (controller.signal.aborted || !pageActive.value || historyController !== controller) return
      histories.value = data.items
      historyTotal.value = data.total
      historyPage.value = data.page
    } catch (caught) {
      const error = toError(caught)
      if (error.name !== 'AbortError' && !controller.signal.aborted && pageActive.value && historyController === controller) {
        historyError.value = error.message
      }
    } finally {
      signal?.removeEventListener('abort', abort)
      if (historyController === controller) {
        historyLoading.value = false
        historyController = null
      }
    }
  }

  async function changeHistoryPage(value: number) {
    if (historyLoading.value || isBusy() || value < 1 || value > historyPageCount.value) return
    await loadHistory({ page: value })
  }

  function pauseHistory() {
    historyController?.abort()
    historyController = null
    historyLoading.value = false
  }

  return { histories, historyPage, historyPageSize, historyTotal, historyLoading, historyError, loadHistory, changeHistoryPage, pauseHistory }
}
