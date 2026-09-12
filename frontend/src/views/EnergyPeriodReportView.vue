<script setup>
import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { init, use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { computed, nextTick, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, watch } from 'vue'
import {
  getEnergyDay,
  getEnergyMonthlyRange,
  getEnergyRange,
  getLatestWorkshopProductionDate,
  getTaskEnergyDay,
} from '../api/reports'
import { recentTwelveMonthStart, reportToday } from '../api/reportDates.js'
import AnalysisPanel from '../components/AnalysisPanel.vue'
import ReportExportMenu from '../components/ReportExportMenu.vue'
import ReportPeriodPicker from '../components/ReportPeriodPicker.vue'
import ReportTrendPanel from '../components/ReportTrendPanel.vue'
import { createReportChartGrid, REPORT_CHART_COLORS } from '../utils/reportChartTheme.js'

use([BarChart, LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])
defineProps({ exporting: Boolean })
const emit = defineEmits(['export'])

const today = reportToday()
const anchorDate = ref(today)
const periodStart = ref(today)
const periodEnd = ref(today)
const appliedPeriodStart = ref(today)
const appliedPeriodEnd = ref(today)
const summaryPeriodStart = ref(recentTwelveMonthStart(today))
const summaryPeriodEnd = ref(today)
const defaultSummaryView = ref(true)
const taskDay = ref(null)
const periodReport = ref(null)
const chartType = ref('bar_line')
const loading = ref(false)
const error = ref('')
const taskPanel = ref(null)
const periodPanel = ref(null)
let taskChart = null
let periodChart = null
let controller = null
let active = true
let summaryRefreshTimer = null
let summaryRefreshCount = 0

const taskRows = computed(() => taskDay.value?.records || [])
const dailyRows = computed(() => periodReport.value?.rows || [])
const summaryRows = computed(() => dailyRows.value)
const month = computed(() => periodReport.value)
const units = computed(() => taskDay.value?.units || { electricity: 'kWh', gas: 'Nm³' })
const summary = computed(() => taskDay.value?.summary || {
  task_count: 0,
  total_work_time: 0,
  total_gas_consumption: null,
  total_electricity_consumption: null,
  gas_record_count: 0,
  electricity_record_count: 0,
})
const equipmentScopeLabel = computed(() => taskDay.value?.equipment_scope?.join(' / ') || 'Cutter01')
const configurationError = computed(() => taskDay.value?.configuration_error || '')
const summaryTitle = computed(() => defaultSummaryView.value ? '近12个月月度能耗汇总' : '区间每日能耗汇总')
const summaryCountLabel = computed(() => `${summaryRows.value.length} ${defaultSummaryView.value ? '个月' : '天'}`)
const summaryDateHeading = computed(() => defaultSummaryView.value ? '月份' : '日期')
const summaryChartLabel = computed(() => defaultSummaryView.value ? '月度能耗图' : '每日能耗图')
const summaryTableLabel = computed(() => defaultSummaryView.value ? '近12个月月度能耗汇总表' : '区间每日能耗汇总表')
const summaryEmptyText = computed(() => defaultSummaryView.value ? '近12个月暂无月度汇总' : '所选区间暂无每日汇总')
const summarySubtitle = computed(() => {
  const base = `${summaryPeriodStart.value} 至 ${summaryPeriodEnd.value} · ${summaryCountLabel.value}`
  const missing = Number(periodReport.value?.missing_day_count || 0)
  if (!defaultSummaryView.value || !missing) return base
  return periodReport.value?.syncing
    ? `${base} · 后台补齐 ${missing} 天能耗中`
    : `${base} · ${missing} 天暂无能耗数据`
})
const analysisReady = computed(() => Boolean(
  taskDay.value
  && taskDay.value.period_start === appliedPeriodStart.value
  && taskDay.value.period_end === appliedPeriodEnd.value
  && !loading.value
))

function format(value, digits = 1) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '--'
  return new Intl.NumberFormat('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(Number(value))
}

function duration(value) {
  const seconds = Math.max(0, Math.round(Number(value || 0)))
  return [Math.floor(seconds / 3600), Math.floor(seconds % 3600 / 60), seconds % 60]
    .map((item) => String(item).padStart(2, '0')).join(':')
}

function dateTime(value) { return value ? String(value).replace('T', ' ').slice(0, 19) : '--' }
function valueText(item) { return format(item?.value) }
function chartValue(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return null
  return Number(Number(value).toFixed(1))
}

function chartOption(rows, labels) {
  const line = chartType.value === 'line'
  return {
    tooltip: { trigger: 'axis' },
    legend: { data: ['电耗', '气耗'] },
    grid: createReportChartGrid(),
    xAxis: { type: 'category', data: labels, axisLabel: { hideOverlap: true } },
    yAxis: [
      { type: 'value', name: `电耗\n${units.value.electricity}`, splitLine: { lineStyle: { color: '#edf1f4' } } },
      { type: 'value', name: `气耗\n${units.value.gas}`, splitLine: { show: false } },
    ],
    series: [
      { name: '电耗', type: line ? 'line' : 'bar', yAxisIndex: 0, data: rows.map((row) => chartValue(row.electricity)), tooltip: { valueFormatter: (value) => format(value) }, itemStyle: { color: REPORT_CHART_COLORS.primary } },
      { name: '气耗', type: chartType.value === 'bar' ? 'bar' : 'line', yAxisIndex: 1, data: rows.map((row) => chartValue(row.gas)), tooltip: { valueFormatter: (value) => format(value) }, itemStyle: { color: chartType.value === 'bar_line' ? REPORT_CHART_COLORS.accent : REPORT_CHART_COLORS.secondary } },
    ],
  }
}

function renderCharts() {
  if (!active) return
  const taskChartElement = taskPanel.value?.getChartElement()
  const periodChartElement = periodPanel.value?.getChartElement()
  if (taskChartElement && taskDay.value) {
    taskChart ||= init(taskChartElement)
    const rows = taskRows.value.slice(0, 50).map((row) => ({ electricity: row.electricity_consumption, gas: row.gas_consumption }))
    const labels = taskRows.value.slice(0, 50).map((row) => `${String(row.production_time || '').slice(5, 16).replace('T', ' ')} ${row.segment_name || ''}`)
    taskChart.setOption(chartOption(rows, labels), true)
  }
  if (periodChartElement && periodReport.value) {
    periodChart ||= init(periodChartElement)
    const rows = summaryRows.value.map((row) => ({ electricity: row.electricity?.value, gas: row.gas?.value }))
    const labels = summaryRows.value.map((row) => defaultSummaryView.value ? row.report_date : row.report_date.slice(5))
    periodChart.setOption(chartOption(rows, labels), true)
  }
}

async function loadRange(force = false) {
  if (!periodStart.value || !periodEnd.value) return
  if (periodEnd.value < periodStart.value) {
    error.value = '结束日期不能早于开始日期'
    return
  }
  controller?.abort()
  stopSummaryRefresh()
  controller = new AbortController()
  loading.value = true
  error.value = ''
  const requestedStart = periodStart.value
  const requestedEnd = periodEnd.value
  try {
    const result = await getEnergyRange(requestedStart, requestedEnd, { force, signal: controller.signal })
    taskDay.value = result.task_report
    periodReport.value = result.period_report
    appliedPeriodStart.value = requestedStart
    appliedPeriodEnd.value = requestedEnd
    summaryPeriodStart.value = requestedStart
    summaryPeriodEnd.value = requestedEnd
    defaultSummaryView.value = false
    await nextTick()
    renderCharts()
  } catch (exception) {
    if (exception.name !== 'AbortError') error.value = exception.message
  } finally {
    loading.value = false
  }
}

function stopSummaryRefresh() {
  if (summaryRefreshTimer) window.clearTimeout(summaryRefreshTimer)
  summaryRefreshTimer = null
}

async function applyMonthlySummary(summaryResult) {
  periodReport.value = summaryResult
  await nextTick()
  renderCharts()
  scheduleSummaryRefresh(summaryResult)
}

function scheduleSummaryRefresh(summaryResult) {
  stopSummaryRefresh()
  if (!active || !defaultSummaryView.value || !summaryResult?.syncing
      || !summaryResult?.missing_day_count || summaryRefreshCount >= 60) return
  const requestedStart = summaryPeriodStart.value
  const requestedEnd = summaryPeriodEnd.value
  summaryRefreshTimer = window.setTimeout(async () => {
    summaryRefreshTimer = null
    if (!active || !defaultSummaryView.value
        || requestedStart !== summaryPeriodStart.value
        || requestedEnd !== summaryPeriodEnd.value) return
    summaryRefreshCount += 1
    try {
      const refreshed = await getEnergyMonthlyRange(requestedStart, requestedEnd, { force: true })
      await applyMonthlySummary(refreshed)
    } catch {
      scheduleSummaryRefresh(summaryResult)
    }
  }, 10000)
}

async function loadDefaultRanges() {
  controller?.abort()
  stopSummaryRefresh()
  summaryRefreshCount = 0
  controller = new AbortController()
  loading.value = true
  error.value = ''
  const selectedDate = periodStart.value
  const recentStart = recentTwelveMonthStart(selectedDate)
  const signal = controller.signal
  appliedPeriodStart.value = selectedDate
  appliedPeriodEnd.value = selectedDate
  summaryPeriodStart.value = recentStart
  summaryPeriodEnd.value = selectedDate
  defaultSummaryView.value = true

  const showError = (exception) => {
    if (exception.name !== 'AbortError') error.value ||= exception.message
  }
  const taskRequest = getTaskEnergyDay(selectedDate, { signal })
    .then(async (taskResult) => {
      if (signal.aborted) return
      taskDay.value = {
        ...taskResult,
        period_start: selectedDate,
        period_end: selectedDate,
      }
      await nextTick()
      renderCharts()
    })
    .catch(showError)
  const summaryRequest = getEnergyMonthlyRange(recentStart, selectedDate, { signal })
    .then(async (summaryResult) => {
      if (signal.aborted) return
      await applyMonthlySummary(summaryResult)
    })
    .catch(showError)

  // Daily meter synchronization is independent from first paint. Existing
  // monthly snapshots render first; the current month refreshes silently once
  // the live meter request finishes.
  getEnergyDay(selectedDate, { signal })
    .then(() => getEnergyMonthlyRange(recentStart, selectedDate, { signal }))
    .then(async (summaryResult) => {
      if (signal.aborted) return
      await applyMonthlySummary(summaryResult)
    })
    .catch(() => {})

  await Promise.allSettled([taskRequest, summaryRequest])
  if (!signal.aborted) loading.value = false
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
    parameters: { chart_type: chartType.value },
  }, format)
}

