<script setup>
import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { init, use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { computed, nextTick, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getLeadershipRange, getLeadershipReport } from '../api/reports'
import AnalysisPanel from '../components/AnalysisPanel.vue'
import MetricSparkline from '../components/MetricSparkline.vue'
import ReportFeedback from '../components/ReportFeedback.vue'
import ReportTrendPanel from '../components/ReportTrendPanel.vue'
import StandardReportQuery from '../components/StandardReportQuery.vue'
import AppIcon from '../components/AppIcon.vue'
import heroImage from '../assets/home-laser-cutting.webp'
import { recentTwelveMonthStart, reportToday } from '../api/reportDates.js'
import { formatChineseDate, formatChineseMonth } from '../utils/dateDisplay.js'
import { createReportChartGrid, REPORT_CHART_COLORS } from '../utils/reportChartTheme.js'
import { mergeOwnedQuery, queryChoice, queryDateRange, sameRouteQuery } from '../utils/reportRouteState.js'

defineProps({ exporting: Boolean })
const emit = defineEmits(['export'])

use([BarChart, LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

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
const report = ref(null)
const monthlyReport = ref(null)
const loading = ref(false)
const monthlyLoading = ref(false)
const refreshLoading = ref(false)
const error = ref('')
const dailyPanel = ref(null)
const monthlyPanel = ref(null)
const pageActive = ref(true)
let dailyChart = null
let monthlyChart = null
let refreshTimer = null
let refreshPollCount = 0
let requestController = null
let requestSequence = 0
let resizeListening = false
let routeStateReady = false
let restoringRouteState = false

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

function beginRequest() {
  requestController?.abort()
  requestController = new AbortController()
  return { sequence: ++requestSequence, signal: requestController.signal }
}

function requestIsCurrent(sequence, signal) {
  return pageActive.value && !signal.aborted && sequence === requestSequence
}

const dailyRecords = computed(() => report.value?.daily_records || [])
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

function roundNumber(value, digits = 1) {
  const factor = 10 ** digits
  return Math.round((Number(value || 0) + Number.EPSILON) * factor) / factor
}

function formatLength(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '--'
  return new Intl.NumberFormat('zh-CN', {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  }).format(Number(value || 0))
}

function formatDuration(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '--'
  const totalSeconds = Math.max(0, Math.round(Number(value || 0)))
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  return [hours, minutes, seconds].map((part) => String(part).padStart(2, '0')).join(':')
}

function changeRate(current, previous) {
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

function changeText(change) {
  return change ? `${change.value}%` : '暂无对比'
}

function comparisonLabel(change) {
  if (!change) return '当前统计区间'
  return defaultMonthlyView.value ? '较上月' : '较对比区间'
}

function formatSnapshotTime(value) {
  if (!value) return '尚未生成快照'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
    hour12: false, timeZone: 'Asia/Shanghai',
  }).format(new Date(value))
}

function applyChartType(series) {
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
    renderCharts({ monthly: false })
    scheduleRefreshPoll(true)
    if (syncRoute) await syncRouteState()
  } catch (exception) {
    if (exception.name !== 'AbortError' && sequence === requestSequence) error.value = exception.message
  } finally {
    if (sequence === requestSequence) {
      loading.value = false
      requestController = null
    }
  }
}

async function restoreRouteState() {
  if (route.name !== 'leadership-report') return false
  const range = queryDateRange(route.query)
  const nextChartType = queryChoice(route.query.chart, ['bar_line', 'bar', 'line', 'area'], 'bar_line')
  restoringRouteState = true
  chartType.value = nextChartType
  restoringRouteState = false

  if (range) {
    periodStart.value = range.start
    periodEnd.value = range.end
    if (!report.value || defaultMonthlyView.value || appliedPeriodStart.value !== range.start || appliedPeriodEnd.value !== range.end) {
      await loadReport(false, { syncRoute: false })
      return true
    }
    return false
  }

  periodStart.value = defaultPeriodStart
  periodEnd.value = today
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
    renderCharts()
    scheduleRefreshPoll(true)
  } catch (exception) {
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
  const requestedRange = `${appliedPeriodStart.value}:${appliedPeriodEnd.value}`
  const requestedDefaultView = defaultMonthlyView.value
  refreshTimer = window.setTimeout(async () => {
    if (!pageActive.value || requestedRange !== `${appliedPeriodStart.value}:${appliedPeriodEnd.value}` || requestedDefaultView !== defaultMonthlyView.value) {
      refreshLoading.value = false
      return
    }
    const { sequence, signal } = beginRequest()
    try {
      const result = requestedDefaultView
        ? await getLeadershipReport(appliedPeriodStart.value.slice(0, 7), { force: true, signal })
        : await getLeadershipRange(appliedPeriodStart.value, appliedPeriodEnd.value, { force: true, signal })
      if (!requestIsCurrent(sequence, signal)) return
      report.value = result
      if (requestedDefaultView) monthlyReport.value = result
      await nextTick()
      renderCharts({ monthly: requestedDefaultView })
      refreshPollCount += 1
      if (snapshot.value.refreshing && refreshPollCount < 75) {
        scheduleRefreshPoll()
      } else {
        refreshLoading.value = false
      }
    } catch (exception) {
      refreshLoading.value = false
      if (exception.name !== 'AbortError' && sequence === requestSequence) error.value = exception.message
    } finally {
      if (sequence === requestSequence) requestController = null
    }
  }, 2000)
}

function renderCharts({ monthly = true } = {}) {
  if (!pageActive.value) return
  const dailyChartElement = dailyPanel.value?.getChartElement()
  const monthlyChartElement = monthlyPanel.value?.getChartElement()
  if (dailyChartElement && report.value) {
    dailyChart ||= init(dailyChartElement)
    const records = [...dailyRecords.value].sort((left, right) => left.period_start.localeCompare(right.period_start))
    dailyChart.setOption({
      tooltip: { trigger: 'axis' },
      legend: { data: ['切割长度', '坡口长度', '切割工时'] },
      grid: createReportChartGrid(),
      xAxis: { type: 'category', data: records.map((row) => row.period_start.slice(5)), axisLabel: { hideOverlap: true } },
      yAxis: [
        { type: 'value', name: '长度（米）', splitLine: { lineStyle: { color: '#edf1f4' } } },
        { type: 'value', name: '工时（小时）', splitLine: { show: false } },
      ],
      series: [
        { name: '切割长度', type: 'bar', data: records.map((row) => roundNumber(row.cut_length_m)), tooltip: { valueFormatter: (value) => `${formatLength(value)} 米` }, itemStyle: { color: REPORT_CHART_COLORS.primary } },
        { name: '坡口长度', type: 'bar', data: records.map((row) => roundNumber(row.groove_length_m)), tooltip: { valueFormatter: (value) => `${formatLength(value)} 米` }, itemStyle: { color: REPORT_CHART_COLORS.secondary } },
        { name: '切割工时', type: 'line', yAxisIndex: 1, smooth: true, data: records.map((row) => roundNumber(Number(row.work_time_seconds) / 3600)), tooltip: { valueFormatter: (value) => `${formatLength(value)} 小时` }, itemStyle: { color: REPORT_CHART_COLORS.accent } },
      ].map(applyChartType),
    }, true)
  }
  if (monthly && monthlyChartElement && monthlyReport.value) {
    monthlyChart ||= init(monthlyChartElement)
    const records = [...monthlyRecords.value].sort((left, right) => left.period_start.localeCompare(right.period_start))
    monthlyChart.setOption({
      tooltip: { trigger: 'axis' },
      legend: { data: ['切割长度', '坡口长度', '完成任务'] },
      grid: createReportChartGrid(),
      xAxis: { type: 'category', data: records.map((row) => row.period_start.slice(0, 7)), axisLabel: { hideOverlap: true } },
      yAxis: [
        { type: 'value', name: '长度（米）', splitLine: { lineStyle: { color: '#edf1f4' } } },
        { type: 'value', name: '任务数', splitLine: { show: false } },
      ],
      series: [
        { name: '切割长度', type: 'bar', data: records.map((row) => roundNumber(row.cut_length_m)), tooltip: { valueFormatter: (value) => `${formatLength(value)} 米` }, itemStyle: { color: REPORT_CHART_COLORS.primary } },
        { name: '坡口长度', type: 'bar', data: records.map((row) => roundNumber(row.groove_length_m)), tooltip: { valueFormatter: (value) => `${formatLength(value)} 米` }, itemStyle: { color: REPORT_CHART_COLORS.secondary } },
        { name: '完成任务', type: 'line', yAxisIndex: 1, smooth: true, data: records.map((row) => row.task_count), itemStyle: { color: REPORT_CHART_COLORS.accent } },
      ].map(applyChartType),
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
}

function stopResizeListening() {
  if (!resizeListening) return
  resizeListening = false
  window.removeEventListener('resize', resizeCharts)
}

function pausePage(dispose = false) {
  if (pageActive.value) {
    pageActive.value = false
    requestSequence += 1
    requestController?.abort()
    requestController = null
    loading.value = false
    monthlyLoading.value = false
    refreshLoading.value = false
    stopResizeListening()
    stopRefreshPoll()
  }
  if (dispose) disposeCharts()
}

function exportReport(format = 'html') {
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
    renderCharts()
    scheduleRefreshPoll(true)
  } catch (exception) {
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
  await loadDefaultReport()
  if (routeRange && pageActive.value) await restoreRouteState()
  routeStateReady = true
})
onActivated(async () => {
  pageActive.value = true
  startResizeListening()
  if (routeStateReady && await restoreRouteState()) return
  await nextTick()
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
  if (routeStateReady && !restoringRouteState && route.name === 'leadership-report') await syncRouteState()
})
watch(() => route.query, async () => {
  if (routeStateReady && pageActive.value && route.name === 'leadership-report') await restoreRouteState()
}, { deep: true })
</script>

<template>
  <section class="page-hero report-hero report-hero--query leadership-hero" :style="{ '--leadership-hero-image': `url(${heroImage})` }">
    <div class="report-hero-copy leadership-hero__copy">
      <span class="report-hero-eyebrow eyebrow">MANAGEMENT OVERVIEW</span>
      <h1>领导报表</h1>
      <p class="report-hero-description">按自定义日期区间查看任务、切割长度、坡口长度和切割时长趋势，为生产决策提供数据支持。</p>
      <p v-if="report" class="report-hero-meta report-source-note"><AppIcon name="calendar" size="14" />数据更新：{{ formatSnapshotTime(snapshot.data_as_of) }}<span>{{ snapshot.refreshing ? '正在后台更新' : snapshot.stale ? '快照待更新' : '数据快照有效' }}</span></p>
    </div>
    <p class="leadership-hero__slogan"><strong>数据驱动制造</strong><span>让生产更高效</span></p>
    <StandardReportQuery
      v-model:start-date="periodStart"
      v-model:end-date="periodEnd"
      v-model:chart-type="chartType"
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
      <span class="leadership-kpi__icon"><AppIcon name="tasks" size="22" tone="muted" /></span>
      <div><span>完成任务<small>COMPLETED TASKS</small></span><p><strong>{{ total.task_count ?? '--' }}</strong> 项</p><em :class="metricChanges.taskCount?.direction"><AppIcon v-if="metricChanges.taskCount" :name="metricChanges.taskCount.direction === 'down' ? 'arrow-down' : metricChanges.taskCount.direction === 'flat' ? 'activity' : 'arrow-up'" size="12" />{{ changeText(metricChanges.taskCount) }} <small>{{ comparisonLabel(metricChanges.taskCount) }}</small></em></div>
      <MetricSparkline class="metric-sparkline" :values="metricSeries.taskCount" label="每日完成任务趋势" />
    </article>
    <article class="leadership-kpi kpi-teal">
      <span class="leadership-kpi__icon"><AppIcon name="ruler" size="22" tone="muted" /></span>
      <div><span>切割长度<small>CUTTING LENGTH</small></span><p><strong>{{ formatLength(total.cut_length_m) }}</strong> 米</p><em :class="metricChanges.cutLength?.direction"><AppIcon v-if="metricChanges.cutLength" :name="metricChanges.cutLength.direction === 'down' ? 'arrow-down' : metricChanges.cutLength.direction === 'flat' ? 'activity' : 'arrow-up'" size="12" />{{ changeText(metricChanges.cutLength) }} <small>{{ comparisonLabel(metricChanges.cutLength) }}</small></em></div>
      <MetricSparkline class="metric-sparkline" :values="metricSeries.cutLength" label="每日切割长度趋势" />
    </article>
    <article class="leadership-kpi kpi-violet">
      <span class="leadership-kpi__icon"><AppIcon name="grid" size="22" tone="muted" /></span>
      <div><span>坡口长度<small>GROOVE LENGTH</small></span><p><strong>{{ formatLength(total.groove_length_m) }}</strong> 米</p><em :class="metricChanges.grooveLength?.direction"><AppIcon v-if="metricChanges.grooveLength" :name="metricChanges.grooveLength.direction === 'down' ? 'arrow-down' : metricChanges.grooveLength.direction === 'flat' ? 'activity' : 'arrow-up'" size="12" />{{ changeText(metricChanges.grooveLength) }} <small>{{ comparisonLabel(metricChanges.grooveLength) }}</small></em></div>
      <MetricSparkline class="metric-sparkline" :values="metricSeries.grooveLength" label="每日坡口长度趋势" />
    </article>
    <article class="leadership-kpi kpi-red">
      <span class="leadership-kpi__icon"><AppIcon name="clock" size="22" tone="muted" /></span>
      <div><span>切割时长<small>CUTTING HOURS</small></span><p><strong>{{ formatDuration(total.work_time_seconds) }}</strong> <small>HH:mm:ss</small></p><em :class="metricChanges.workTime?.direction"><AppIcon v-if="metricChanges.workTime" :name="metricChanges.workTime.direction === 'down' ? 'arrow-down' : metricChanges.workTime.direction === 'flat' ? 'activity' : 'arrow-up'" size="12" />{{ changeText(metricChanges.workTime) }} <small>{{ comparisonLabel(metricChanges.workTime) }}</small></em></div>
      <MetricSparkline class="metric-sparkline" :values="metricSeries.workTime" label="每日切割时长趋势" />
    </article>
  </section>

  <ReportTrendPanel ref="dailyPanel" number="01" title="区间每日明细与趋势" subtitle="查看所选时间段内每日任务与加工指标变化" chart-label="区间每日趋势图" :height="245" :loading="loading || (refreshLoading && !snapshot.data_as_of)" :has-data="dailyRecords.length > 0" :empty="Boolean(report) && !dailyRecords.length" :error="!report ? error : ''" loading-title="正在读取区间生产数据" loading-message="正在汇总任务、切割长度、坡口长度和切割时长。" empty-title="所选区间暂无已完成任务" empty-message="请调整日期范围，或等待生产数据完成后重新查询。" @retry="retryReport">
    <template #table>
        <table v-content-table="pageActive" class="leadership-table summary-table">
        <thead><tr><th>序号</th><th>日期</th><th>任务数</th><th>切割长度</th><th>坡口长度</th><th>切割时长</th><th>备注</th></tr></thead>
        <tbody>
          <tr v-for="(row, index) in dailyRecords" :key="row.period_start">
            <td>{{ index + 1 }}</td><td>{{ formatChineseDate(row.period_start) }}</td><td>{{ row.task_count }}</td>
            <td>{{ formatLength(row.cut_length_m) }}</td><td>{{ formatLength(row.groove_length_m) }}</td>
            <td class="duration-cell">{{ formatDuration(row.work_time_seconds) }}</td><td>{{ row.remark || '--' }}</td>
          </tr>
          <tr v-if="refreshLoading && !snapshot.data_as_of"><td colspan="7" class="table-empty">首次快照正在后台生成</td></tr>
          <tr v-else-if="!loading && !dailyRecords.length"><td colspan="7" class="table-empty">所选区间暂无已完成任务</td></tr>
        </tbody>
        <tfoot>
          <tr><th aria-hidden="true"></th><th>汇总</th><th>{{ total.task_count }}</th><th>{{ formatLength(total.cut_length_m) }}</th><th>{{ formatLength(total.groove_length_m) }}</th><th class="duration-cell">{{ formatDuration(total.work_time_seconds) }}</th><th>--</th></tr>
        </tfoot>
        </table>
    </template>
  </ReportTrendPanel>

  <ReportTrendPanel ref="monthlyPanel" number="02" :title="monthlyTitle" subtitle="查看近12个月各项数据的对比趋势" :note="`${monthlyPeriodStart} 至 ${monthlyPeriodEnd}`" chart-label="近12个月月度趋势图" :height="245" :loading="monthlyLoading" :has-data="monthlyRecords.length > 0" :empty="Boolean(monthlyReport) && !monthlyRecords.length" :error="!monthlyReport ? error : ''" loading-title="正在读取近12个月趋势" loading-message="月度汇总独立加载，不影响当前区间明细。" empty-title="近12个月暂无已完成任务" empty-message="当前统计范围内还没有可汇总的生产任务。" @retry="loadDefaultReport">
    <template #table>
        <table v-content-table="pageActive" class="leadership-table summary-table">
        <thead><tr><th>序号</th><th>月份</th><th>任务数</th><th>切割长度</th><th>坡口长度</th><th>切割时长</th><th>备注</th></tr></thead>
        <tbody>
          <tr v-for="(row, index) in monthlyRecords" :key="row.period_start">
            <td>{{ index + 1 }}</td><td>{{ formatChineseMonth(row.period_start) }}</td><td>{{ row.task_count }}</td>
            <td>{{ formatLength(row.cut_length_m) }}</td><td>{{ formatLength(row.groove_length_m) }}</td>
            <td class="duration-cell">{{ formatDuration(row.work_time_seconds) }}</td><td>{{ row.remark || '--' }}</td>
          </tr>
          <tr v-if="monthlyLoading"><td colspan="7" class="table-empty">近12个月趋势正在加载</td></tr>
          <tr v-else-if="!monthlyRecords.length"><td colspan="7" class="table-empty">近12个月暂无已完成任务</td></tr>
        </tbody>
        <tfoot>
          <tr><th aria-hidden="true"></th><th>汇总</th><th>{{ monthlyTotal.task_count }}</th><th>{{ formatLength(monthlyTotal.cut_length_m) }}</th><th>{{ formatLength(monthlyTotal.groove_length_m) }}</th><th class="duration-cell">{{ formatDuration(monthlyTotal.work_time_seconds) }}</th><th>--</th></tr>
        </tfoot>
        </table>
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
.leadership-hero{position:relative;min-height:206px;display:grid;grid-template-columns:minmax(360px,.72fr) minmax(660px,1.28fr);grid-template-rows:1fr auto;align-items:center;gap:10px 28px;overflow:hidden;padding:24px 30px 16px;border:1px solid rgba(255,255,255,.72);border-radius:14px;background:linear-gradient(90deg,rgba(246,252,255,.98) 0%,rgba(238,248,253,.91) 39%,rgba(7,48,76,.04) 72%),var(--leadership-hero-image) center 48%/cover no-repeat;box-shadow:0 12px 30px rgba(22,62,89,.07);animation:surface-in .46s cubic-bezier(.2,.75,.25,1) both}.leadership-hero::after{content:"";position:absolute;inset:0;pointer-events:none;background:linear-gradient(116deg,transparent 0 46%,rgba(255,255,255,.26) 46.2%,transparent 46.8%)}.leadership-hero__copy,.leadership-hero__slogan,.leadership-hero .report-query{position:relative;z-index:1}.leadership-hero__copy{grid-row:1/3;align-self:center}.leadership-hero .eyebrow{display:block;margin:0 0 5px;color:#237fb7;font-size:10px;font-weight:700;letter-spacing:1.4px}.leadership-hero h1{margin:0 0 7px;color:#153f5d;font-size:38px;line-height:1.08;letter-spacing:1px}.leadership-hero__copy>p:not(.report-source-note){max-width:510px;margin:0;color:#607a8d;font-size:12px;line-height:1.7}.leadership-hero .report-source-note{display:flex;align-items:center;gap:7px;margin:12px 0 0;color:#47708b;font-size:9px}.leadership-hero .report-source-note>span{padding-left:8px;border-left:1px solid #c8d8e1;color:#7b90a0}.leadership-hero__slogan{grid-column:2;align-self:end;justify-self:end;margin:0 14px 1px 0;color:#effaff;text-align:right;text-shadow:0 2px 10px rgba(4,37,58,.8);transform:rotate(-6deg)}.leadership-hero__slogan strong,.leadership-hero__slogan span{display:block}.leadership-hero__slogan strong{font-size:17px;letter-spacing:4px}.leadership-hero__slogan span{margin-top:5px;font-size:12px;letter-spacing:3px}.leadership-hero .report-query{grid-column:2;grid-row:2;width:100%;grid-template-columns:minmax(145px,1fr) minmax(145px,1fr) 120px 118px 126px;gap:8px;padding:11px 12px;border-color:rgba(66,128,157,.16);border-radius:9px;background:rgba(255,255,255,.88);box-shadow:0 10px 26px rgba(22,62,89,.1);backdrop-filter:blur(5px)}.leadership-hero :deep(.report-period-picker__label),.leadership-hero .report-query label>span{margin-bottom:5px;font-size:9px}.leadership-hero :deep(.report-period-picker__trigger),.leadership-hero :deep(.app-select__trigger),.leadership-hero .report-query>button{height:36px;min-height:36px;border-radius:6px;font-size:10px}.leadership-hero .report-query>button.primary{border-color:#1179ab;background:linear-gradient(135deg,#0e688f,#0584af);box-shadow:0 5px 12px rgba(15,111,158,.18)}.leadership-kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:12px 0}.leadership-kpi{--kpi-color:#237fb7;--kpi-soft:#eaf5fb;position:relative;min-width:0;min-height:112px;display:flex;gap:12px;overflow:hidden;padding:15px 14px;border:1px solid #dbe6ec;border-top:2px solid var(--kpi-color);border-radius:11px;background:#fff;box-shadow:0 7px 22px rgba(22,62,89,.055);transition:transform .18s ease,box-shadow .18s ease;animation:surface-in .42s cubic-bezier(.2,.75,.25,1) both}.leadership-kpi:hover{transform:translateY(-2px);box-shadow:0 11px 26px rgba(22,62,89,.09)}.leadership-kpi:nth-child(2){animation-delay:.04s}.leadership-kpi:nth-child(3){animation-delay:.08s}.leadership-kpi:nth-child(4){animation-delay:.12s}.leadership-kpi.kpi-teal{--kpi-color:#16a39d;--kpi-soft:#e8f7f5}.leadership-kpi.kpi-violet{--kpi-color:#7366db;--kpi-soft:#efedff}.leadership-kpi.kpi-red{--kpi-color:#617c90;--kpi-soft:#edf3f6}.leadership-kpi__icon{position:relative;z-index:1;width:36px;height:36px;display:grid;place-items:center;flex:0 0 auto;color:var(--kpi-color);border-radius:50%;background:var(--kpi-soft);transition:transform .2s ease}.leadership-kpi:hover .leadership-kpi__icon{transform:scale(1.08) rotate(-3deg)}.leadership-kpi>div{position:relative;z-index:1;min-width:0;flex:1}.leadership-kpi>div>span{display:block;color:#284a62;font-size:11px;font-weight:700;white-space:nowrap}.leadership-kpi>div>span small{display:block;margin:3px 0 0;color:var(--muted);font-size:8px;font-weight:500;letter-spacing:.6px}.leadership-kpi p{margin:8px 0 6px;color:#173f64;font-size:10px;white-space:nowrap}.leadership-kpi p strong{margin:0;font-size:23px}.leadership-kpi:nth-child(4) p strong{font-size:19px}.leadership-kpi p small{margin-left:3px;font-size:8px}.leadership-kpi em{display:flex;align-items:center;gap:3px;color:#2aa473;font-style:normal;font-size:9px;white-space:nowrap}.leadership-kpi em.down{color:#df5961}.leadership-kpi em.flat{color:var(--muted)}.leadership-kpi em:not(.up):not(.down):not(.flat){color:var(--muted)}.leadership-kpi em small{margin-left:2px;color:var(--muted)}.metric-sparkline{position:absolute;right:10px;bottom:13px;width:72px;height:28px;opacity:.78}.leadership-kpi::after{content:"";position:absolute;width:76px;height:76px;right:-27px;bottom:-43px;border-radius:50%;background:var(--kpi-soft);opacity:.72}.global-error{margin:12px 0 0;border-radius:9px}:deep(.report-trend-panel){margin-bottom:12px;border:1px solid #dbe6ec;border-radius:11px;box-shadow:0 7px 22px rgba(22,62,89,.05)}:deep(.report-trend-panel__heading){min-height:48px;padding:0 15px;border-bottom:1px solid #e5edf1;background:linear-gradient(90deg,#fff,#fbfdfe)}:deep(.report-trend-panel__heading>div){gap:8px}:deep(.report-trend-panel__heading>div>span){width:28px;height:24px;display:grid;place-items:center;padding:0;color:#237fb7;border:0;border-radius:7px;background:#eaf5fb;font-size:10px;font-weight:700}:deep(.report-trend-panel__heading h2){color:#193c57;font-size:14px}:deep(.report-trend-panel__heading small){color:var(--muted);font-size:9px;font-weight:400}:deep(.report-trend-panel__heading p){color:var(--muted);font-size:9px}:deep(.report-trend-panel__content){grid-template-columns:minmax(330px,.95fr) minmax(0,1fr);gap:10px;padding:11px 13px 13px}:deep(.report-trend-panel__chart),:deep(.report-trend-panel__table){overflow:hidden;border-color:#dfe9ee;border-radius:7px}:deep(table.content-table th),:deep(table.content-table td){height:35px!important;padding:6px 8px!important;font-size:10px!important}:deep(table.content-table thead th){color:#49677c!important;background:#eef5f9!important;font-size:9px!important;letter-spacing:.15px!important}:deep(table.content-table tfoot th){color:#174c70!important;border-top:1px solid #cbdfe8!important;background:#eaf4f9!important}:deep(.leadership-analysis){margin-bottom:0;overflow:hidden;border:1px solid #dbe6ec;border-radius:11px;box-shadow:0 7px 22px rgba(22,62,89,.05)}:deep(.leadership-analysis .analysis-panel-head){padding:12px 15px;background:linear-gradient(90deg,#fff,#fbfdfe)}:deep(.leadership-analysis .analysis-panel-icon){width:34px;height:34px}:deep(.leadership-analysis .analysis-kicker){font-size:8px}:deep(.leadership-analysis .analysis-panel-head h2){margin:2px 0 0;color:#193c57;font-size:15px}:deep(.leadership-analysis .analysis-actions button){min-height:33px;padding:0 12px;font-size:10px}:deep(.leadership-analysis .analysis-facts){gap:7px;padding:10px 13px 0}:deep(.leadership-analysis .analysis-facts article){padding:8px 10px}:deep(.leadership-analysis .analysis-facts span){font-size:9px}:deep(.leadership-analysis .analysis-facts strong){font-size:12px}:deep(.leadership-analysis .analysis-grid){grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;padding:10px 13px 13px}:deep(.leadership-analysis .analysis-grid article){min-height:88px;padding:11px 12px;border:0;border-radius:7px;background:#eff7fb}:deep(.leadership-analysis .analysis-grid article:nth-child(3)){background:#fff1f2}:deep(.leadership-analysis .analysis-grid article:nth-child(4)){background:#eef8f4}:deep(.leadership-analysis .analysis-grid h3){font-size:10px}:deep(.leadership-analysis .analysis-grid p){color:#60798b;font-size:9px;line-height:1.6;overflow-wrap:anywhere}:deep(.leadership-analysis .analysis-evidence){margin:0 13px 12px}:deep(.leadership-analysis .analysis-time){padding:0 13px 12px;font-size:9px}
@media(max-width:1180px){.leadership-hero{grid-template-columns:1fr;grid-template-rows:auto auto;padding:24px}.leadership-hero__copy{grid-row:auto}.leadership-hero__slogan{display:none}.leadership-hero .report-query{grid-column:1;grid-row:auto}.leadership-kpis{grid-template-columns:repeat(2,minmax(0,1fr))}:deep(.leadership-analysis .analysis-grid){grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:720px){.leadership-hero{min-height:0;padding:22px 18px;background:linear-gradient(90deg,rgba(246,252,255,.97),rgba(238,248,253,.89)),var(--leadership-hero-image) center/cover}.leadership-hero h1{font-size:31px}.leadership-hero .report-query{grid-template-columns:1fr}.leadership-hero :deep(.report-period-picker){grid-column:1}.leadership-kpis{grid-template-columns:1fr}.metric-sparkline{right:16px}:deep(.report-trend-panel__content){grid-template-columns:1fr}:deep(.leadership-analysis .analysis-grid){grid-template-columns:1fr}}
@media(prefers-reduced-motion:reduce){.leadership-hero,.leadership-kpi{animation:none}.leadership-kpi{transition:none}}
</style>

<style scoped>
/* Shared report-page density. Keep leadership metrics aligned with equipment and workshop reports. */
.leadership-kpis { gap:10px; margin:10px 0; }
.leadership-kpi { min-height:108px; gap:10px; padding:14px 12px; border-radius:var(--report-panel-radius); }
.leadership-kpi__icon { width:35px; height:35px; }
.leadership-kpi > div > span small { font-size:7px; letter-spacing:.45px; }
.leadership-kpi p { font-size:9px; }
.leadership-kpi p strong { font-size:21px; }
.metric-sparkline { right:8px; bottom:12px; width:58px; height:25px; }
.leadership-kpi::after { width:70px; height:70px; right:-28px; bottom:-41px; }
.report-trend-panel { margin-bottom:10px; }
:deep(.report-trend-panel) { border-radius:var(--report-panel-radius); }
:deep(.report-trend-panel__heading) { min-height:47px; padding:0 14px; }
:deep(.report-trend-panel__content) { grid-template-columns:minmax(330px,.9fr) minmax(0,1.25fr); }
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
:deep(.leadership-analysis) { border-radius:var(--report-panel-radius); }
@media(max-width:720px) {
  :deep(.report-trend-panel__content) { grid-template-columns:1fr; }
}
</style>
