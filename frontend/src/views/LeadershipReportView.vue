<script setup lang="ts">
import AppReportTable from '../components/AppReportTable.vue'
import type { ReportCreateRequest } from '../types/reports'

import type { LeadershipPeriodRecord, LeadershipReport } from '../types/reports'
import type { ExportFormat } from '../types/api'
import type { BarSeriesOption, LineSeriesOption } from 'echarts'
type TrendSeries = Omit<LineSeriesOption, 'type'> & Pick<BarSeriesOption, 'barMaxWidth'> & { type: 'bar' | 'line' }

import type { EChartsType as ECharts } from 'echarts/core'
import type { EChartsOption } from 'echarts'

import { toError } from '../utils/errors'
import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { init, use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { computed, nextTick, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getLeadershipRange, getLeadershipReport } from '../api/cutting.ts'
import AnalysisPanel from '../components/AnalysisPanel.vue'
import MetricSparkline from '../components/MetricSparkline.vue'
import ReportFeedback from '../components/ReportFeedback.vue'
import ReportTrendPanel from '../components/ReportTrendPanel.vue'
import StandardReportQuery from '../components/StandardReportQuery.vue'
import AppIcon from '../components/AppIcon.vue'
import heroImage from '../assets/home-laser-cutting.webp'
import { recentTwelveMonthStart, reportToday } from '../api/reportDates.ts'
import { formatChineseDate, formatChineseMonth } from '../utils/dateDisplay.ts'
import { createReportChartGrid } from '../utils/reportChartTheme.ts'
import { useReportChartTheme } from '../composables/useReportChartTheme.ts'
import { useDeviceSwitchQuery } from '../composables/useDeviceSwitchQuery.ts'
import { mergeOwnedQuery, queryChoice, queryDateRange, sameRouteQuery } from '../utils/reportRouteState.ts'

defineProps({ exporting: Boolean })
const emit = defineEmits<{ export: [payload: ReportCreateRequest, format: ExportFormat] }>()

use([BarChart, LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

const chartTheme = useReportChartTheme(() => { disposeCharts(); renderCharts() })
const route = useRoute()
const router = useRouter()
const today = reportToday()
const defaultPeriodStart = `${today.slice(0, 7)}-01`
const routeRange = queryDateRange(route.query)
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
const dailyPanel = ref<InstanceType<typeof ReportTrendPanel> | null>(null)
const monthlyPanel = ref<InstanceType<typeof ReportTrendPanel> | null>(null)
const pageActive = ref(true)
let dailyChart: ECharts | null = null
let monthlyChart: ECharts | null = null
let refreshTimer: number | null = null
let refreshPollCount = 0
let requestController: AbortController | null = null
let requestSequence = 0
let resizeListening = false
let chartResizeObserver: ResizeObserver | undefined
let routeStateReady = false

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

useDeviceSwitchQuery('leadership-report', routeStateQuery)

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

const dailyRecords = computed(() => report.value?.daily_records || [])
const dailyChartRecords = computed<LeadershipPeriodRecord[]>(() => {
  if (!report.value) return []
  const byDate = new Map(dailyRecords.value.map(row => [row.period_start, row]))
  const rows: LeadershipPeriodRecord[] = []
  const end = Date.parse(`${appliedPeriodEnd.value}T00:00:00Z`)
  for (let day = Date.parse(`${appliedPeriodStart.value}T00:00:00Z`); day <= end; day += 86_400_000) {
    const date = new Date(day).toISOString().slice(0, 10)
    rows.push(byDate.get(date) || { period_start: date, task_count: 0, cut_length_m: 0, groove_length_m: 0, work_time_seconds: 0 })
  }
  return rows
})
const metricSeries = computed(() => {
  const rows = [...dailyRecords.value].sort((left, right) => left.period_start.localeCompare(right.period_start))
  return {
    taskCount: rows.map((row) => row.task_count),
    cutLength: rows.map((row) => row.cut_length_m),
    grooveLength: rows.map((row) => row.groove_length_m),
    workTime: rows.map((row) => row.work_time_seconds),
  }
})
const monthlyRecords = computed(() => monthlyReport.value?.monthly_records || [])
const total = computed(() => report.value?.current_month_total || {
  task_count: null,
  cut_length_m: null,
  groove_length_m: null,
  work_time_seconds: null,
})
const monthlyTotal = computed(() => monthlyReport.value?.twelve_month_total || {
  task_count: null,
  cut_length_m: null,
  groove_length_m: null,
  work_time_seconds: null,
})
const snapshot = computed(() => report.value?.snapshot || {})
const comparisonTotal = computed(() => report.value?.comparison_total || null)
const monthlyTitle = '近12个月按月对比与趋势'
const analysisReady = computed(() => Boolean(
  pageActive.value
  &&
  report.value
  && monthlyReport.value?.analysis_summary_source_version
  && snapshot.value.data_as_of
  && !loading.value
  && !monthlyLoading.value
))

function roundNumber(value: unknown, digits = 1) {
  const factor = 10 ** digits
  return Math.round((Number(value || 0) + Number.EPSILON) * factor) / factor
}

function formatLength(value: unknown) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '--'
  return new Intl.NumberFormat('zh-CN', {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  }).format(Number(value || 0))
}

function formatDuration(value: unknown) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '--'
  const totalSeconds = Math.max(0, Math.round(Number(value || 0)))
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  return [hours, minutes, seconds].map((part) => String(part).padStart(2, '0')).join(':')
}

function changeRate(current: unknown, previous: unknown) {
  if (previous === null || previous === undefined) return null
  const currentValue = Number(current || 0)
  const previousValue = Number(previous || 0)
  if (!previousValue) return currentValue ? { value: 100, direction: 'up' } : { value: 0, direction: 'flat' }
  const rate = Math.round(((currentValue - previousValue) / Math.abs(previousValue)) * 100)
  return { value: Math.abs(rate), direction: rate > 0 ? 'up' : rate < 0 ? 'down' : 'flat' }
}

const metricChanges = computed(() => ({
  taskCount: changeRate(total.value.task_count, comparisonTotal.value?.task_count),
  cutLength: changeRate(total.value.cut_length_m, comparisonTotal.value?.cut_length_m),
  grooveLength: changeRate(total.value.groove_length_m, comparisonTotal.value?.groove_length_m),
  workTime: changeRate(total.value.work_time_seconds, comparisonTotal.value?.work_time_seconds),
}))

function changeText(change: ReturnType<typeof changeRate>) {
  return change ? `${change.value}%` : '暂无对比'
}

function comparisonLabel(change: ReturnType<typeof changeRate>) {
  if (!change) return '当前统计区间'
  return defaultMonthlyView.value ? '较上月' : '较对比区间'
}

function applyChartType(series: TrendSeries): TrendSeries {
  if (chartType.value === 'bar_line') return series
  const result = { ...series }
  delete result.areaStyle
  if (chartType.value === 'bar') {
    result.type = 'bar'
    delete result.smooth
  } else {
    result.type = 'line'
    result.smooth = true
    if (chartType.value === 'area') result.areaStyle = { opacity: 0.16 }
  }
  return result
}

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

function renderCharts({ monthly = true } = {}) {
  if (!pageActive.value) return
  const dailyChartElement = dailyPanel.value?.getChartElement()
  const monthlyChartElement = monthlyPanel.value?.getChartElement()
  if (dailyChartElement && report.value) {
    dailyChart ||= init(dailyChartElement, chartTheme.darkMode.value ? 'dark' : undefined)
    const records = dailyChartRecords.value
    dailyChart.setOption<EChartsOption>({
      backgroundColor: 'transparent',
      tooltip: { trigger: 'axis', backgroundColor: chartTheme.vars.value.popoverColor, borderColor: chartTheme.vars.value.borderColor, textStyle: { color: chartTheme.vars.value.textColor1 } },
      legend: { data: ['切割长度', '坡口长度', '切割工时'] },
      grid: createReportChartGrid(),
      xAxis: { type: 'category', data: records.map((row) => row.period_start.slice(5)), axisLabel: { hideOverlap: true } },
      yAxis: [
        { type: 'value', name: '长度（米）', splitLine: { lineStyle: { color: chartTheme.vars.value.dividerColor } } },
        { type: 'value', name: '工时（小时）', splitLine: { show: false } },
      ],
      series: ([
        { name: '切割长度', type: 'bar', data: records.map((row) => roundNumber(row.cut_length_m)), tooltip: { valueFormatter: (value) => `${formatLength(value)} 米` }, itemStyle: { color: chartTheme.colors.value.primary } },
        { name: '坡口长度', type: 'bar', data: records.map((row) => roundNumber(row.groove_length_m)), tooltip: { valueFormatter: (value) => `${formatLength(value)} 米` }, itemStyle: { color: chartTheme.colors.value.secondary } },
        { name: '切割工时', type: 'line', yAxisIndex: 1, smooth: true, data: records.map((row) => roundNumber(Number(row.work_time_seconds) / 3600)), tooltip: { valueFormatter: (value) => `${formatLength(value)} 小时` }, itemStyle: { color: chartTheme.colors.value.accent } },
      ] satisfies TrendSeries[]).map(applyChartType),
    }, true)
  }
  if (monthly && monthlyChartElement && monthlyReport.value) {
    monthlyChart ||= init(monthlyChartElement, chartTheme.darkMode.value ? 'dark' : undefined)
    const records = [...monthlyRecords.value].sort((left, right) => left.period_start.localeCompare(right.period_start))
    monthlyChart.setOption<EChartsOption>({
      backgroundColor: 'transparent',
      tooltip: { trigger: 'axis', backgroundColor: chartTheme.vars.value.popoverColor, borderColor: chartTheme.vars.value.borderColor, textStyle: { color: chartTheme.vars.value.textColor1 } },
      legend: { data: ['切割长度', '坡口长度', '完成任务'] },
      grid: createReportChartGrid(),
      xAxis: { type: 'category', data: records.map((row) => row.period_start.slice(0, 7)), axisLabel: { hideOverlap: true } },
      yAxis: [
        { type: 'value', name: '长度（米）', splitLine: { lineStyle: { color: chartTheme.vars.value.dividerColor } } },
        { type: 'value', name: '任务数', splitLine: { show: false } },
      ],
      series: ([
        { name: '切割长度', type: 'bar', data: records.map((row) => roundNumber(row.cut_length_m)), tooltip: { valueFormatter: (value) => `${formatLength(value)} 米` }, itemStyle: { color: chartTheme.colors.value.primary } },
        { name: '坡口长度', type: 'bar', data: records.map((row) => roundNumber(row.groove_length_m)), tooltip: { valueFormatter: (value) => `${formatLength(value)} 米` }, itemStyle: { color: chartTheme.colors.value.secondary } },
        { name: '完成任务', type: 'line', yAxisIndex: 1, smooth: true, data: records.map((row) => row.task_count), itemStyle: { color: chartTheme.colors.value.accent } },
      ] satisfies TrendSeries[]).map(applyChartType),
    }, true)
  }
}

function resizeCharts() {
  if (pageActive.value) {
    dailyChart?.resize()
    monthlyChart?.resize()
  }
}

function disposeCharts() {
  dailyChart?.dispose()
  monthlyChart?.dispose()
  dailyChart = null
  monthlyChart = null
}

function startResizeListening() {
  if (resizeListening) return
  resizeListening = true
  window.addEventListener('resize', resizeCharts)
  chartResizeObserver = new ResizeObserver(resizeCharts)
  for (const panel of [dailyPanel, monthlyPanel]) {
    const element = panel.value?.getChartElement()
    if (element) chartResizeObserver.observe(element)
  }
}

function stopResizeListening() {
  if (!resizeListening) return
  resizeListening = false
  window.removeEventListener('resize', resizeCharts)
  chartResizeObserver?.disconnect()
  chartResizeObserver = undefined
}

function pausePage(dispose = false) {
  if (pageActive.value) {
    pageActive.value = false
    cancelReportRequest()
    stopResizeListening()
  }
  if (dispose) disposeCharts()
}

function exportReport(format: ExportFormat = 'html') {
  emit('export', {
    report_type: 'leadership',
    report_date: appliedPeriodStart.value,
    period_start: appliedPeriodStart.value,
    period_end: appliedPeriodEnd.value,
    project_code: null,
    ship_no: null,
    parameters: {
      chart_type: chartType.value,
      summary_period_start: monthlyPeriodStart.value,
      summary_period_end: monthlyPeriodEnd.value,
    },
  }, format)
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

onMounted(async () => {
  const initialQuery = route.query
  await loadDefaultReport()
  routeStateReady = true
  if (pageActive.value && (routeRange || route.query !== initialQuery)) {
    if (!await restoreRouteState()) scheduleRefreshPoll()
  }
})
onActivated(async () => {
  pageActive.value = true
  await nextTick()
  if (!pageActive.value) return
  startResizeListening()
  if (routeStateReady && await restoreRouteState()) return
  if (!report.value && !loading.value) await loadDefaultReport()
  else if (!dailyChart || (monthlyReport.value && !monthlyChart)) renderCharts()
  resizeCharts()
  scheduleRefreshPoll(true)
})
onDeactivated(pausePage)
onBeforeUnmount(() => pausePage(true))
watch(chartType, async () => {
  await nextTick()
  renderCharts()
})
watch(() => route.query, async (query, previous) => {
  if (routeStateReady && pageActive.value && route.name === 'leadership-report') {
    const preserveDraft = query.start === previous.start && query.end === previous.end
    if (!await restoreRouteState({ preserveDraft })) scheduleRefreshPoll()
  }
}, { deep: true })

const dailyTableColumns = [
  { key: 'c0', title: "序号", width: 56, align: 'center', fixed: 'left' },
  { key: 'c1', title: "日期", width: 152, align: 'left' },
  { key: 'c2', title: "任务数", width: 84, align: 'right' },
  { key: 'c3', title: "切割长度", width: 104, align: 'right' },
  { key: 'c4', title: "坡口长度", width: 104, align: 'right' },
  { key: 'c5', title: "切割时长", width: 104, align: 'center' },
] as const

const monthlyTableColumns = [
  { key: 'c0', title: "序号", width: 56, align: 'center', fixed: 'left' },
  { key: 'c1', title: "月份", width: 112, align: 'left' },
  { key: 'c2', title: "任务数", width: 84, align: 'right' },
  { key: 'c3', title: "切割长度", width: 104, align: 'right' },
  { key: 'c4', title: "坡口长度", width: 104, align: 'right' },
  { key: 'c5', title: "切割时长", width: 104, align: 'center' },
] as const
</script>

<template>
  <section class="page-hero report-hero report-hero--query leadership-hero" :style="{ '--leadership-hero-image': `url(${heroImage})` }">
    <div class="report-hero-copy leadership-hero__copy">
      <span class="report-hero-eyebrow eyebrow">MANAGEMENT OVERVIEW</span>
      <h1>领导报表</h1>
    </div>
    <StandardReportQuery
      v-model:start-date="periodStart"
      v-model:end-date="periodEnd"
      :chart-type="chartType"
      @update:chart-type="changeChartType"
      class="report-hero-query leadership-query-shell"
      aria-label="领导报表查询条件"
      :anchor-date="today"
      :loading="loading"
      :has-data="Boolean(report)"
      :date-dirty="dateQueryDirty"
      :export-disabled="loading || !report || !snapshot.data_as_of || snapshot.refreshing"
      :exporting="exporting"
      @submit="loadReport(false)"
      @export="exportReport"
      @validation-error="error = $event"
    />
  </section>

  <ReportFeedback v-if="error && report" type="error" title="报表更新失败" :message="error" action-label="重新查询" dismissible @action="retryReport" @dismiss="error = ''" />

  <section class="leadership-kpis" aria-label="区间汇总" :aria-busy="loading">
    <article class="leadership-kpi kpi-blue">
      <span class="leadership-kpi__icon"><AppIcon name="tasks" size="22" tone="current" /></span>
      <div><span>完成任务</span><p><strong>{{ total.task_count ?? '--' }}</strong> 项</p><em :class="metricChanges.taskCount?.direction"><AppIcon v-if="metricChanges.taskCount" :name="metricChanges.taskCount.direction === 'down' ? 'arrow-down' : metricChanges.taskCount.direction === 'flat' ? 'activity' : 'arrow-up'" size="12" tone="current" />{{ changeText(metricChanges.taskCount) }} <small>{{ comparisonLabel(metricChanges.taskCount) }}</small></em></div>
      <MetricSparkline class="metric-sparkline" :values="metricSeries.taskCount" label="每日完成任务趋势" />
    </article>
    <article class="leadership-kpi kpi-teal">
      <span class="leadership-kpi__icon"><AppIcon name="ruler" size="22" tone="current" /></span>
      <div><span>切割长度</span><p><strong>{{ formatLength(total.cut_length_m) }}</strong> 米</p><em :class="metricChanges.cutLength?.direction"><AppIcon v-if="metricChanges.cutLength" :name="metricChanges.cutLength.direction === 'down' ? 'arrow-down' : metricChanges.cutLength.direction === 'flat' ? 'activity' : 'arrow-up'" size="12" tone="current" />{{ changeText(metricChanges.cutLength) }} <small>{{ comparisonLabel(metricChanges.cutLength) }}</small></em></div>
      <MetricSparkline class="metric-sparkline" :values="metricSeries.cutLength" label="每日切割长度趋势" />
    </article>
    <article class="leadership-kpi kpi-violet">
      <span class="leadership-kpi__icon"><AppIcon name="grid" size="22" tone="current" /></span>
      <div><span>坡口长度</span><p><strong>{{ formatLength(total.groove_length_m) }}</strong> 米</p><em :class="metricChanges.grooveLength?.direction"><AppIcon v-if="metricChanges.grooveLength" :name="metricChanges.grooveLength.direction === 'down' ? 'arrow-down' : metricChanges.grooveLength.direction === 'flat' ? 'activity' : 'arrow-up'" size="12" tone="current" />{{ changeText(metricChanges.grooveLength) }} <small>{{ comparisonLabel(metricChanges.grooveLength) }}</small></em></div>
      <MetricSparkline class="metric-sparkline" :values="metricSeries.grooveLength" label="每日坡口长度趋势" />
    </article>
    <article class="leadership-kpi kpi-red">
      <span class="leadership-kpi__icon"><AppIcon name="clock" size="22" tone="current" /></span>
      <div><span>切割时长</span><p><strong>{{ formatDuration(total.work_time_seconds) }}</strong> <small>HH:mm:ss</small></p><em :class="metricChanges.workTime?.direction"><AppIcon v-if="metricChanges.workTime" :name="metricChanges.workTime.direction === 'down' ? 'arrow-down' : metricChanges.workTime.direction === 'flat' ? 'activity' : 'arrow-up'" size="12" tone="current" />{{ changeText(metricChanges.workTime) }} <small>{{ comparisonLabel(metricChanges.workTime) }}</small></em></div>
      <MetricSparkline class="metric-sparkline" :values="metricSeries.workTime" label="每日切割时长趋势" />
    </article>
  </section>

  <ReportTrendPanel ref="dailyPanel" number="01" title="区间每日明细与趋势" subtitle="查看所选时间段内每日任务与加工指标变化" chart-label="区间每日趋势图" :height="245" :loading="loading || (refreshLoading && !snapshot.data_as_of)" :has-data="dailyRecords.length > 0" :empty="Boolean(report) && !dailyRecords.length" :error="!report ? error : ''" loading-title="正在读取区间生产数据" loading-message="正在汇总任务、切割长度、坡口长度和切割时长。" empty-title="所选区间暂无已完成任务" empty-message="请调整日期范围，或等待生产数据完成后重新查询。" @retry="retryReport">
    <template #table>
        <AppReportTable flex-height :columns="dailyTableColumns" :items="dailyRecords" :row-key="row => row.period_start" :paginate="false" summary>
        <template #c0="{ row, index: rowIndex }">{{ rowIndex + 1 }}</template>
        <template #c1="{ row }">{{ formatChineseDate(row.period_start) }}</template>
        <template #c2="{ row }">{{ row.task_count }}</template>
        <template #c3="{ row }">{{ formatLength(row.cut_length_m) }}</template>
        <template #c4="{ row }">{{ formatLength(row.groove_length_m) }}</template>
        <template #c5="{ row }"><div class="duration-cell">{{ formatDuration(row.work_time_seconds) }}</div></template>
        <template #summary-c0>汇总</template>
        <template #summary-c1></template>
        <template #summary-c2>{{ total.task_count }}</template>
        <template #summary-c3>{{ formatLength(total.cut_length_m) }}</template>
        <template #summary-c4>{{ formatLength(total.groove_length_m) }}</template>
        <template #summary-c5>{{ formatDuration(total.work_time_seconds) }}</template>
        <template #empty>所选区间暂无已完成任务</template>
      </AppReportTable>
    </template>
  </ReportTrendPanel>

  <ReportTrendPanel ref="monthlyPanel" number="02" :title="monthlyTitle" subtitle="查看近12个月各项数据的对比趋势" :note="`${monthlyPeriodStart} 至 ${monthlyPeriodEnd}`" chart-label="近12个月月度趋势图" :height="245" :loading="monthlyLoading" :has-data="monthlyRecords.length > 0" :empty="Boolean(monthlyReport) && !monthlyRecords.length" :error="!monthlyReport ? error : ''" loading-title="正在读取近12个月趋势" loading-message="月度汇总独立加载，不影响当前区间明细。" empty-title="近12个月暂无已完成任务" empty-message="当前统计范围内还没有可汇总的生产任务。" @retry="loadDefaultReport">
    <template #table>
        <AppReportTable flex-height :columns="monthlyTableColumns" :items="monthlyRecords" :row-key="row => row.period_start" :paginate="false" summary>
        <template #c0="{ row, index: rowIndex }">{{ rowIndex + 1 }}</template>
        <template #c1="{ row }">{{ formatChineseMonth(row.period_start) }}</template>
        <template #c2="{ row }">{{ row.task_count }}</template>
        <template #c3="{ row }">{{ formatLength(row.cut_length_m) }}</template>
        <template #c4="{ row }">{{ formatLength(row.groove_length_m) }}</template>
        <template #c5="{ row }"><div class="duration-cell">{{ formatDuration(row.work_time_seconds) }}</div></template>
        <template #summary-c0>汇总</template>
        <template #summary-c1></template>
        <template #summary-c2>{{ monthlyTotal.task_count }}</template>
        <template #summary-c3>{{ formatLength(monthlyTotal.cut_length_m) }}</template>
        <template #summary-c4>{{ formatLength(monthlyTotal.groove_length_m) }}</template>
        <template #summary-c5>{{ formatDuration(monthlyTotal.work_time_seconds) }}</template>
        <template #empty>近12个月暂无已完成任务</template>
      </AppReportTable>
    </template>
  </ReportTrendPanel>

  <AnalysisPanel
    report-type="leadership"
    :report-date="appliedPeriodStart"
    :period-start="defaultMonthlyView ? '' : appliedPeriodStart"
    :period-end="defaultMonthlyView ? '' : appliedPeriodEnd"
    :summary-period-start="monthlyPeriodStart"
    :summary-period-end="monthlyPeriodEnd"
    :ready="analysisReady"
    :data-version="report?.analysis_source_version || ''"
    :summary-data-version="monthlyReport?.analysis_summary_source_version || ''"
    :version-check-enabled="defaultMonthlyView"
    @refresh-report="refreshAnalysisReport"
  />

</template>

<style scoped>
.leadership-hero{position:relative;min-height:206px;display:grid;grid-template-columns:minmax(360px,.72fr) minmax(660px,1.28fr);grid-template-rows:1fr auto;align-items:center;gap:10px 28px;overflow:hidden;padding:24px 30px 16px;border:1px solid var(--theme-border-color);border-radius:14px;background:linear-gradient(90deg,rgb(var(--container-bg-color) / 0.98) 0%,rgb(var(--container-bg-color) / 0.91) 39%,rgb(var(--container-bg-color) / 0.04) 72%),var(--leadership-hero-image) center 48%/cover no-repeat;box-shadow:var(--theme-shadow);animation:surface-in .46s cubic-bezier(.2,.75,.25,1) both}.leadership-hero::after{content:"";position:absolute;inset:0;pointer-events:none;background:transparent}.leadership-hero__copy,.leadership-hero .report-query{position:relative;z-index:1}.leadership-hero .eyebrow{display:block;margin:0 0 5px;color:rgb(var(--info-color));font-size:var(--font-auxiliary);font-weight:700;letter-spacing:1.4px}.leadership-hero h1{margin:0 0 7px;color:var(--text-color-1);font-size:var(--font-page-title);line-height:1.08;letter-spacing:1px}.leadership-hero .report-query{grid-column:2;grid-row:2;width:100%;grid-template-columns:minmax(145px,1fr) minmax(145px,1fr) 120px 118px 126px;gap:8px;padding:11px 12px;border-color:var(--theme-border-color);border-radius:9px;background:rgb(var(--container-bg-color));box-shadow:var(--theme-shadow);backdrop-filter:blur(5px)}.leadership-hero :deep(.report-period-picker__label),.leadership-hero .report-query label>span{margin-bottom:5px;font-size:var(--font-auxiliary)}.leadership-hero :deep(.report-period-picker__trigger),.leadership-hero :deep(.app-select__trigger),.leadership-hero .report-query>button{height:36px;min-height:36px;border-radius:6px;font-size:var(--font-auxiliary)}.leadership-hero .report-query>button.primary{border-color:var(--theme-border-color);background:rgb(var(--info-color));box-shadow:var(--theme-shadow)}.leadership-kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr))}.leadership-kpi{--kpi-color:rgb(var(--info-color));--kpi-soft:rgb(var(--info-color) / .1);position:relative;min-width:0;border:1px solid var(--theme-border-color);border-top:2px solid var(--kpi-color);background:rgb(var(--container-bg-color));box-shadow:var(--theme-shadow);transition:transform .18s ease,box-shadow .18s ease;animation:surface-in .42s cubic-bezier(.2,.75,.25,1) both}.leadership-kpi:hover{transform:translateY(-2px);box-shadow:var(--theme-shadow)}.leadership-kpi:nth-child(2){animation-delay:.04s}.leadership-kpi:nth-child(3){animation-delay:.08s}.leadership-kpi:nth-child(4){animation-delay:.12s}.leadership-kpi.kpi-teal{--kpi-color:rgb(var(--info-color));--kpi-soft:rgb(var(--info-color) / .1)}.leadership-kpi.kpi-violet{--kpi-color:rgb(var(--primary-color));--kpi-soft:rgb(var(--primary-color) / .1)}.leadership-kpi.kpi-red{--kpi-color:var(--text-color-3);--kpi-soft:rgb(var(--info-color) / .1)}.leadership-kpi__icon{position:relative;z-index:1;display:grid;place-items:center;flex:0 0 auto;color:var(--kpi-color);border-radius:50%;background:var(--kpi-soft);transition:transform .2s ease}.leadership-kpi:hover .leadership-kpi__icon{transform:scale(1.08) rotate(-3deg)}.leadership-kpi>div{display:contents}.leadership-kpi>div>span{display:block;color:var(--text-color-1);font-size:var(--font-auxiliary);font-weight:700;white-space:normal;overflow-wrap:anywhere;grid-column:2}.leadership-kpi p{margin:0;color:var(--text-color-1)}.leadership-kpi p strong{margin:0}.leadership-kpi:nth-child(4) p strong{font-size:19px}.leadership-kpi p small{margin-left:3px;font-size:var(--font-auxiliary)}.leadership-kpi em{display:flex;align-items:center;gap:4px;color:rgb(var(--success-color));font-style:normal;font-size:var(--font-auxiliary);grid-column:1/-1;min-width:0;white-space:normal;overflow-wrap:anywhere;line-height:1.6;flex-wrap:wrap}.leadership-kpi em.down{color:rgb(var(--error-color))}.leadership-kpi em.flat{color:var(--muted)}.leadership-kpi em:not(.up):not(.down):not(.flat){color:var(--muted)}.leadership-kpi em small{margin-left:2px;color:var(--muted);font-size:inherit}.metric-sparkline{opacity:.78}.global-error{margin:12px 0 0;border-radius:9px}:deep(.report-trend-panel){margin-bottom:12px;border:1px solid var(--theme-border-color);box-shadow:var(--theme-shadow)}:deep(.report-trend-panel__heading){border-bottom:1px solid var(--theme-border-color);background:rgb(var(--container-bg-color))}:deep(.report-trend-panel__heading>div){gap:8px}:deep(.report-trend-panel__heading>div>span){width:28px;height:24px;display:grid;place-items:center;padding:0;color:rgb(var(--info-color));border:0;border-radius:7px;background:rgb(var(--info-color) / .1);font-size:var(--font-auxiliary);font-weight:700}:deep(.report-trend-panel__heading h2){color:var(--text-color-1);font-size:14px}:deep(.report-trend-panel__heading small){color:var(--muted);font-size:var(--font-auxiliary);font-weight:400}:deep(.report-trend-panel__heading p){color:var(--muted);font-size:var(--font-auxiliary)}:deep(.report-trend-panel__content){gap:10px}:deep(.report-trend-panel__chart),:deep(.report-trend-panel__table){overflow:hidden;border-color:var(--theme-border-color);border-radius:6px}:deep(table.content-table th),:deep(table.content-table td){height:35px!important;padding:6px 8px!important;font-size:var(--font-auxiliary)!important}:deep(table.content-table thead th){letter-spacing:.15px!important}:deep(table.content-table tfoot th){color:var(--text-color-1)!important;border-top:1px solid var(--theme-border-color)!important;background:rgb(var(--info-color) / .1)!important}
@media(max-width:1180px){.leadership-hero{grid-template-columns:1fr;grid-template-rows:auto auto;padding:24px}.leadership-hero__copy{grid-row:auto}.leadership-hero .report-query{grid-column:1;grid-row:auto}.leadership-kpis{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:720px){.leadership-hero{min-height:0;padding:22px 18px;background:linear-gradient(90deg,rgb(var(--container-bg-color) / 0.97),rgb(var(--container-bg-color) / 0.89)),var(--leadership-hero-image) center/cover}.leadership-hero h1{font-size:var(--font-page-title-mobile)}.leadership-hero .report-query{grid-template-columns:1fr}.leadership-hero :deep(.report-period-picker){grid-column:1}.leadership-kpis{grid-template-columns:1fr}:deep(.report-trend-panel__content){grid-template-columns:1fr}}
@media(prefers-reduced-motion:reduce){.leadership-hero,.leadership-kpi{animation:none}.leadership-kpi{transition:none;display:grid;grid-template-columns:35px minmax(0,1fr);align-items:center;gap:8px 12px}}


/* Shared report-page density. Keep leadership metrics aligned with equipment and workshop reports. */
.leadership-kpis { gap:16px; margin:16px 0; }
.leadership-kpi { min-height:108px; padding:12px; border-radius:var(--report-panel-radius); display:grid; grid-template-columns:35px minmax(0,1fr); align-items:center; gap:8px 12px; }
.leadership-kpi__icon { width:35px; height:35px; }
.leadership-kpi p { font-size:var(--font-auxiliary); grid-column:1/-1; min-width:0; overflow-wrap:anywhere; }
.leadership-kpi p strong { font-size:21px; }
.metric-sparkline { width:58px; height:25px; grid-column:1/-1; justify-self:end; }
.report-trend-panel { margin-bottom:24px; }
:deep(.report-trend-panel) { border-radius:var(--report-panel-radius); margin-bottom:24px; }
:deep(.report-trend-panel__heading) { min-height:47px; padding:0 16px; }
:deep(.report-trend-panel__content) { gap:16px; }
:deep(table.content-table thead th) {
  color:var(--report-table-head-ink)!important;
  background:var(--report-table-head-bg)!important;
  font-size:var(--font-auxiliary)!important;
}
:deep(table.content-table tbody td),
:deep(table.content-table tbody th),
:deep(table.content-table tfoot th) { font-size:var(--font-auxiliary)!important; }
:deep(.report-trend-panel__table) {
  height:var(--report-trend-height)!important;
  overflow:hidden!important;
  scrollbar-gutter:auto;
}
@media(max-width:720px) {
  :deep(.report-trend-panel__content) { grid-template-columns:1fr; gap:16px; }
}
</style>
