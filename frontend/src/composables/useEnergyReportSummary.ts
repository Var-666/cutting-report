import type { Ref } from 'vue'
import type { EnergyPeriodReport } from '../types/energy'
import { nextTick, ref } from 'vue'
import { getEnergyDay, getEnergyMonthlyRange } from '../api/energy'
import { recentTwelveMonthStart } from '../api/reportDates'
import { toError } from '../utils/errors'

export function useEnergyReportSummary(pageActive: Ref<boolean>, initialDate: string) {
  const monthlyReport = ref<EnergyPeriodReport | null>(null)
  const summaryPeriodStart = ref(recentTwelveMonthStart(initialDate))
  const summaryPeriodEnd = ref(initialDate)
  const summaryLoading = ref(false)
  let backgroundController: AbortController | null = null
  let refreshController: AbortController | null = null
  let refreshRequestSequence = 0
  let summaryRefreshTimer: number | null = null
  let summaryRefreshCount = 0

  function stopSummaryRefresh() {
    if (summaryRefreshTimer) window.clearTimeout(summaryRefreshTimer)
    summaryRefreshTimer = null
    refreshRequestSequence += 1
    refreshController?.abort()
    refreshController = null
  }

  async function applyMonthlySummary(summaryResult: EnergyPeriodReport) {
    if (!pageActive.value) return
    monthlyReport.value = summaryResult
    await nextTick()
    scheduleSummaryRefresh(summaryResult)
  }

  function scheduleSummaryRefresh(summaryResult: EnergyPeriodReport) {
    if (summaryRefreshTimer || refreshController || !pageActive.value || !summaryResult?.syncing
        || !summaryResult?.missing_day_count || summaryRefreshCount >= 600) return
    const requestedStart = summaryPeriodStart.value
    const requestedEnd = summaryPeriodEnd.value
    const sequence = refreshRequestSequence
    const current = () => pageActive.value && sequence === refreshRequestSequence
      && requestedStart === summaryPeriodStart.value && requestedEnd === summaryPeriodEnd.value
    summaryRefreshTimer = window.setTimeout(async () => {
      if (!current()) return
      summaryRefreshTimer = null
      summaryRefreshCount += 1
      const controller = new AbortController()
      refreshController = controller
      try {
        const refreshed = await getEnergyMonthlyRange(requestedStart, requestedEnd, { force: true, signal: controller.signal })
        if (!current() || controller.signal.aborted) return
        await applyMonthlySummary(refreshed)
      } catch (caught) {
        const exception = toError(caught)
        if (exception.name === 'AbortError') return
        // Keep the last valid summary and retry within the existing attempt limit.
      } finally {
        if (refreshController === controller) {
          refreshController = null
          if (current() && !controller.signal.aborted) scheduleSummaryRefresh(monthlyReport.value || summaryResult)
        }
      }
    }, 1000)
  }

  function pauseSummary() {
    stopSummaryRefresh()
    backgroundController?.abort()
    backgroundController = null
    summaryLoading.value = false
  }

  function resetSummaryScope(selectedDate: string) {
    pauseSummary()
    summaryRefreshCount = 0
    const recentStart = recentTwelveMonthStart(selectedDate)
    if (summaryPeriodStart.value !== recentStart || summaryPeriodEnd.value !== selectedDate) monthlyReport.value = null
    summaryPeriodStart.value = recentStart
    summaryPeriodEnd.value = selectedDate
  }

  function loadInitialSummary(force: boolean, signal: AbortSignal) {
    const requestedStart = summaryPeriodStart.value
    const requestedEnd = summaryPeriodEnd.value
    const sequence = refreshRequestSequence
    const current = () => pageActive.value && sequence === refreshRequestSequence
    summaryLoading.value = true
    const request = getEnergyMonthlyRange(requestedStart, requestedEnd, { force, signal })
      .then(async summaryResult => {
        if (!current() || signal.aborted) return
        await applyMonthlySummary(summaryResult)
      })
      .finally(() => { if (current() && !signal.aborted) summaryLoading.value = false })

    // First paint uses the saved monthly snapshot; daily meter synchronization runs independently.
    const syncController = new AbortController()
    backgroundController = syncController
    getEnergyDay(requestedEnd, { signal: syncController.signal })
      .then(() => getEnergyMonthlyRange(requestedStart, requestedEnd, { signal: syncController.signal }))
      .then(async summaryResult => {
        if (!current() || syncController.signal.aborted) return
        await applyMonthlySummary(summaryResult)
      })
      .catch(() => {})
      .finally(() => { if (backgroundController === syncController) backgroundController = null })
    return request
  }

  return {
    monthlyReport, summaryPeriodStart, summaryPeriodEnd, summaryLoading,
    resetSummaryScope, loadInitialSummary, applyMonthlySummary, scheduleSummaryRefresh, pauseSummary,
  }
}