function resizeCharts() { if (active) { taskChart?.resize(); periodChart?.resize() } }
function pause() { active = false; controller?.abort(); stopSummaryRefresh(); window.removeEventListener('resize', resizeCharts) }

async function initializeReport() {
  try {
    const latest = await getLatestWorkshopProductionDate(today)
    if (latest.report_date) {
      anchorDate.value = latest.report_date
      periodStart.value = latest.report_date
      periodEnd.value = latest.report_date
    }
  } catch (exception) {
    error.value = exception.message
  }
  await loadDefaultRanges()
}

onMounted(initializeReport)
onActivated(async () => { active = true; window.addEventListener('resize', resizeCharts); await nextTick(); renderCharts(); resizeCharts() })
onDeactivated(pause)
onBeforeUnmount(() => { pause(); taskChart?.dispose(); periodChart?.dispose() })
watch(chartType, async () => { await nextTick(); renderCharts() })
</script>

<template>
  <section class="page-hero report-hero equipment-hero">
    <div><span class="eyebrow">EQUIPMENT ENERGY REPORT</span><h2>设备报表</h2><p>查看 {{ equipmentScopeLabel }} 在自定义区间内的任务和能耗数据</p></div>
    <form class="report-query equipment-query energy-period-query" @submit.prevent="loadRange(true)">
      <ReportPeriodPicker
        v-model:start-date="periodStart"
        v-model:end-date="periodEnd"
        :anchor-date="anchorDate"
        :max-days="31"
        :disabled="loading"
        @validation-error="error = $event"
      />
      <label><span>图表类型</span><AppSelect v-model="chartType"><option value="bar_line">柱线组合</option><option value="bar">双柱状图</option><option value="line">双折线图</option></AppSelect></label>
      <button class="primary" type="submit" :disabled="loading"><span v-if="loading" class="spinner"></span>{{ loading ? '查询中' : '查询报表' }}</button>
      <ReportExportMenu :disabled="loading || !taskDay" :loading="exporting" @select="exportReport" />
    </form>
  </section>

  <div v-if="error" class="global-error"><strong>查询提示</strong><span>{{ error }}</span><button @click="error = ''">关闭</button></div>
  <div v-if="configurationError" class="energy-config-note"><strong>能管数据提示</strong><span>{{ configurationError }}。缺失值显示 --，不按零计算。</span></div>

  <ReportTrendPanel ref="taskPanel" number="01" title="区间任务能耗明细" :subtitle="`${appliedPeriodStart} 至 ${appliedPeriodEnd}`" chart-label="任务能耗图">
    <template #summary>
      <div class="energy-stat-strip">
        <div><span>任务电耗合计</span><strong>{{ format(summary.total_electricity_consumption) }}</strong><small>{{ units.electricity }}</small></div>
        <div><span>任务气耗合计</span><strong>{{ format(summary.total_gas_consumption) }}</strong><small>{{ units.gas }}</small></div>
        <div class="energy-coverage"><span>任务匹配情况</span><p>电耗 {{ summary.electricity_record_count || 0 }}/{{ summary.task_count || 0 }} 项</p><p>气耗 {{ summary.gas_record_count || 0 }}/{{ summary.task_count || 0 }} 项</p></div>
        <div class="energy-saved"><span>{{ equipmentScopeLabel }} 区间任务</span><strong>{{ summary.task_count || 0 }} 项</strong><small>累计切割时长 {{ duration(summary.total_work_time) }}</small></div>
      </div>
    </template>
    <template #table>
        <table v-content-table class="energy-period-table energy-task-table summary-table" aria-label="任务能耗明细表">
          <thead><tr><th scope="col">序号</th><th scope="col">工程号</th><th scope="col">船号</th><th scope="col">分段名</th><th scope="col">任务开始</th><th scope="col">切割时长</th><th scope="col">电耗</th><th scope="col">气耗</th></tr></thead>
          <tbody><tr v-for="(row, index) in taskRows" :key="row.task_id"><th scope="row">{{ index + 1 }}</th><td>{{ row.project_code || '--' }}</td><td>{{ row.ship_no || '--' }}</td><td>{{ row.segment_name || '--' }}</td><td>{{ dateTime(row.production_time) }}</td><td>{{ duration(row.work_time) }}</td><td>{{ format(row.electricity_consumption) }}</td><td>{{ format(row.gas_consumption) }}</td></tr><tr v-if="!taskRows.length"><td colspan="8" class="energy-empty">{{ loading ? '正在查询…' : '所选区间暂无任务' }}</td></tr></tbody>
          <tfoot><tr><th aria-hidden="true"></th><th scope="row">汇总</th><th>{{ equipmentScopeLabel }}</th><th>{{ summary.task_count || 0 }} 项</th><th>--</th><th>{{ duration(summary.total_work_time) }}</th><th>{{ format(summary.total_electricity_consumption) }}</th><th>{{ format(summary.total_gas_consumption) }}</th></tr></tfoot>
        </table>
    </template>
  </ReportTrendPanel>

  <ReportTrendPanel ref="periodPanel" number="02" :title="summaryTitle" :subtitle="summarySubtitle" :chart-label="summaryChartLabel">
    <template #table><table v-content-table class="leadership-table energy-month-table summary-table" :aria-label="summaryTableLabel"><thead><tr><th scope="col">{{ summaryDateHeading }}</th><th scope="col">任务数</th><th scope="col">切割时长</th><th scope="col">电耗</th><th scope="col">气耗</th></tr></thead><tbody><tr v-for="row in summaryRows" :key="row.report_date"><td>{{ row.report_date }}</td><td>{{ row.task_count }}</td><td>{{ duration(row.work_time_seconds) }}</td><td>{{ valueText(row.electricity) }}</td><td>{{ valueText(row.gas) }}</td></tr><tr v-if="!summaryRows.length"><td colspan="5" class="energy-empty">{{ loading ? '正在查询…' : summaryEmptyText }}</td></tr></tbody><tfoot><tr><th scope="row">汇总</th><th>{{ month?.total_task_count || 0 }}</th><th>{{ duration(month?.total_work_time_seconds) }}</th><th>{{ valueText(month?.summary?.electricity) }}</th><th>{{ valueText(month?.summary?.gas) }}</th></tr></tfoot></table></template>
  </ReportTrendPanel>

  <AnalysisPanel
    report-type="equipment"
    :report-date="appliedPeriodStart"
    :period-start="defaultSummaryView ? '' : appliedPeriodStart"
    :period-end="defaultSummaryView ? '' : appliedPeriodEnd"
    :ready="analysisReady"
    :context-version="`${appliedPeriodStart}-${appliedPeriodEnd}-${summary.task_count}-${summary.total_work_time}`"
  />
