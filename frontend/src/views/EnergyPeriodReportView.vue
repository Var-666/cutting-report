<script setup>
import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { init, use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { computed, nextTick, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  getEnergyDay,
  getEnergyMonthlyRange,
  getEnergyRange,
  getLatestWorkshopProductionDate,
  getTaskEnergyDay,
} from '../api/reports'
import { recentTwelveMonthStart, reportToday, taskProductionDate } from '../api/reportDates.js'
import AnalysisPanel from '../components/AnalysisPanel.vue'
import AppIcon from '../components/AppIcon.vue'
import MetricSparkline from '../components/MetricSparkline.vue'
import ReportFeedback from '../components/ReportFeedback.vue'
import ReportTrendPanel from '../components/ReportTrendPanel.vue'
import StandardReportQuery from '../components/StandardReportQuery.vue'
import { formatChineseDateTime, formatChineseMonth, sortReportPeriods } from '../utils/dateDisplay.js'
import { deviceSelection } from '../state/devices.js'
import { formatDeviceDisplay } from '../utils/deviceDisplay.js'
import { createReportChartGrid } from '../utils/reportChartTheme.js'
import { useReportChartTheme } from '../composables/useReportChartTheme.js'
import { useDeviceSwitchQuery } from '../composables/useDeviceSwitchQuery.js'
import { mergeOwnedQuery, queryChoice, queryDateRange, sameRouteQuery } from '../utils/reportRouteState.js'

use([BarChart, LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])
defineProps({ exporting: Boolean })
const emit = defineEmits(['export'])

const chartTheme = useReportChartTheme(() => { disposeCharts(); renderCharts() })
const route = useRoute()
const router = useRouter()
const today = reportToday()
const anchorDate = ref(today)
const periodStart = ref(today)
const periodEnd = ref(today)
const appliedPeriodStart = ref(today)
const appliedPeriodEnd = ref(today)
const summaryPeriodStart = ref(recentTwelveMonthStart(today))
const summaryPeriodEnd = ref(today)
const rangeQueryApplied = ref(false)
const taskDay = ref(null)
const monthlyReport = ref(null)
const chartType = ref(queryChoice(route.query.chart, ['bar_line', 'bar', 'line'], 'bar_line'))
const energyChartOptions = [
  { value: 'bar_line', label: '柱线组合' },
  { value: 'bar', label: '双柱状图' },
  { value: 'line', label: '双折线图' },
]
const loading = ref(false)
const summaryLoading = ref(false)
const error = ref('')
const taskPanel = ref(null)
const periodPanel = ref(null)
const pageActive = ref(true)
let taskChart = null
let periodChart = null
let controller = null
let backgroundController = null
let refreshController = null
let requestSequence = 0
let refreshRequestSequence = 0
let summaryRefreshTimer = null
let summaryRefreshCount = 0
let resizeListening = false
let routeStateReady = false
let restoringRouteState = false

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

function beginRequest({ preserveSummary = false } = {}) {
  controller?.abort()
  if (!preserveSummary) {
    backgroundController?.abort()
    backgroundController = null
    refreshRequestSequence += 1
    refreshController?.abort()
    refreshController = null
  }
  controller = new AbortController()
  return { sequence: ++requestSequence, signal: controller.signal }
}

function requestIsCurrent(sequence, signal) {
  return pageActive.value && !signal.aborted && sequence === requestSequence
}

const taskRows = computed(() => taskDay.value?.records || [])
const taskChartRows = computed(() => taskRows.value
  .slice(0, 50)
  .sort((left, right) => String(left.production_time || '').localeCompare(String(right.production_time || ''))))
const summaryRows = computed(() => sortReportPeriods(monthlyReport.value?.rows || [], 'desc'))
const summaryChartRows = computed(() => sortReportPeriods(monthlyReport.value?.rows || []))
const month = computed(() => monthlyReport.value)
const units = computed(() => taskDay.value?.units || { electricity: 'kWh', gas: 'Nm³' })
const summary = computed(() => taskDay.value?.summary || {
  task_count: null,
  total_work_time: null,
  total_gas_consumption: null,
  total_electricity_consumption: null,
  gas_record_count: 0,
  electricity_record_count: 0,
})
const equipmentScopeLabel = computed(() => formatDeviceDisplay(taskDay.value?.equipment_scope?.join(' / ') || deviceSelection.id))
const energyConnected = computed(() => taskDay.value?.energy_connected !== false)
const configurationError = computed(() => taskDay.value?.configuration_error || '')
const energyCoverage = computed(() => {
  const taskCount = Number(summary.value.task_count || 0)
  if (!taskCount) return 0
  const matchedRecords = Number(summary.value.electricity_record_count || 0)
    + Number(summary.value.gas_record_count || 0)
  return Math.max(0, Math.min(100, Math.round((matchedRecords / (taskCount * 2)) * 100)))
})
const deviceMetricSeries = computed(() => {
  const rows = [...taskRows.value].sort((left, right) => String(left.production_time || '').localeCompare(String(right.production_time || '')))
  const countsByDay = new Map()
  for (const row of rows) {
    const stamp = taskProductionDate(row)
    if (stamp) countsByDay.set(stamp, (countsByDay.get(stamp) || 0) + 1)
  }
  return {
    electricity: rows.map((row) => row.electricity_consumption),
    gas: rows.map((row) => row.gas_consumption),
    coverage: rows.map((row) => (
      (row.electricity_consumption == null ? 0 : 50)
      + (row.gas_consumption == null ? 0 : 50)
    )),
    tasks: [...countsByDay.values()],
    workTime: rows.map((row) => row.work_time),
  }
})
const summaryTitle = '近12个月月度能耗汇总'
const summaryCountLabel = computed(() => `${summaryRows.value.length} 个月`)
const summaryDateHeading = '月份'
const summaryChartLabel = '月度能耗图'
const summaryTableLabel = '近12个月月度能耗汇总表'
const summaryEmptyText = '近12个月暂无月度汇总'
const summarySubtitle = computed(() => {
  const base = `${summaryPeriodStart.value} 至 ${summaryPeriodEnd.value} · ${summaryCountLabel.value}`
  if (!energyConnected.value) return `${base} · 能耗暂未接入`
  const missing = Number(monthlyReport.value?.missing_day_count || 0)
  if (!missing) return base
  return monthlyReport.value?.syncing
    ? `${base} · 后台补齐 ${missing} 天能耗中`
    : `${base} · ${missing} 天暂无能耗数据`
})
const analysisReady = computed(() => Boolean(
  pageActive.value
  && taskDay.value
  && monthlyReport.value?.analysis_source_version
  && taskDay.value.period_start === appliedPeriodStart.value
  && taskDay.value.period_end === appliedPeriodEnd.value
  && !loading.value
  && !summaryLoading.value
))

function format(value, digits = 1) {
  if (!energyConnected.value && value == null) return '暂未接入'
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '--'
  return new Intl.NumberFormat('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(Number(value))
}

function duration(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '--'
  const seconds = Math.max(0, Math.round(Number(value)))
  return [Math.floor(seconds / 3600), Math.floor(seconds % 3600 / 60), seconds % 60]
    .map((item) => String(item).padStart(2, '0')).join(':')
}

function valueText(item) { return format(item?.value) }
function summaryPeriod(value) { return formatChineseMonth(value) }
function chartValue(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return null
  return Number(Number(value).toFixed(1))
}

function chartOption(rows, labels) {
  const line = chartType.value === 'line'
  return {
    backgroundColor: 'transparent',
      tooltip: { trigger: 'axis', backgroundColor: chartTheme.vars.value.popoverColor, borderColor: chartTheme.vars.value.borderColor, textStyle: { color: chartTheme.vars.value.textColor1 } },
    legend: { data: ['电耗', '气耗'] },
    grid: createReportChartGrid(),
    xAxis: { type: 'category', data: labels, axisLabel: { hideOverlap: true } },
    yAxis: [
      { type: 'value', name: `电耗\n${units.value.electricity}`, splitLine: { lineStyle: { color: chartTheme.vars.value.dividerColor } } },
      { type: 'value', name: `气耗\n${units.value.gas}`, splitLine: { show: false } },
    ],
    series: [
      { name: '电耗', type: line ? 'line' : 'bar', smooth: line, yAxisIndex: 0, data: rows.map((row) => chartValue(row.electricity)), tooltip: { valueFormatter: (value) => format(value) }, itemStyle: { color: chartTheme.colors.value.primary } },
      { name: '气耗', type: chartType.value === 'bar' ? 'bar' : 'line', smooth: chartType.value !== 'bar', yAxisIndex: 1, data: rows.map((row) => chartValue(row.gas)), tooltip: { valueFormatter: (value) => format(value) }, itemStyle: { color: chartType.value === 'bar_line' ? chartTheme.colors.value.accent : chartTheme.colors.value.secondary } },
    ],
  }
}

function renderCharts({ summary: renderSummary = true } = {}) {
  if (!pageActive.value) return
  const taskChartElement = taskPanel.value?.getChartElement()
  const periodChartElement = periodPanel.value?.getChartElement()
  if (taskChartElement && taskDay.value) {
    taskChart ||= init(taskChartElement, chartTheme.darkMode.value ? 'dark' : undefined)
    const rows = taskChartRows.value.map((row) => ({ electricity: row.electricity_consumption, gas: row.gas_consumption }))
    const labels = taskChartRows.value.map((row) => `${String(row.production_time || '').slice(5, 16).replace('T', ' ')} ${row.segment_name || ''}`)
    taskChart.setOption(chartOption(rows, labels), true)
  }
  if (renderSummary && periodChartElement && monthlyReport.value) {
    periodChart ||= init(periodChartElement, chartTheme.darkMode.value ? 'dark' : undefined)
    const rows = summaryChartRows.value.map((row) => ({ electricity: row.electricity?.value, gas: row.gas?.value }))
    const labels = summaryChartRows.value.map((row) => row.report_date)
    periodChart.setOption(chartOption(rows, labels), true)
  }
}

async function loadRange(force = false, { syncRoute = true } = {}) {
  if (!periodStart.value || !periodEnd.value) return
  if (periodEnd.value < periodStart.value) {
    error.value = '结束日期不能早于开始日期'
    return
  }
  const { sequence, signal } = beginRequest({ preserveSummary: true })
  loading.value = true
  error.value = ''
  const requestedStart = periodStart.value
  const requestedEnd = periodEnd.value
  try {
    const result = await getEnergyRange(requestedStart, requestedEnd, { force, signal })
    if (!requestIsCurrent(sequence, signal)) return
    taskDay.value = result.task_report
    appliedPeriodStart.value = requestedStart
    appliedPeriodEnd.value = requestedEnd
    rangeQueryApplied.value = true
    await nextTick()
    renderCharts({ summary: false })
    if (syncRoute) await syncRouteState()
  } catch (exception) {
    if (exception.name !== 'AbortError' && sequence === requestSequence) error.value = exception.message
  } finally {
    if (sequence === requestSequence) {
      loading.value = false
      controller = null
    }
  }
}

function stopSummaryRefresh() {
  if (summaryRefreshTimer) window.clearTimeout(summaryRefreshTimer)
  summaryRefreshTimer = null
}

async function applyMonthlySummary(summaryResult) {
  if (!pageActive.value) return
  monthlyReport.value = summaryResult
  await nextTick()
  renderCharts()
  scheduleSummaryRefresh(summaryResult)
}

function scheduleSummaryRefresh(summaryResult) {
  stopSummaryRefresh()
  if (!pageActive.value || !summaryResult?.syncing
      || !summaryResult?.missing_day_count || summaryRefreshCount >= 600) return
  const requestedStart = summaryPeriodStart.value
  const requestedEnd = summaryPeriodEnd.value
  summaryRefreshTimer = window.setTimeout(async () => {
    summaryRefreshTimer = null
    if (!pageActive.value
        || requestedStart !== summaryPeriodStart.value
        || requestedEnd !== summaryPeriodEnd.value) return
    summaryRefreshCount += 1
    refreshController?.abort()
    refreshController = new AbortController()
    const signal = refreshController.signal
    const sequence = ++refreshRequestSequence
    try {
      const refreshed = await getEnergyMonthlyRange(requestedStart, requestedEnd, { force: true, signal })
      if (!pageActive.value || signal.aborted || sequence !== refreshRequestSequence) return
      await applyMonthlySummary(refreshed)
    } catch (exception) {
      if (exception.name !== 'AbortError' && pageActive.value) scheduleSummaryRefresh(summaryResult)
    } finally {
      if (sequence === refreshRequestSequence) refreshController = null
    }
  }, 1000)
}

async function loadDefaultRanges(force = false) {
  stopSummaryRefresh()
  summaryRefreshCount = 0
  const { sequence, signal } = beginRequest()
  loading.value = true
  summaryLoading.value = true
  error.value = ''
  const selectedDate = periodStart.value
  const recentStart = recentTwelveMonthStart(selectedDate)
  appliedPeriodStart.value = selectedDate
  appliedPeriodEnd.value = selectedDate
  summaryPeriodStart.value = recentStart
  summaryPeriodEnd.value = selectedDate
  rangeQueryApplied.value = false

  const showError = (exception) => {
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
      renderCharts()
    })
    .catch(showError)
  const summaryRequest = getEnergyMonthlyRange(recentStart, selectedDate, { force, signal })
    .then(async (summaryResult) => {
      if (!requestIsCurrent(sequence, signal)) return
      await applyMonthlySummary(summaryResult)
    })
    .catch(showError)
    .finally(() => {
      if (sequence === requestSequence) summaryLoading.value = false
    })

  // Daily meter synchronization is independent from first paint. Existing
  // monthly snapshots render first; the current month refreshes silently once
  // the live meter request finishes.
  backgroundController = new AbortController()
  const syncController = backgroundController
  const syncSignal = syncController.signal
  getEnergyDay(selectedDate, { signal: syncSignal })
    .then(() => getEnergyMonthlyRange(recentStart, selectedDate, { signal: syncSignal }))
    .then(async (summaryResult) => {
      if (!pageActive.value || syncSignal.aborted) return
      await applyMonthlySummary(summaryResult)
    })
    .catch(() => {})
    .finally(() => {
      if (backgroundController === syncController) backgroundController = null
    })

  await Promise.allSettled([taskRequest, summaryRequest])
  if (requestIsCurrent(sequence, signal)) {
    loading.value = false
    controller = null
  }
}

function exportReport(format = 'html') {
  if (!taskDay.value || loading.value) return
  emit('export', {
    report_type: 'equipment',
    report_date: appliedPeriodStart.value,
    period_start: appliedPeriodStart.value,
    period_end: appliedPeriodEnd.value,
    project_code: null,
    ship_no: null,
    parameters: {
      chart_type: chartType.value,
      summary_period_start: summaryPeriodStart.value,
      summary_period_end: summaryPeriodEnd.value,
    },
  }, format)
}

function resizeCharts() { if (pageActive.value) { taskChart?.resize(); periodChart?.resize() } }

function disposeCharts() {
  taskChart?.dispose()
  periodChart?.dispose()
  taskChart = null
  periodChart = null
}

function startResizeListening() {
  if (resizeListening) return
  resizeListening = true
  window.addEventListener('resize', resizeCharts)
}

function stopResizeListening() {
  if (!resizeListening) return
  resizeListening = false
  window.removeEventListener('resize', resizeCharts)
}

function pause(dispose = false) {
  if (pageActive.value) {
    pageActive.value = false
    requestSequence += 1
    controller?.abort()
    controller = null
    backgroundController?.abort()
    backgroundController = null
    refreshRequestSequence += 1
    refreshController?.abort()
    refreshController = null
    loading.value = false
    summaryLoading.value = false
    stopSummaryRefresh()
    stopResizeListening()
  }
  if (dispose) disposeCharts()
}

async function initializeReport({ restoreQuery = false } = {}) {
  const requestedRange = restoreQuery ? queryDateRange(route.query, { maxDays: 31 }) : null
  const { sequence, signal } = beginRequest()
  loading.value = true
  try {
    const latest = await getLatestWorkshopProductionDate(today, { signal })
    if (!requestIsCurrent(sequence, signal)) return
    if (latest.report_date) {
      anchorDate.value = latest.report_date
      periodStart.value = latest.report_date
      periodEnd.value = latest.report_date
    }
  } catch (exception) {
    if (exception.name !== 'AbortError' && sequence === requestSequence) error.value = exception.message
  }
  if (!requestIsCurrent(sequence, signal)) return
  await loadDefaultRanges()
  if (requestedRange && pageActive.value) {
    periodStart.value = requestedRange.start
    periodEnd.value = requestedRange.end
    await loadRange(false, { syncRoute: false })
  }
}

async function restoreRouteState() {
  if (route.name !== 'equipment-report') return false
  const range = queryDateRange(route.query, { maxDays: 31 })
  const nextChartType = queryChoice(route.query.chart, ['bar_line', 'bar', 'line'], 'bar_line')
  restoringRouteState = true
  chartType.value = nextChartType
  restoringRouteState = false

  if (range) {
    periodStart.value = range.start
    periodEnd.value = range.end
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
  periodStart.value = appliedPeriodStart.value
  periodEnd.value = appliedPeriodEnd.value
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
    renderCharts()
  } catch (exception) {
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
  await initializeReport({ restoreQuery: true })
  routeStateReady = true
})
onActivated(async () => {
  pageActive.value = true
  startResizeListening()
  if (routeStateReady && await restoreRouteState()) return
  await nextTick()
  if ((!taskDay.value || !monthlyReport.value) && !loading.value) await initializeReport()
  else if (!taskChart || (monthlyReport.value && !periodChart)) renderCharts()
  resizeCharts()
})
onDeactivated(pause)
onBeforeUnmount(() => pause(true))
watch(chartType, async () => {
  await nextTick()
  renderCharts()
  if (routeStateReady && !restoringRouteState && route.name === 'equipment-report') await syncRouteState()
})
watch(() => route.query, async () => {
  if (routeStateReady && pageActive.value && route.name === 'equipment-report') await restoreRouteState()
}, { deep: true })
</script>

<template>
  <div class="device-report-shell">
    <section class="page-hero report-hero report-hero--query equipment-hero">
      <div class="report-hero-copy device-hero__copy">
        <span class="report-hero-eyebrow">EQUIPMENT INTELLIGENCE</span>
        <h2>设备报表</h2>
      </div>
      <StandardReportQuery
        v-model:start-date="periodStart"
        v-model:end-date="periodEnd"
        v-model:chart-type="chartType"
        class="report-hero-query device-query-shell"
        aria-label="设备报表查询条件"
        :anchor-date="anchorDate"
        :max-days="31"
        :chart-options="energyChartOptions"
        :loading="loading"
        :has-data="Boolean(taskDay)"
        :date-dirty="dateQueryDirty"
        :export-disabled="loading || !taskDay"
        :exporting="exporting"
        @submit="loadRange(true)"
        @export="exportReport"
        @validation-error="error = $event"
      />
    </section>

    <ReportFeedback v-if="error && taskDay && monthlyReport" type="error" title="设备报表更新失败" :message="error" action-label="重新查询" dismissible @action="retryReport" @dismiss="error = ''" />
    <ReportFeedback v-if="!energyConnected" type="info" title="能耗暂未接入" message="当前设备暂未接入电表和气表，生产任务与工时正常统计。" />
    <ReportFeedback v-else-if="configurationError" type="warning" title="能管数据提示" :message="`${configurationError}。缺失值显示 --，不按零计算。`" />

    <section class="device-kpis" aria-label="设备区间汇总" :aria-busy="loading">
      <article class="device-kpi kpi-red">
        <span class="device-kpi__icon"><AppIcon name="zap" size="21" tone="current" /></span>
        <div><span>电耗<small>POWER CONSUMPTION</small></span><p><strong>{{ format(summary.total_electricity_consumption) }}</strong> <small>{{ units.electricity }}</small></p><em>区间合计</em></div>
        <MetricSparkline class="device-sparkline" :values="deviceMetricSeries.electricity" label="任务电耗趋势" />
      </article>
      <article class="device-kpi kpi-blue">
        <span class="device-kpi__icon"><AppIcon name="wind" size="21" tone="current" /></span>
        <div><span>气耗<small>GAS CONSUMPTION</small></span><p><strong>{{ format(summary.total_gas_consumption) }}</strong> <small>{{ units.gas }}</small></p><em>区间合计</em></div>
        <MetricSparkline class="device-sparkline" :values="deviceMetricSeries.gas" label="任务气耗趋势" />
      </article>
      <article class="device-kpi kpi-teal">
        <span class="device-kpi__icon"><AppIcon name="activity" size="21" tone="current" /></span>
        <div><span>能耗覆盖率<small>ENERGY COVERAGE</small></span><p><strong>{{ taskDay ? energyCoverage : '--' }}</strong> <small>%</small></p><em>电 / 气数据匹配</em></div>
        <MetricSparkline class="device-sparkline" :values="deviceMetricSeries.coverage" label="单项任务能耗覆盖率趋势" />
      </article>
      <article class="device-kpi kpi-violet">
        <span class="device-kpi__icon"><AppIcon name="tasks" size="21" tone="current" /></span>
        <div><span>任务数量<small>TASK COUNT</small></span><p><strong>{{ taskDay ? (summary.task_count ?? '--') : '--' }}</strong> <small>项</small></p><em>{{ equipmentScopeLabel }}</em></div>
        <MetricSparkline class="device-sparkline" :values="deviceMetricSeries.tasks" label="每日任务数量趋势" />
      </article>
      <article class="device-kpi kpi-cyan">
        <span class="device-kpi__icon"><AppIcon name="clock" size="21" tone="current" /></span>
        <div><span>累计切割时长<small>TOTAL CUTTING TIME</small></span><p class="device-duration"><strong>{{ duration(summary.total_work_time) }}</strong></p><em>当前查询区间</em></div>
        <MetricSparkline class="device-sparkline" :values="deviceMetricSeries.workTime" label="单项任务切割时长趋势" />
      </article>
    </section>

    <ReportTrendPanel
      ref="taskPanel"
      number="01"
      title="区间任务能耗明细"
      :subtitle="`${appliedPeriodStart} 至 ${appliedPeriodEnd}`"
      chart-label="任务能耗图"
      :loading="loading"
      :has-data="taskRows.length > 0"
      :empty="Boolean(taskDay) && !taskRows.length"
      :error="!taskDay ? error : ''"
      empty-title="所选区间暂无任务"
      empty-message="当前统计区间没有可展示的设备任务，可调整日期后重新查询。"
      @retry="retryReport"
    >
    <template #table>
        <table v-content-table="pageActive" class="energy-period-table energy-task-table summary-table" aria-label="任务能耗明细表">
          <thead><tr><th scope="col">序号</th><th scope="col">工程号</th><th scope="col">船号</th><th scope="col">分段名</th><th scope="col">任务开始</th><th scope="col">切割时长</th><th scope="col">电耗</th><th scope="col">气耗</th></tr></thead>
          <tbody><tr v-for="(row, index) in taskRows" :key="row.task_id"><th scope="row">{{ index + 1 }}</th><td>{{ row.project_code || '--' }}</td><td>{{ row.ship_no || '--' }}</td><td>{{ row.segment_name || '--' }}</td><td>{{ formatChineseDateTime(row.production_time) }}</td><td>{{ duration(row.work_time) }}</td><td>{{ format(row.electricity_consumption) }}</td><td>{{ format(row.gas_consumption) }}</td></tr><tr v-if="!taskRows.length"><td colspan="8" class="energy-empty">{{ loading ? '正在查询…' : '所选区间暂无任务' }}</td></tr></tbody>
          <tfoot><tr><th aria-hidden="true"></th><th scope="row">汇总</th><th>{{ equipmentScopeLabel }}</th><th>{{ summary.task_count || 0 }} 项</th><th>--</th><th>{{ duration(summary.total_work_time) }}</th><th>{{ format(summary.total_electricity_consumption) }}</th><th>{{ format(summary.total_gas_consumption) }}</th></tr></tfoot>
        </table>
    </template>
    </ReportTrendPanel>

    <ReportTrendPanel
      ref="periodPanel"
      number="02"
      :title="summaryTitle"
      :subtitle="summarySubtitle"
      :chart-label="summaryChartLabel"
      :loading="summaryLoading"
      :has-data="summaryRows.length > 0"
      :empty="Boolean(monthlyReport) && !summaryRows.length"
      :error="!monthlyReport ? error : ''"
      :empty-title="summaryEmptyText"
      empty-message="当前月份范围没有可展示的能耗汇总。"
      @retry="initializeReport"
    >
    <template #table><table v-content-table="pageActive" class="leadership-table energy-month-table summary-table" :aria-label="summaryTableLabel"><thead><tr><th scope="col">序号</th><th scope="col">{{ summaryDateHeading }}</th><th scope="col">任务数</th><th scope="col">切割时长</th><th scope="col">电耗</th><th scope="col">气耗</th></tr></thead><tbody><tr v-for="(row, index) in summaryRows" :key="row.report_date"><th scope="row">{{ index + 1 }}</th><td>{{ summaryPeriod(row.report_date) }}</td><td>{{ row.task_count }}</td><td>{{ duration(row.work_time_seconds) }}</td><td>{{ valueText(row.electricity) }}</td><td>{{ valueText(row.gas) }}</td></tr><tr v-if="!summaryRows.length"><td colspan="6" class="energy-empty">{{ summaryLoading ? '正在查询…' : summaryEmptyText }}</td></tr></tbody><tfoot><tr><th aria-hidden="true"></th><th scope="row">汇总</th><th>{{ month?.total_task_count || 0 }}</th><th>{{ duration(month?.total_work_time_seconds) }}</th><th>{{ valueText(month?.summary?.electricity) }}</th><th>{{ valueText(month?.summary?.gas) }}</th></tr></tfoot></table></template>
    </ReportTrendPanel>

    <AnalysisPanel
      report-type="equipment"
      :report-date="appliedPeriodStart"
      :period-start="rangeQueryApplied ? appliedPeriodStart : ''"
      :period-end="rangeQueryApplied ? appliedPeriodEnd : ''"
      :summary-period-start="summaryPeriodStart"
      :summary-period-end="summaryPeriodEnd"
      :ready="analysisReady"
      :data-version="taskDay?.analysis_source_version || ''"
      :summary-data-version="monthlyReport?.analysis_source_version || ''"
      :version-check-enabled="!rangeQueryApplied"
      @refresh-report="refreshAnalysisReport"
    />
  </div>
</template>

<style scoped>
.device-report-shell>.page-hero{position:relative;min-height:174px;display:flex;align-items:center;overflow:hidden;padding:25px 30px;border:1px solid var(--theme-border-color);border-radius:14px;background:linear-gradient(90deg,rgb(var(--container-bg-color) / 0.99) 0%,rgb(var(--container-bg-color) / 0.92) 39%,rgb(var(--container-bg-color) / 0.08) 69%),url('../assets/home-laser-cutting.webp') center 51%/cover no-repeat;box-shadow:var(--theme-shadow);animation:surface-in .46s cubic-bezier(.2,.75,.25,1) both}.device-report-shell>.page-hero::after{content:"";position:absolute;inset:0;pointer-events:none;background:transparent}.device-hero__copy{position:relative;z-index:1;max-width:540px}.device-breadcrumb{display:block;margin-bottom:7px;color:rgb(var(--info-color));font-size:10px}.device-title-row{display:flex;align-items:flex-end;gap:14px}.device-title-row h2{margin:0;color:var(--text-color-1);font-size:38px;line-height:1.08;letter-spacing:1px}.device-title-row>span{padding-bottom:5px;color:rgb(var(--info-color));font-size:8px;font-weight:700;letter-spacing:3px}.device-query-shell{margin:10px 0;padding:11px 14px;border:1px solid var(--theme-border-color);border-radius:10px;background:rgb(var(--container-bg-color));box-shadow:var(--theme-shadow)}.device-query-shell .report-query{width:100%;grid-template-columns:minmax(320px,1fr) minmax(180px,.55fr) 128px 128px;padding:0;border:0;background:transparent;box-shadow:none}.device-query-shell :deep(.report-period-picker){grid-column:auto}.device-query-shell :deep(.report-period-picker__trigger),.device-query-shell :deep(.app-select__trigger),.device-query-shell .report-query>button{height:38px;min-height:38px;border-radius:6px;font-size:10px}.device-query-shell .report-query>button.primary{display:flex;align-items:center;justify-content:center;gap:6px;border-color:var(--theme-border-color);background:rgb(var(--info-color));box-shadow:var(--theme-shadow)}
.energy-config-note{display:grid;gap:6px;padding:12px 16px;margin-bottom:10px;border:1px solid var(--theme-border-color);border-radius:9px;background:rgb(var(--warning-color) / .1);color:rgb(var(--warning-color));font-size:11px}.device-kpis{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;margin:10px 0}.device-kpi{--kpi-color:rgb(var(--info-color));--kpi-soft:rgb(var(--info-color) / .1);position:relative;min-width:0;min-height:108px;display:flex;gap:10px;overflow:hidden;padding:14px 12px;border:1px solid var(--theme-border-color);border-top:2px solid var(--kpi-color);border-radius:10px;background:rgb(var(--container-bg-color));box-shadow:var(--theme-shadow);transition:transform .18s ease,box-shadow .18s ease;animation:surface-in .42s cubic-bezier(.2,.75,.25,1) both}.device-kpi:hover{transform:translateY(-2px);box-shadow:var(--theme-shadow)}.device-kpi:nth-child(2){animation-delay:.04s}.device-kpi:nth-child(3){animation-delay:.08s}.device-kpi:nth-child(4){animation-delay:.12s}.device-kpi:nth-child(5){animation-delay:.16s}.device-kpi.kpi-red{--kpi-color:var(--text-color-3);--kpi-soft:rgb(var(--info-color) / .1)}.device-kpi.kpi-teal{--kpi-color:rgb(var(--info-color));--kpi-soft:rgb(var(--info-color) / .1)}.device-kpi.kpi-violet{--kpi-color:rgb(var(--primary-color));--kpi-soft:rgb(var(--primary-color) / .1)}.device-kpi.kpi-cyan{--kpi-color:rgb(var(--info-color));--kpi-soft:rgb(var(--info-color) / .1)}.device-kpi__icon{position:relative;z-index:1;width:35px;height:35px;display:grid;place-items:center;flex:0 0 auto;color:var(--kpi-color);border-radius:50%;background:var(--kpi-soft);transition:transform .2s ease}.device-kpi:hover .device-kpi__icon{transform:scale(1.08) rotate(-3deg)}.device-kpi>div{position:relative;z-index:1;min-width:0;flex:1}.device-kpi>div>span{display:block;color:var(--text-color-1);font-size:11px;font-weight:700;white-space:normal;overflow-wrap:anywhere}.device-kpi>div>span small{display:block;margin:3px 0 0;color:var(--muted);font-size:7px;font-weight:500;letter-spacing:.45px}.device-kpi p{margin:8px 0 6px;color:var(--text-color-1);font-size:9px;white-space:nowrap}.device-kpi p strong{font-size:21px}.device-kpi p.device-duration strong{font-size:17px}.device-kpi p small{margin-left:2px;color:var(--muted);font-size:8px}.device-kpi em{display:block;overflow:hidden;color:var(--muted);text-overflow:ellipsis;white-space:nowrap;font-style:normal;font-size:8px}.device-sparkline{position:absolute;right:8px;bottom:12px;width:58px;height:25px;opacity:.72}.device-kpi::after{content:"";position:absolute;width:70px;height:70px;right:-28px;bottom:-41px;border-radius:50%;background:var(--kpi-soft);opacity:.72}.global-error{margin:10px 0 0;border-radius:9px}
.energy-period-table { width:100%; border-collapse:collapse; font-size:12px; }
.energy-period-table th,.energy-period-table td { height:47px; padding:10px; border-bottom:1px solid var(--theme-border-color); text-align:center; white-space:nowrap; }
.energy-task-table th:nth-child(2),.energy-task-table td:nth-child(2),.energy-task-table th:nth-child(3),.energy-task-table td:nth-child(3),.energy-task-table th:nth-child(4),.energy-task-table td:nth-child(4) { text-align:left; }
.energy-period-table thead th { position:sticky; top:0; z-index:2; color:var(--report-table-head-ink); background:var(--report-table-head-bg); }
.energy-period-table tfoot th { position:sticky; bottom:0; background:rgb(var(--info-color) / .1); color:var(--navy); }
.energy-task-table tfoot th:nth-child(2),.energy-task-table tfoot th:nth-child(3),.energy-task-table tfoot th:nth-child(4) { text-align:left; }
.energy-empty { height:120px; color:var(--text-color-3); }
:deep(.report-trend-panel){margin-bottom:10px;border:1px solid var(--theme-border-color);border-radius:10px;box-shadow:var(--theme-shadow)}:deep(.report-trend-panel__heading){min-height:47px;padding:0 14px;border-bottom:1px solid var(--theme-border-color);background:rgb(var(--container-bg-color))}:deep(.report-trend-panel__heading>div){gap:8px}:deep(.report-trend-panel__heading>div>span){width:28px;height:24px;display:grid;place-items:center;padding:0;color:rgb(var(--info-color));border:0;border-radius:7px;background:rgb(var(--info-color) / .1);font-size:10px;font-weight:700}:deep(.report-trend-panel__heading h2){color:var(--text-color-1);font-size:14px}:deep(.report-trend-panel__heading small){color:var(--muted);font-size:9px;font-weight:400}:deep(.report-trend-panel__heading p){color:var(--muted);font-size:9px}:deep(.report-trend-panel__content){grid-template-columns:minmax(330px,.9fr) minmax(0,1.25fr);gap:10px}:deep(.report-trend-panel__chart),:deep(.report-trend-panel__table){height:245px;overflow:hidden;border-color:var(--theme-border-color);border-radius:7px}:deep(table.content-table th),:deep(table.content-table td){height:35px!important;padding:6px 8px!important;font-size:10px!important}:deep(table.content-table thead th){color:var(--text-color-1)!important;background:rgb(var(--info-color))!important;font-size:9px!important;letter-spacing:.15px!important}:deep(table.content-table tfoot th){color:var(--text-color-1)!important;border-top:1px solid var(--theme-border-color)!important;background:rgb(var(--info-color) / .1)!important}:deep(.device-analysis){margin-bottom:0;overflow:hidden;border:1px solid var(--theme-border-color);border-radius:10px;box-shadow:var(--theme-shadow)}:deep(.device-analysis .analysis-panel-head){padding:12px 15px;background:rgb(var(--container-bg-color))}:deep(.device-analysis .analysis-panel-icon){width:34px;height:34px}:deep(.device-analysis .analysis-kicker){font-size:8px}:deep(.device-analysis .analysis-panel-head h2){margin:2px 0 0;color:var(--text-color-1);font-size:15px}:deep(.device-analysis .analysis-actions button){min-height:33px;padding:0 12px;font-size:10px}:deep(.device-analysis .analysis-facts){gap:7px;padding:10px 13px 0}:deep(.device-analysis .analysis-facts article){padding:8px 10px}:deep(.device-analysis .analysis-facts span){font-size:9px}:deep(.device-analysis .analysis-facts strong){font-size:12px}:deep(.device-analysis .analysis-grid){grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;padding:10px 13px 13px}:deep(.device-analysis .analysis-grid article){min-height:88px;padding:11px 12px;border:0;border-radius:7px;background:rgb(var(--info-color) / .1)}:deep(.device-analysis .analysis-grid article:nth-child(3)){background:rgb(var(--error-color) / .1)}:deep(.device-analysis .analysis-grid article:nth-child(4)){background:rgb(var(--success-color) / .1)}:deep(.device-analysis .analysis-grid h3){font-size:10px}:deep(.device-analysis .analysis-grid p){color:var(--text-color-3);font-size:9px;line-height:1.6;overflow-wrap:anywhere}:deep(.device-analysis .analysis-evidence){margin:0 13px 12px}:deep(.device-analysis .analysis-time){padding:0 13px 12px;font-size:9px}
@media(max-width:1120px){.device-kpis{grid-template-columns:repeat(2,minmax(0,1fr))}.device-kpi:last-child{grid-column:1/-1}:deep(.device-analysis .analysis-grid){grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:900px){.device-query-shell .report-query{grid-template-columns:repeat(2,minmax(180px,1fr))}.device-query-shell :deep(.report-period-picker){grid-column:1/-1}:deep(.report-trend-panel__content){grid-template-columns:1fr}:deep(.report-trend-panel__chart),:deep(.report-trend-panel__table){height:270px}}
@media(max-width:560px){.device-report-shell>.page-hero{min-height:0;padding:22px 18px;background:linear-gradient(90deg,rgb(var(--container-bg-color) / 0.97),rgb(var(--container-bg-color) / 0.89)),url('../assets/home-laser-cutting.webp') center/cover}.device-title-row{display:block}.device-title-row h2{font-size:31px}.device-title-row>span{display:block;padding:6px 0 0}.device-query-shell .report-query,.device-kpis{grid-template-columns:1fr}.device-query-shell :deep(.report-period-picker),.device-kpi:last-child{grid-column:auto}:deep(.device-analysis .analysis-grid){grid-template-columns:1fr}}
@media(prefers-reduced-motion:reduce){.device-report-shell>.page-hero,.device-kpi{animation:none}.device-kpi{transition:none}}
</style>

<style scoped>
/* Report tables use the same low-contrast header treatment across all report pages. */
.device-query-shell { position:relative; padding:12px 14px 13px; border-color:var(--theme-border-color); border-radius:12px; background:rgb(var(--container-bg-color)); box-shadow:var(--theme-shadow); }
.device-query-shell::before { content:""; position:absolute; top:-1px; left:18px; width:34px; height:3px; border-radius:0 0 999px 999px; background:rgb(var(--info-color)); }
.device-query-shell .standard-report-query { gap:10px; }
:deep(.report-trend-panel),
:deep(.device-analysis) { border-radius:var(--report-panel-radius); }
:deep(table.content-table thead th) {
  color:var(--report-table-head-ink)!important;
  background:var(--report-table-head-bg)!important;
  font-size:10px!important;
}
:deep(table.content-table tbody td),
:deep(table.content-table tbody th),
:deep(table.content-table tfoot th) { font-size:11px!important; }
:deep(.report-trend-panel__table) {
  overflow:auto!important;
  scrollbar-gutter:stable;
}
</style>
