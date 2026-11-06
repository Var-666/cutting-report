import type { TaskEnergyReport } from '../types/energy'
import { computed, nextTick, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getEnergyMonthlyRange, getEnergyRange, getTaskEnergyDay } from '../api/energy'
import { reportToday } from '../api/reportDates'
import { useDeviceSwitchQuery } from './useDeviceSwitchQuery'
import { useEnergyReportSummary } from './useEnergyReportSummary'
import { mergeOwnedQuery, queryChoice, queryDateRange, sameRouteQuery } from '../utils/reportRouteState'
import { toError } from '../utils/errors'

export function useEnergyReportQuery() {
  const route = useRoute()
  const router = useRouter()
  const today = reportToday()
  const anchorDate = ref(today)
  const periodStart = ref(today)
  const periodEnd = ref(today)
  const appliedPeriodStart = ref(today)
  const appliedPeriodEnd = ref(today)
  const rangeQueryApplied = ref(false)
  const taskDay = ref<TaskEnergyReport | null>(null)
  const chartType = ref(queryChoice(route.query.chart, ['bar_line', 'bar', 'line'], 'bar_line'))
  const loading = ref(false)
  const error = ref('')
  const pageActive = ref(true)
  const {
    monthlyReport, summaryPeriodStart, summaryPeriodEnd, summaryLoading,
    resetSummaryScope, loadInitialSummary, applyMonthlySummary, scheduleSummaryRefresh, pauseSummary,
  } = useEnergyReportSummary(pageActive, today)
  let controller: AbortController | null = null
  let requestSequence = 0
  let routeStateReady = false

  const routeQueryKeys = ['start', 'end', 'chart']
  const dateQueryDirty = computed(() => (
    periodStart.value !== appliedPeriodStart.value || periodEnd.value !== appliedPeriodEnd.value
  ))

  function routeStateQuery() {
    return mergeOwnedQuery(route.query, routeQueryKeys, {
      start: rangeQueryApplied.value ? appliedPeriodStart.value : '',
      end: rangeQueryApplied.value ? appliedPeriodEnd.value : '',
      chart: chartType.value === 'bar_line' ? '' : chartType.value,
    })
  }

  useDeviceSwitchQuery('equipment-report', () => ({
    ...routeStateQuery(), start: appliedPeriodStart.value, end: appliedPeriodEnd.value,
  }))

  async function syncRouteState() {
    const query = routeStateQuery()
    if (sameRouteQuery(query, route.query)) return
    await router.push({ query })
  }

  function cancelReportRequest() {
    requestSequence += 1
    controller?.abort()
    controller = null
    loading.value = false
    summaryLoading.value = false
  }

  async function changeChartType(value: string) {
    chartType.value = value
    const query = mergeOwnedQuery(route.query, ['chart'], { chart: value === 'bar_line' ? '' : value })
    if (loading.value) Object.assign(query, { start: periodStart.value, end: periodEnd.value })
    if (pageActive.value && !sameRouteQuery(query, route.query)) await router.push({ query })
  }

  function beginRequest({ preserveSummary = false } = {}) {
    cancelReportRequest()
    if (!preserveSummary) {
      pauseSummary()
    }
    controller = new AbortController()
    return { sequence: requestSequence, signal: controller.signal }
  }

  function requestIsCurrent(sequence: number, signal: AbortSignal) {
    return pageActive.value && !signal.aborted && sequence === requestSequence
  }
  const analysisReady = computed(() => Boolean(
    pageActive.value
    && taskDay.value
    && monthlyReport.value?.analysis_source_version
    && taskDay.value.period_start === appliedPeriodStart.value
    && taskDay.value.period_end === appliedPeriodEnd.value
    && !loading.value
    && !summaryLoading.value
  ))

  async function loadRange(force = false, { syncRoute = true, useAppliedPeriod = false } = {}) {
    const requestedStart = useAppliedPeriod ? appliedPeriodStart.value : periodStart.value
    const requestedEnd = useAppliedPeriod ? appliedPeriodEnd.value : periodEnd.value
    if (!requestedStart || !requestedEnd) return
    if (requestedEnd < requestedStart) {
      error.value = '结束日期不能早于开始日期'
      return
    }
    const { sequence, signal } = beginRequest({ preserveSummary: true })
    loading.value = true
    error.value = ''
    try {
      const result = await getEnergyRange(requestedStart, requestedEnd, { force, signal })
      if (!requestIsCurrent(sequence, signal)) return
      taskDay.value = result.task_report
      appliedPeriodStart.value = requestedStart
      appliedPeriodEnd.value = requestedEnd
      rangeQueryApplied.value = true
      await nextTick()
      if (!requestIsCurrent(sequence, signal)) return

      if (syncRoute) await syncRouteState()
    } catch (caught) {
      const exception = toError(caught)
      if (exception.name !== 'AbortError' && sequence === requestSequence) error.value = exception.message
    } finally {
      if (sequence === requestSequence) {
        loading.value = false
        controller = null
      }
    }
  }

  async function loadDefaultRanges(force = false) {
    const { sequence, signal } = beginRequest()
    loading.value = true
    summaryLoading.value = true
    error.value = ''
    const selectedDate = periodStart.value
    if (appliedPeriodStart.value !== selectedDate || appliedPeriodEnd.value !== selectedDate) taskDay.value = null
    appliedPeriodStart.value = selectedDate
    appliedPeriodEnd.value = selectedDate
    resetSummaryScope(selectedDate)
    rangeQueryApplied.value = false

    const showError = (exception: Error) => {
      if (exception.name !== 'AbortError' && requestIsCurrent(sequence, signal)) error.value ||= exception.message
    }
    const taskRequest = getTaskEnergyDay(selectedDate, { force, signal })
      .then(async (taskResult) => {
        if (!requestIsCurrent(sequence, signal)) return
        taskDay.value = {
          ...taskResult,
          period_start: selectedDate,
          period_end: selectedDate,
        }
        await nextTick()

      })
      .catch(showError)
    const summaryRequest = loadInitialSummary(force, signal).catch(showError)

    await Promise.allSettled([taskRequest, summaryRequest])
    if (requestIsCurrent(sequence, signal)) {
      loading.value = false
      controller = null
    }
  }

  function pause() {
    if (pageActive.value) {
      pageActive.value = false
      cancelReportRequest()
      loading.value = false
      summaryLoading.value = false
      pauseSummary()
    }
  }

  async function initializeReport({ restoreQuery = false } = {}) {
    if (!pageActive.value) return
    const requestedQuery = route.query
    const requestedRange = restoreQuery ? queryDateRange(route.query, { maxDays: 31 }) : null
    const selectedDate = reportToday()
    anchorDate.value = selectedDate
    periodStart.value = selectedDate
    periodEnd.value = selectedDate
    await loadDefaultRanges()
    if (requestedRange && pageActive.value && route.query === requestedQuery) {
      periodStart.value = requestedRange.start
      periodEnd.value = requestedRange.end
      await loadRange(false, { syncRoute: false })
    }
  }

  async function restoreRouteState({ preserveDraft = false } = {}) {
    if (route.name !== 'equipment-report') return false
    cancelReportRequest()
    const range = queryDateRange(route.query, { maxDays: 31 })
    const nextChartType = queryChoice(route.query.chart, ['bar_line', 'bar', 'line'], 'bar_line')
    chartType.value = nextChartType

    if (range) {
      const needsLoad = !taskDay.value || !monthlyReport.value || !rangeQueryApplied.value || appliedPeriodStart.value !== range.start || appliedPeriodEnd.value !== range.end
      if (!preserveDraft || needsLoad) {
        periodStart.value = range.start
        periodEnd.value = range.end
      }
      if (!taskDay.value || !monthlyReport.value) {
        await initializeReport({ restoreQuery: true })
        return true
      }
      if (!rangeQueryApplied.value || appliedPeriodStart.value !== range.start || appliedPeriodEnd.value !== range.end) {
        await loadRange(false, { syncRoute: false })
        return true
      }
      return false
    }

    if (!taskDay.value || !monthlyReport.value || rangeQueryApplied.value) {
      await initializeReport()
      return true
    }
    if (!preserveDraft) {
      periodStart.value = appliedPeriodStart.value
      periodEnd.value = appliedPeriodEnd.value
    }
    return false
  }

  function retryReport() {
    return rangeQueryApplied.value ? loadRange(false) : initializeReport()
  }

  async function refreshRangeAnalysisReport() {
    const { sequence, signal } = beginRequest()
    loading.value = true
    summaryLoading.value = true
    error.value = ''
    try {
      const [rangeResult, summaryResult] = await Promise.all([
        getEnergyRange(appliedPeriodStart.value, appliedPeriodEnd.value, { force: true, signal }),
        getEnergyMonthlyRange(
          summaryPeriodStart.value,
          summaryPeriodEnd.value,
          { force: true, signal },
        ),
      ])
      if (!requestIsCurrent(sequence, signal)) return
      taskDay.value = rangeResult.task_report
      await applyMonthlySummary(summaryResult)
      await nextTick()

    } catch (caught) {
      const exception = toError(caught)
      if (exception.name !== 'AbortError' && sequence === requestSequence) error.value = exception.message
    } finally {
      if (sequence === requestSequence) {
        loading.value = false
        summaryLoading.value = false
        controller = null
      }
    }
  }

  async function refreshAnalysisReport() {
    if (!pageActive.value || loading.value) return
    return rangeQueryApplied.value
      ? refreshRangeAnalysisReport()
      : loadDefaultRanges(true)
  }

  onMounted(async () => {
    const initialQuery = route.query
    await initializeReport({ restoreQuery: true })
    routeStateReady = true
    if (pageActive.value && route.query !== initialQuery) await restoreRouteState()
  })
  onActivated(async () => {
    pageActive.value = true
    await nextTick()
    if (!pageActive.value) return
    if (routeStateReady && await restoreRouteState()) return
    if ((!taskDay.value || !monthlyReport.value) && !loading.value) await initializeReport()
    if (monthlyReport.value?.syncing) scheduleSummaryRefresh(monthlyReport.value)
    else if (routeStateReady && taskDay.value && monthlyReport.value && !loading.value) {
      if (rangeQueryApplied.value) loadRange(false, { syncRoute: false, useAppliedPeriod: true })
      else initializeReport()
    }
  })
  onDeactivated(pause)
  onBeforeUnmount(pause)
  watch(() => route.query, async (query, previous) => {
    if (routeStateReady && pageActive.value && route.name === 'equipment-report') {
      await restoreRouteState({ preserveDraft: query.start === previous.start && query.end === previous.end })
    }
  }, { deep: true })


  return {
    anchorDate, periodStart, periodEnd, appliedPeriodStart, appliedPeriodEnd,
    summaryPeriodStart, summaryPeriodEnd, taskDay, monthlyReport, chartType, loading, summaryLoading,
    error, pageActive, rangeQueryApplied, dateQueryDirty, analysisReady,
    loadRange, loadDefaultRanges, changeChartType, initializeReport, retryReport, refreshAnalysisReport,
  }
}