</template>

<style scoped>
.energy-config-note { display:grid; gap:6px; padding:14px 18px; margin-bottom:20px; background:#fff7e9; border:1px solid #eddbb6; color:#85571c; font-size:13px; }
.energy-stat-strip { display:grid; grid-template-columns:repeat(4,1fr); gap:14px; margin:20px 18px 0; }
.energy-stat-strip > div { min-width:0; min-height:112px; padding:18px 20px; border-top:3px solid var(--cyan); background:#fff; box-shadow:0 7px 22px rgba(21,54,78,.055); }
.energy-stat-strip span { display:block; color:var(--muted); font-size:11px; }
.energy-stat-strip strong { color:var(--navy); font-size:24px; display:block; margin-top:11px; }
.energy-stat-strip small,.energy-stat-strip p { color:#687d8d; display:block; font-size:11px; margin:6px 0 0; }
.energy-period-table { width:100%; border-collapse:collapse; font-size:12px; }
.energy-period-table th,.energy-period-table td { height:47px; padding:10px; border-bottom:1px solid #dde5ea; text-align:center; white-space:nowrap; }
.energy-task-table th:nth-child(2),.energy-task-table td:nth-child(2),.energy-task-table th:nth-child(3),.energy-task-table td:nth-child(3),.energy-task-table th:nth-child(4),.energy-task-table td:nth-child(4) { text-align:left; }
.energy-period-table thead th { position:sticky; top:0; z-index:2; color:#fff; background:#0b4f76; }
.energy-period-table tfoot th { position:sticky; bottom:0; background:#eef4f7; color:var(--navy); }
.energy-task-table tfoot th:nth-child(2),.energy-task-table tfoot th:nth-child(3),.energy-task-table tfoot th:nth-child(4) { text-align:left; }
.energy-empty { height:120px; color:#8a9aa5; }
@media (max-width:900px) { .energy-stat-strip { grid-template-columns:1fr 1fr; } }
@media (max-width:560px) { .energy-stat-strip { grid-template-columns:1fr; } }
</style>
