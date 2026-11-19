import type { Ref } from 'vue'
import type { EnergyPeriodReport } from '../types/energy'
import { nextTick, ref } from 'vue'
import { getEnergyDay, getEnergyMonthlyRange } from '../api/energy'
import { recentTwelveMonthStart } from '../api/reportDates'
import { toError } from '../utils/errors'
import { REPORT_POLL_INTERVAL, REPORT_POLL_LIMIT } from '../api/reportRequests'

export function useEnergyReportSummary(pageActive: Ref<boolean>, initialDate: string) {
  const monthlyReport = ref<EnergyPeriodReport | null>(null)
  const summaryPeriodStart = ref(recentTwelveMonthStart(initialDate))
  const summaryPeriodEnd = ref(initialDate)
  const summaryLoading = ref(false)
  const summaryRefreshError = ref('')
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

  async function applyMonthlySummary(summaryResult: EnergyPeriodReport, restart = false) {
    if (!pageActive.value) return
    if (restart) {
      stopSummaryRefresh()
      summaryRefreshCount = 0
      summaryRefreshError.value = ''
    }
    monthlyReport.value = summaryResult
    await nextTick()
    scheduleSummaryRefresh(summaryResult)
  }

  function scheduleSummaryRefresh(summaryResult: EnergyPeriodReport) {
    const state = summaryResult?.meta?.report_data
    if (summaryRefreshTimer || refreshController || !pageActive.value || state?.refresh_failed || summaryRefreshError.value
        || !(state?.refreshing || summaryResult?.syncing && summaryResult?.missing_day_count)
        || summaryRefreshCount >= REPORT_POLL_LIMIT) return
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
      let succeeded = false
      try {
        const refreshed = await getEnergyMonthlyRange(requestedStart, requestedEnd, { mode: 'poll', signal: controller.signal })
        if (!current() || controller.signal.aborted) return
        summaryRefreshError.value = ''
        await applyMonthlySummary(refreshed)
        succeeded = true
      } catch (caught) {
        const exception = toError(caught)
        if (exception.name === 'AbortError') return
        if (current() && !controller.signal.aborted) summaryRefreshError.value = exception.message
      } finally {
        if (refreshController === controller) {
          refreshController = null
          if (succeeded && current() && !controller.signal.aborted) scheduleSummaryRefresh(monthlyReport.value || summaryResult)
        }
      }
    }, REPORT_POLL_INTERVAL)
  }

  function pauseSummary() {
    stopSummaryRefresh()
    backgroundController?.abort()
    backgroundController = null
    summaryLoading.value = false
  }

  function resetSummaryScope(selectedDate: string) {
    pauseSummary()
    summaryRefreshError.value = ''
    summaryRefreshCount = 0
    const recentStart = recentTwelveMonthStart(selectedDate)
    if (summaryPeriodStart.value !== recentStart || summaryPeriodEnd.value !== selectedDate) monthlyReport.value = null
    summaryPeriodStart.value = recentStart
    summaryPeriodEnd.value = selectedDate
  }

  function loadInitialSummary(force: boolean, signal: AbortSignal) {
    summaryRefreshError.value = ''
    const requestedStart = summaryPeriodStart.value
    const requestedEnd = summaryPeriodEnd.value
    const sequence = refreshRequestSequence
    const current = () => pageActive.value && sequence === refreshRequestSequence
    summaryLoading.value = true
    const request = getEnergyMonthlyRange(requestedStart, requestedEnd, { force, signal })
      .then(async summaryResult => {
        if (!current() || signal.aborted) return
        await applyMonthlySummary(summaryResult, true)
      })
      .finally(() => { if (current() && !signal.aborted) summaryLoading.value = false })

    // First paint uses the saved monthly snapshot; daily meter synchronization runs independently.
    const syncController = new AbortController()
    backgroundController = syncController
    getEnergyDay(requestedEnd, { signal: syncController.signal })
      .then(() => getEnergyMonthlyRange(requestedStart, requestedEnd, { signal: syncController.signal }))
      .then(async summaryResult => {
        if (!current() || syncController.signal.aborted) return
        summaryRefreshError.value = ''
        await applyMonthlySummary(summaryResult)
      })
      .catch(caught => {
        const exception = toError(caught)
        if (current() && !syncController.signal.aborted && exception.name !== 'AbortError') summaryRefreshError.value = exception.message
      })
      .finally(() => { if (backgroundController === syncController) backgroundController = null })
    return request
  }

  return {
    monthlyReport, summaryPeriodStart, summaryPeriodEnd, summaryLoading, summaryRefreshError,
    resetSummaryScope, loadInitialSummary, applyMonthlySummary, scheduleSummaryRefresh, pauseSummary,
  }
}
