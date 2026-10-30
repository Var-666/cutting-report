import type { Ref } from 'vue'
import type { RouteLocationNormalizedLoaded, Router } from 'vue-router'
import type { LeadershipReport } from '../types/reports'
import { computed, nextTick, ref } from 'vue'
import { getLeadershipRange, getLeadershipReport } from '../api/cutting'
import { recentTwelveMonthStart } from '../api/reportDates'
import { mergeOwnedQuery, queryChoice, queryDateRange, sameRouteQuery } from '../utils/reportRouteState'
import { toError } from '../utils/errors'

export function useLeadershipReportQuery({ route, router, today, routeRange, pageActive, renderCharts }: {
  route: RouteLocationNormalizedLoaded
  router: Router
  today: string
  routeRange: { start: string; end: string } | null
  pageActive: Ref<boolean>
  renderCharts: (options?: { monthly?: boolean }) => void
}) {
  const defaultPeriodStart = `${today.slice(0, 7)}-01`
  const periodStart = ref(routeRange?.start || defaultPeriodStart)
  const periodEnd = ref(routeRange?.end || today)
  const appliedPeriodStart = ref(periodStart.value)
  const appliedPeriodEnd = ref(periodEnd.value)
  const monthlyPeriodStart = ref(recentTwelveMonthStart(today))
  const monthlyPeriodEnd = ref(today)
  const defaultMonthlyView = ref(true)
  const chartType = ref(queryChoice(route.query.chart, ['bar_line', 'bar', 'line', 'area'], 'bar_line'))
  const report = ref<LeadershipReport | null>(null)
  const monthlyReport = ref<LeadershipReport | null>(null)
  const loading = ref(false)
  const monthlyLoading = ref(false)
  const refreshLoading = ref(false)
  const error = ref('')
  let refreshTimer: number | null = null
  let refreshPollCount = 0
  let requestController: AbortController | null = null
  let requestSequence = 0
  const routeQueryKeys = ['start', 'end', 'chart']
  const dateQueryDirty = computed(() => (
    periodStart.value !== appliedPeriodStart.value || periodEnd.value !== appliedPeriodEnd.value
  ))

  function routeStateQuery() {
    return mergeOwnedQuery(route.query, routeQueryKeys, {
      start: defaultMonthlyView.value ? '' : appliedPeriodStart.value,
      end: defaultMonthlyView.value ? '' : appliedPeriodEnd.value,
      chart: chartType.value === 'bar_line' ? '' : chartType.value,
    })
  }

  async function syncRouteState() {
    const query = routeStateQuery()
    if (sameRouteQuery(query, route.query)) return
    await router.push({ query })
  }

  function cancelReportRequest() {
    stopRefreshPoll()
    requestSequence += 1
    requestController?.abort()
    requestController = null
    loading.value = false
    monthlyLoading.value = false
    refreshLoading.value = false
  }

  async function changeChartType(value: string) {
    chartType.value = value
    const query = mergeOwnedQuery(route.query, ['chart'], { chart: value === 'bar_line' ? '' : value })
    if (loading.value) Object.assign(query, { start: periodStart.value, end: periodEnd.value })
    if (pageActive.value && !sameRouteQuery(query, route.query)) await router.push({ query })
  }

  function beginRequest() {
    cancelReportRequest()
    requestController = new AbortController()
    return { sequence: requestSequence, signal: requestController.signal }
  }

  function requestIsCurrent(sequence: number, signal: AbortSignal) {
    return pageActive.value && !signal.aborted && sequence === requestSequence
  }
  const snapshot = computed(() => report.value?.snapshot || {})

  async function loadReport(force = false, { syncRoute = true } = {}) {
    if (!periodStart.value || !periodEnd.value) return
    if (periodEnd.value < periodStart.value) {
      error.value = '结束日期不能早于开始日期'
      return
    }
    const { sequence, signal } = beginRequest()
    loading.value = true
    error.value = ''
    const requestedStart = periodStart.value
    const requestedEnd = periodEnd.value
    try {
      const result = await getLeadershipRange(requestedStart, requestedEnd, { force, signal })
      if (!requestIsCurrent(sequence, signal)) return
      report.value = result
      appliedPeriodStart.value = requestedStart
      appliedPeriodEnd.value = requestedEnd
      defaultMonthlyView.value = false
      await nextTick()
      if (!requestIsCurrent(sequence, signal)) return
      renderCharts({ monthly: false })
      scheduleRefreshPoll(true)
      if (syncRoute) await syncRouteState()
    } catch (caught) {
      const exception = toError(caught)
      if (exception.name !== 'AbortError' && sequence === requestSequence) error.value = exception.message
    } finally {
      if (sequence === requestSequence) {
        loading.value = false
        requestController = null
      }
    }
  }

  async function restoreRouteState({ preserveDraft = false } = {}) {
    if (route.name !== 'leadership-report') return false
    cancelReportRequest()
    const range = queryDateRange(route.query)
    const nextChartType = queryChoice(route.query.chart, ['bar_line', 'bar', 'line', 'area'], 'bar_line')
    chartType.value = nextChartType

    if (range) {
      const needsLoad = !report.value || defaultMonthlyView.value || appliedPeriodStart.value !== range.start || appliedPeriodEnd.value !== range.end
      if (!preserveDraft || needsLoad) {
        periodStart.value = range.start
        periodEnd.value = range.end
      }
      if (needsLoad) {
        await loadReport(false, { syncRoute: false })
        return true
      }
      return false
    }

    if (!preserveDraft || !report.value || !defaultMonthlyView.value) {
      periodStart.value = defaultPeriodStart
      periodEnd.value = today
    }
    if (!report.value || !defaultMonthlyView.value) {
      await loadDefaultReport()
      return true
    }
    return false
  }

  async function loadDefaultReport(force = false) {
    const { sequence, signal } = beginRequest()
    loading.value = true
    monthlyLoading.value = true
    error.value = ''
    try {
      const result = await getLeadershipReport(today.slice(0, 7), { force, signal })
      if (!requestIsCurrent(sequence, signal)) return
      report.value = result
      monthlyReport.value = result
      appliedPeriodStart.value = `${today.slice(0, 7)}-01`
      appliedPeriodEnd.value = today
      monthlyPeriodStart.value = recentTwelveMonthStart(today)
      monthlyPeriodEnd.value = today
      defaultMonthlyView.value = true
      await nextTick()
      if (!requestIsCurrent(sequence, signal)) return
      renderCharts()
      scheduleRefreshPoll(true)
    } catch (caught) {
      const exception = toError(caught)
      if (exception.name !== 'AbortError' && sequence === requestSequence) error.value = exception.message
    } finally {
      if (sequence === requestSequence) {
        loading.value = false
        monthlyLoading.value = false
        requestController = null
      }
    }
  }

  function stopRefreshPoll() {
    if (refreshTimer) window.clearTimeout(refreshTimer)
    refreshTimer = null
  }

  function scheduleRefreshPoll(reset = false) {
    stopRefreshPoll()
    if (reset) refreshPollCount = 0
    if (!pageActive.value || !snapshot.value.refreshing) {
      refreshLoading.value = false
      return
    }
    refreshLoading.value = true
    const sequence = requestSequence
    const requestedStart = appliedPeriodStart.value
    const requestedEnd = appliedPeriodEnd.value
    const requestedRange = `${requestedStart}:${requestedEnd}`
    const requestedDefaultView = defaultMonthlyView.value
    refreshTimer = window.setTimeout(async () => {
      if (!pageActive.value || sequence !== requestSequence || loading.value || monthlyLoading.value || requestedRange !== `${appliedPeriodStart.value}:${appliedPeriodEnd.value}` || requestedDefaultView !== defaultMonthlyView.value) {
        return
      }
      refreshTimer = null
      const controller = new AbortController()
      requestController = controller
      const signal = controller.signal
      try {
        const result = requestedDefaultView
          ? await getLeadershipReport(requestedStart.slice(0, 7), { force: true, signal })
          : await getLeadershipRange(requestedStart, requestedEnd, { force: true, signal })
        if (!requestIsCurrent(sequence, signal)) return
        report.value = result
        if (requestedDefaultView) monthlyReport.value = result
        await nextTick()
        if (!requestIsCurrent(sequence, signal)) return
        renderCharts({ monthly: requestedDefaultView })
        refreshPollCount += 1
        if (snapshot.value.refreshing && refreshPollCount < 75) {
          scheduleRefreshPoll()
        } else {
          refreshLoading.value = false
        }
      } catch (caught) {
      const exception = toError(caught)
        if (sequence === requestSequence) {
          refreshLoading.value = false
          if (exception.name !== 'AbortError') error.value = exception.message
        }
      } finally {
        if (requestController === controller) requestController = null
      }
    }, 2000)
  }

  function retryReport() {
    return report.value ? loadReport(false) : loadDefaultReport()
  }

  async function refreshRangeAnalysisReport() {
    const { sequence, signal } = beginRequest()
    loading.value = true
    monthlyLoading.value = true
    error.value = ''
    try {
      const [refreshedReport, refreshedSummary] = await Promise.all([
        getLeadershipRange(appliedPeriodStart.value, appliedPeriodEnd.value, { force: true, signal }),
        getLeadershipReport(monthlyPeriodEnd.value.slice(0, 7), { force: true, signal }),
      ])
      if (!requestIsCurrent(sequence, signal)) return
      report.value = refreshedReport
      monthlyReport.value = refreshedSummary
      await nextTick()
      if (!requestIsCurrent(sequence, signal)) return
      renderCharts()
      scheduleRefreshPoll(true)
    } catch (caught) {
      const exception = toError(caught)
      if (exception.name !== 'AbortError' && sequence === requestSequence) error.value = exception.message
    } finally {
      if (sequence === requestSequence) {
        loading.value = false
        monthlyLoading.value = false
        requestController = null
      }
    }
  }

  function refreshAnalysisReport() {
    if (!pageActive.value || loading.value) return
    return defaultMonthlyView.value ? loadDefaultReport(true) : refreshRangeAnalysisReport()
  }

  return { periodStart, periodEnd, appliedPeriodStart, appliedPeriodEnd, monthlyPeriodStart, monthlyPeriodEnd, defaultMonthlyView, chartType, report, monthlyReport, loading, monthlyLoading, refreshLoading, error, dateQueryDirty, routeStateQuery, cancelReportRequest, changeChartType, snapshot, loadReport, restoreRouteState, loadDefaultReport, scheduleRefreshPoll, retryReport, refreshAnalysisReport }
}
