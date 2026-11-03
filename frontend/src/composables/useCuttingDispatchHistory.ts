import type { Ref } from 'vue'
import type { CuttingDispatch } from '../types/cutting'
import type { DispatchTrend } from '../api/generated/types.gen'
import type { DispatchHistoryFilters, DispatchTrendFilters } from '../api/dispatch'
import { computed, ref } from 'vue'
import { listCuttingDispatches, getCuttingDispatchTrend } from '../api/dispatch'
import { toError } from '../utils/errors'

export function useCuttingDispatchHistory({ pageActive, isBusy }: { pageActive: Ref<boolean>; isBusy: () => boolean }) {
  let historyController: AbortController | null = null
  let trendController: AbortController | null = null
  const histories = ref<CuttingDispatch[]>([])
  const historyPage = ref(1)
  const historyPageSize = 10
  const historyTotal = ref(0)
  const historyLoading = ref(false)
  const historyError = ref('')
  const historyFilters = ref<DispatchHistoryFilters>({})
  const trend = ref<DispatchTrend | null>(null)
  const trendLoading = ref(false)
  const trendError = ref('')
  function defaultPeriod(): DispatchTrendFilters {
    const end = new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Shanghai' })
    const start = new Date(Date.parse(`${end}T00:00:00Z`) - 29 * 86400000).toISOString().slice(0, 10)
    return { start_date: start, end_date: end }
  }
  const trendFilters = ref<DispatchTrendFilters>(defaultPeriod())
  const selectedHistoryDay = computed(() => historyFilters.value.start_date === historyFilters.value.end_date ? historyFilters.value.start_date || '' : '')
  const historySelectionLabel = computed(() => {
    const filters = historyFilters.value
    if (!filters.start_date) return '全部派发记录（包含待处理和失败记录）'
    return `${filters.start_date}${filters.start_date === filters.end_date ? '' : ` 至 ${filters.end_date}`} 成功派发记录`
  })
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
    const filters = { ...historyFilters.value }
    try {
      let data = await listCuttingDispatches({ ...filters, page: targetPage, pageSize: historyPageSize }, { signal: controller.signal })
      if (controller.signal.aborted || !pageActive.value || historyController !== controller) return
      const lastPage = Math.max(1, Math.ceil(data.total / historyPageSize))
      if (targetPage > lastPage) {
        targetPage = lastPage
        data = await listCuttingDispatches({ ...filters, page: targetPage, pageSize: historyPageSize }, { signal: controller.signal })
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

  async function loadTrend({ signal }: { signal?: AbortSignal } = {}) {
    if (!pageActive.value || signal?.aborted) return
    trendController?.abort()
    const controller = new AbortController()
    trendController = controller
    const abort = () => controller.abort()
    signal?.addEventListener('abort', abort, { once: true })
    trendLoading.value = true
    trendError.value = ''
    try {
      const data = await getCuttingDispatchTrend({ ...trendFilters.value }, { signal: controller.signal })
      if (!controller.signal.aborted && pageActive.value && trendController === controller) trend.value = data
    } catch (caught) {
      const error = toError(caught)
      if (error.name !== 'AbortError' && !controller.signal.aborted && pageActive.value && trendController === controller) trendError.value = error.message
    } finally {
      signal?.removeEventListener('abort', abort)
      if (trendController === controller) {
        trendLoading.value = false
        trendController = null
      }
    }
  }

  async function refreshHistory(options: { signal?: AbortSignal } = {}) {
    await Promise.all([loadHistory(options), loadTrend(options)])
  }

  function resetRows(filters: DispatchHistoryFilters) {
    historyFilters.value = { ...filters }
    historyPage.value = 1
    histories.value = []
    historyTotal.value = 0
  }

  async function applyHistoryFilters(filters: DispatchTrendFilters) {
    if (!pageActive.value || isBusy()) return
    trendFilters.value = { ...filters }
    trend.value = null
    resetRows(filters)
    await refreshHistory()
  }

  async function selectHistoryDay(day: string) {
    if (!pageActive.value || isBusy() || trendLoading.value || !trend.value?.items.some(item => item.date === day)) return
    resetRows({ ...trendFilters.value, start_date: day, end_date: day })
    await loadHistory()
  }

  async function clearHistoryFilters() {
    if (!pageActive.value || isBusy()) return
    trendFilters.value = defaultPeriod()
    trend.value = null
    resetRows({})
    await refreshHistory()
  }

  function pauseHistory() {
    historyController?.abort()
    historyController = null
    historyLoading.value = false
    trendController?.abort()
    trendController = null
    trendLoading.value = false
  }

  return { histories, historyPage, historyPageSize, historyTotal, historyLoading, historyError,
    trend, trendLoading, trendError, trendFilters, selectedHistoryDay, historySelectionLabel,
    loadHistory, loadTrend, refreshHistory, applyHistoryFilters, selectHistoryDay, clearHistoryFilters,
    changeHistoryPage, pauseHistory }
}
