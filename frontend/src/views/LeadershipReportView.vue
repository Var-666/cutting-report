<script setup>
import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { init, use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { computed, nextTick, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, watch } from 'vue'
import { getLeadershipRange, getLeadershipReport } from '../api/reports'
import AnalysisPanel from '../components/AnalysisPanel.vue'
import ReportExportMenu from '../components/ReportExportMenu.vue'
import ReportPeriodPicker from '../components/ReportPeriodPicker.vue'
import ReportTrendPanel from '../components/ReportTrendPanel.vue'
import { recentTwelveMonthStart, reportToday } from '../api/reportDates.js'
import { formatChineseDate, formatChineseMonth } from '../utils/dateDisplay.js'
import { createReportChartGrid, REPORT_CHART_COLORS } from '../utils/reportChartTheme.js'

defineProps({ exporting: Boolean })
const emit = defineEmits(['export'])

use([BarChart, LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

const today = reportToday()
const periodStart = ref(`${today.slice(0, 7)}-01`)
const periodEnd = ref(today)
const appliedPeriodStart = ref(periodStart.value)
const appliedPeriodEnd = ref(periodEnd.value)
const monthlyPeriodStart = ref(recentTwelveMonthStart(today))
const monthlyPeriodEnd = ref(today)
const defaultMonthlyView = ref(true)
const chartType = ref('bar_line')
const report = ref(null)
const loading = ref(false)
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

function beginRequest() {
  requestController?.abort()
  requestController = new AbortController()
  return { sequence: ++requestSequence, signal: requestController.signal }
}

function requestIsCurrent(sequence, signal) {
  return pageActive.value && !signal.aborted && sequence === requestSequence
}

const dailyRecords = computed(() => report.value?.daily_records || [])
const monthlyRecords = computed(() => report.value?.monthly_records || [])
const total = computed(() => report.value?.current_month_total || {
  task_count: 0,
  cut_length_m: 0,
  groove_length_m: 0,
  work_time_seconds: 0,
})
const monthlyTotal = computed(() => report.value?.twelve_month_total || {
  task_count: 0,
  cut_length_m: 0,
  groove_length_m: 0,
  work_time_seconds: 0,
})
const snapshot = computed(() => report.value?.snapshot || {})
const monthlyTitle = computed(() => defaultMonthlyView.value ? '近12个月按月对比与趋势' : '区间按月对比与趋势')
const analysisReady = computed(() => Boolean(
  pageActive.value
  &&
  report.value
  && snapshot.value.data_as_of
  && !loading.value
))

function roundNumber(value, digits = 1) {
  const factor = 10 ** digits
  return Math.round((Number(value || 0) + Number.EPSILON) * factor) / factor
}

function formatLength(value) {
  return new Intl.NumberFormat('zh-CN', {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  }).format(Number(value || 0))
}

function formatDuration(value) {
  const totalSeconds = Math.max(0, Math.round(Number(value || 0)))
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  return [hours, minutes, seconds].map((part) => String(part).padStart(2, '0')).join(':')
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

async function loadReport(force = false) {
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
    monthlyPeriodStart.value = requestedStart
    monthlyPeriodEnd.value = requestedEnd
    defaultMonthlyView.value = false
    await nextTick()
    renderCharts()
    scheduleRefreshPoll(true)
  } catch (exception) {
    if (exception.name !== 'AbortError' && sequence === requestSequence) error.value = exception.message
  } finally {
    if (sequence === requestSequence) {
      loading.value = false
      requestController = null
    }
  }
}

async function loadDefaultReport() {
  const { sequence, signal } = beginRequest()
  loading.value = true
  error.value = ''
  try {
    const result = await getLeadershipReport(today.slice(0, 7), { signal })
    if (!requestIsCurrent(sequence, signal)) return
    report.value = result
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
      await nextTick()
      renderCharts()
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

function renderCharts() {
  if (!pageActive.value) return
  const dailyChartElement = dailyPanel.value?.getChartElement()
  const monthlyChartElement = monthlyPanel.value?.getChartElement()
  if (!dailyChartElement || !monthlyChartElement || !report.value) return
  dailyChart?.dispose()
  monthlyChart?.dispose()
  dailyChart = init(dailyChartElement)
  monthlyChart = init(monthlyChartElement)
  const dailyChartRecords = [...dailyRecords.value].sort((left, right) => left.period_start.localeCompare(right.period_start))
  const monthlyChartRecords = [...monthlyRecords.value].sort((left, right) => left.period_start.localeCompare(right.period_start))

  dailyChart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { data: ['切割长度', '坡口长度', '切割工时'] },
    grid: createReportChartGrid(),
    xAxis: { type: 'category', data: dailyChartRecords.map((row) => row.period_start.slice(5)) },
    yAxis: [{ type: 'value', name: '长度（米）' }, { type: 'value', name: '工时（小时）' }],
    series: [
      { name: '切割长度', type: 'bar', data: dailyChartRecords.map((row) => roundNumber(row.cut_length_m)), tooltip: { valueFormatter: (value) => `${formatLength(value)} 米` }, itemStyle: { color: REPORT_CHART_COLORS.primary } },
      { name: '坡口长度', type: 'bar', data: dailyChartRecords.map((row) => roundNumber(row.groove_length_m)), tooltip: { valueFormatter: (value) => `${formatLength(value)} 米` }, itemStyle: { color: REPORT_CHART_COLORS.secondary } },
      { name: '切割工时', type: 'line', yAxisIndex: 1, smooth: true, data: dailyChartRecords.map((row) => roundNumber(Number(row.work_time_seconds) / 3600)), tooltip: { valueFormatter: (value) => `${formatLength(value)} 小时` }, itemStyle: { color: REPORT_CHART_COLORS.accent } },
    ].map(applyChartType),
  })
  monthlyChart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { data: ['切割长度', '坡口长度', '完成任务'] },
    grid: createReportChartGrid(),
    xAxis: { type: 'category', data: monthlyChartRecords.map((row) => row.period_start.slice(0, 7)) },
    yAxis: [{ type: 'value', name: '长度（米）' }, { type: 'value', name: '任务数' }],
    series: [
      { name: '切割长度', type: 'bar', data: monthlyChartRecords.map((row) => roundNumber(row.cut_length_m)), tooltip: { valueFormatter: (value) => `${formatLength(value)} 米` }, itemStyle: { color: REPORT_CHART_COLORS.primary } },
      { name: '坡口长度', type: 'bar', data: monthlyChartRecords.map((row) => roundNumber(row.groove_length_m)), tooltip: { valueFormatter: (value) => `${formatLength(value)} 米` }, itemStyle: { color: REPORT_CHART_COLORS.secondary } },
      { name: '完成任务', type: 'line', yAxisIndex: 1, smooth: true, data: monthlyChartRecords.map((row) => row.task_count), itemStyle: { color: REPORT_CHART_COLORS.accent } },
    ].map(applyChartType),
  })
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

function pausePage() {
  if (!pageActive.value) return
  pageActive.value = false
  requestSequence += 1
  requestController?.abort()
  requestController = null
  loading.value = false
  refreshLoading.value = false
  stopResizeListening()
  stopRefreshPoll()
  disposeCharts()
}

function exportReport(format = 'html') {
  emit('export', {
    report_type: 'leadership',
    report_date: appliedPeriodStart.value,
    period_start: appliedPeriodStart.value,
    period_end: appliedPeriodEnd.value,
    project_code: null,
    ship_no: null,
    parameters: { chart_type: chartType.value },
  }, format)
}

onMounted(() => {
  loadDefaultReport()
})
onActivated(async () => {
  pageActive.value = true
  startResizeListening()
  await nextTick()
  if (!report.value && !loading.value) await loadDefaultReport()
  else renderCharts()
  resizeCharts()
  scheduleRefreshPoll(true)
})
onDeactivated(pausePage)
onBeforeUnmount(pausePage)
watch(chartType, async () => {
  await nextTick()
  renderCharts()
})
</script>

<template>
  <section class="page-hero report-hero leadership-hero">
    <div>
      <span class="eyebrow">MANAGEMENT OVERVIEW</span>
      <h2>领导报表</h2>
      <p>按自定义日期区间查看任务、切割长度、坡口长度和切割时长趋势。</p>
    </div>
    <form class="report-query month-query" @submit.prevent="loadReport(false)">
      <ReportPeriodPicker
        v-model:start-date="periodStart"
        v-model:end-date="periodEnd"
        :anchor-date="today"
        :disabled="loading"
        @validation-error="error = $event"
      />
      <label><span>图表类型</span><AppSelect v-model="chartType"><option value="bar_line">柱线组合</option><option value="bar">柱状图</option><option value="line">折线图</option><option value="area">面积图</option></AppSelect></label>
      <button class="primary" type="submit" :disabled="loading">
        <span v-if="loading" class="spinner"></span>{{ loading ? '查询中' : '查询报表' }}
      </button>
      <ReportExportMenu :disabled="loading || !report || !snapshot.data_as_of || snapshot.refreshing" :loading="exporting" @select="exportReport" />
    </form>
  </section>

  <p v-if="report" class="report-source-note">
    数据截至：{{ formatSnapshotTime(snapshot.data_as_of) }}；
    {{ snapshot.refreshing ? '正在后台刷新，当前展示最近一次快照' : snapshot.stale ? '快照已过期，系统将自动更新' : '数据快照有效' }}。
  </p>

  <div v-if="error" class="global-error"><strong>报表读取失败</strong><span>{{ error }}</span><button @click="error = ''">关闭</button></div>

  <section class="leadership-kpis" aria-label="区间汇总">
    <article><span>完成任务</span><strong>{{ total.task_count }}</strong><small>项</small></article>
    <article><span>切割长度</span><strong>{{ formatLength(total.cut_length_m) }}</strong><small>米</small></article>
    <article><span>坡口长度</span><strong>{{ formatLength(total.groove_length_m) }}</strong><small>米</small></article>
    <article><span>切割时长</span><strong>{{ formatDuration(total.work_time_seconds) }}</strong><small>HH:mm:ss</small></article>
  </section>

  <ReportTrendPanel ref="dailyPanel" number="01" title="区间每日明细与趋势" chart-label="区间每日趋势图">
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

  <ReportTrendPanel ref="monthlyPanel" number="02" :title="monthlyTitle" :note="`${monthlyPeriodStart} 至 ${monthlyPeriodEnd}`" chart-label="区间月度趋势图">
    <template #table>
        <table v-content-table="pageActive" class="leadership-table summary-table">
        <thead><tr><th>序号</th><th>月份</th><th>任务数</th><th>切割长度</th><th>坡口长度</th><th>切割时长</th><th>备注</th></tr></thead>
        <tbody>
          <tr v-for="(row, index) in monthlyRecords" :key="row.period_start">
            <td>{{ index + 1 }}</td><td>{{ formatChineseMonth(row.period_start) }}</td><td>{{ row.task_count }}</td>
            <td>{{ formatLength(row.cut_length_m) }}</td><td>{{ formatLength(row.groove_length_m) }}</td>
            <td class="duration-cell">{{ formatDuration(row.work_time_seconds) }}</td><td>{{ row.remark || '--' }}</td>
          </tr>
          <tr v-if="refreshLoading && !snapshot.data_as_of"><td colspan="7" class="table-empty">历史趋势正在后台生成</td></tr>
          <tr v-else-if="!loading && !monthlyRecords.length"><td colspan="7" class="table-empty">所选区间暂无已完成任务</td></tr>
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
    :ready="analysisReady"
    :data-version="report?.analysis_source_version || ''"
  />

</template>
