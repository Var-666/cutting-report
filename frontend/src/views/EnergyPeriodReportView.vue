<script setup>
import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { init, use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { computed, nextTick, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, watch } from 'vue'
import { getEnergyRange, getLatestWorkshopProductionDate } from '../api/reports'
import { reportToday } from '../api/reportDates.js'
import AnalysisPanel from '../components/AnalysisPanel.vue'
import ReportExportMenu from '../components/ReportExportMenu.vue'
import ReportPeriodPicker from '../components/ReportPeriodPicker.vue'

use([BarChart, LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])
defineProps({ exporting: Boolean })
const emit = defineEmits(['export'])

const today = reportToday()
const anchorDate = ref(today)
const periodStart = ref(today)
const periodEnd = ref(today)
const taskDay = ref(null)
const periodReport = ref(null)
const chartType = ref('bar_line')
const loading = ref(false)
const error = ref('')
const page = ref(1)
const pageSize = 50
const taskChartElement = ref(null)
const periodChartElement = ref(null)
let taskChart = null
let periodChart = null
let controller = null
let active = true

const taskRows = computed(() => taskDay.value?.records || [])
const dailyRows = computed(() => periodReport.value?.rows || [])
const month = computed(() => periodReport.value)
const visibleTaskRows = computed(() => taskRows.value.slice((page.value - 1) * pageSize, page.value * pageSize))
const totalPages = computed(() => Math.max(1, Math.ceil(taskRows.value.length / pageSize)))
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
const analysisReady = computed(() => Boolean(taskDay.value && periodStart.value === periodEnd.value && !loading.value))

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
    tooltip: { trigger: 'axis', confine: true },
    legend: { data: ['电耗', '气耗'], top: 6 },
    grid: { left: 22, right: 28, bottom: 18, top: 54, containLabel: true },
    xAxis: { type: 'category', data: labels, axisLabel: { hideOverlap: true } },
    yAxis: [
      { type: 'value', name: `电耗\n${units.value.electricity}`, splitLine: { lineStyle: { color: '#edf1f4' } } },
      { type: 'value', name: `气耗\n${units.value.gas}`, splitLine: { show: false } },
    ],
    series: [
      { name: '电耗', type: line ? 'line' : 'bar', yAxisIndex: 0, data: rows.map((row) => chartValue(row.electricity)), tooltip: { valueFormatter: (value) => format(value) }, itemStyle: { color: '#db8c32' } },
      { name: '气耗', type: chartType.value === 'bar' ? 'bar' : 'line', yAxisIndex: 1, data: rows.map((row) => chartValue(row.gas)), tooltip: { valueFormatter: (value) => format(value) }, itemStyle: { color: '#167e85' } },
    ],
  }
}

function renderCharts() {
  if (!active) return
  if (taskChartElement.value && taskDay.value) {
    taskChart ||= init(taskChartElement.value)
    const rows = taskRows.value.slice(0, 50).map((row) => ({ electricity: row.electricity_consumption, gas: row.gas_consumption }))
    const labels = taskRows.value.slice(0, 50).map((row) => `${String(row.production_time || '').slice(5, 16).replace('T', ' ')} ${row.segment_name || ''}`)
    taskChart.setOption(chartOption(rows, labels), true)
  }
  if (periodChartElement.value && periodReport.value) {
    periodChart ||= init(periodChartElement.value)
    const rows = dailyRows.value.map((row) => ({ electricity: row.electricity?.value, gas: row.gas?.value }))
    periodChart.setOption(chartOption(rows, dailyRows.value.map((row) => row.report_date.slice(5))), true)
  }
}

async function loadRange(force = false) {
  if (!periodStart.value || !periodEnd.value) return
  if (periodEnd.value < periodStart.value) {
    error.value = '结束日期不能早于开始日期'
    return
  }
  controller?.abort()
  controller = new AbortController()
  loading.value = true
  error.value = ''
  try {
    const result = await getEnergyRange(periodStart.value, periodEnd.value, { force, signal: controller.signal })
    taskDay.value = result.task_report
    periodReport.value = result.period_report
    page.value = 1
    await nextTick()
    renderCharts()
  } catch (exception) {
    if (exception.name !== 'AbortError') error.value = exception.message
  } finally {
    loading.value = false
  }
}

function exportReport(format = 'html') {
  if (!taskDay.value || loading.value) return
  emit('export', {
    report_type: 'equipment',
    report_date: periodStart.value,
    period_start: periodStart.value,
    period_end: periodEnd.value,
    project_code: null,
    ship_no: null,
    parameters: { chart_type: chartType.value },
  }, format)
}

function resizeCharts() { if (active) { taskChart?.resize(); periodChart?.resize() } }
function pause() { active = false; controller?.abort(); window.removeEventListener('resize', resizeCharts) }

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
  await loadRange()
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

  <section class="panel energy-period-panel">
    <div class="panel-title energy-section-heading"><div><span>01</span><h2>区间任务能耗明细</h2><small>{{ periodStart }} 至 {{ periodEnd }}</small></div></div>
    <div class="energy-stat-strip">
      <div><span>任务电耗合计</span><strong>{{ format(summary.total_electricity_consumption) }}</strong><small>{{ units.electricity }}</small></div>
      <div><span>任务气耗合计</span><strong>{{ format(summary.total_gas_consumption) }}</strong><small>{{ units.gas }}</small></div>
      <div class="energy-coverage"><span>任务匹配情况</span><p>电耗 {{ summary.electricity_record_count || 0 }}/{{ summary.task_count || 0 }} 项</p><p>气耗 {{ summary.gas_record_count || 0 }}/{{ summary.task_count || 0 }} 项</p></div>
      <div class="energy-saved"><span>{{ equipmentScopeLabel }} 区间任务</span><strong>{{ summary.task_count || 0 }} 项</strong><small>累计切割时长 {{ duration(summary.total_work_time) }}</small></div>
    </div>
    <div class="energy-period-split">
      <div class="energy-period-chart-wrap"><div ref="taskChartElement" class="energy-period-chart" aria-label="任务能耗图"></div></div>
      <div class="energy-period-table-wrap energy-task-table-wrap">
        <table v-content-table class="energy-period-table energy-task-table summary-table" aria-label="任务能耗明细表">
          <thead><tr><th scope="col">序号</th><th scope="col">工程号</th><th scope="col">船号</th><th scope="col">分段名</th><th scope="col">任务开始</th><th scope="col">切割时长</th><th scope="col">电耗</th><th scope="col">气耗</th></tr></thead>
          <tbody><tr v-for="(row, index) in visibleTaskRows" :key="row.task_id"><th scope="row">{{ (page - 1) * pageSize + index + 1 }}</th><td>{{ row.project_code || '--' }}</td><td>{{ row.ship_no || '--' }}</td><td>{{ row.segment_name || '--' }}</td><td>{{ dateTime(row.production_time) }}</td><td>{{ duration(row.work_time) }}</td><td>{{ format(row.electricity_consumption) }}</td><td>{{ format(row.gas_consumption) }}</td></tr><tr v-if="!visibleTaskRows.length"><td colspan="8" class="energy-empty">{{ loading ? '正在查询…' : '所选区间暂无任务' }}</td></tr></tbody>
          <tfoot><tr><th aria-hidden="true"></th><th scope="row">汇总</th><th>{{ equipmentScopeLabel }}</th><th>{{ summary.task_count || 0 }} 项</th><th>--</th><th>{{ duration(summary.total_work_time) }}</th><th>{{ format(summary.total_electricity_consumption) }}</th><th>{{ format(summary.total_gas_consumption) }}</th></tr></tfoot>
        </table>
      </div>
    </div>
    <div v-if="taskRows.length" class="history-pagination"><span>第 {{ page }} / {{ totalPages }} 页，共 {{ taskRows.length }} 项</span><div><button :disabled="page <= 1" @click="page--">上一页</button><button :disabled="page >= totalPages" @click="page++">下一页</button></div></div>
  </section>

  <section class="panel energy-period-panel">
    <div class="panel-title energy-section-heading"><div><span>02</span><h2>区间每日能耗汇总</h2><small>月度每日能耗汇总口径 · {{ dailyRows.length }} 天</small></div></div>
    <div class="energy-period-split">
      <div class="energy-period-chart-wrap"><div ref="periodChartElement" class="energy-period-chart" aria-label="每日能耗图"></div></div>
      <div class="energy-period-table-wrap"><table v-content-table class="leadership-table energy-month-table summary-table" aria-label="月度每日能耗汇总表"><thead><tr><th scope="col">日期</th><th scope="col">任务数</th><th scope="col">切割时长</th><th scope="col">电耗</th><th scope="col">气耗</th></tr></thead><tbody><tr v-for="row in dailyRows" :key="row.report_date"><td>{{ row.report_date }}</td><td>{{ row.task_count }}</td><td>{{ duration(row.work_time_seconds) }}</td><td>{{ valueText(row.electricity) }}</td><td>{{ valueText(row.gas) }}</td></tr><tr v-if="!dailyRows.length"><td colspan="5" class="energy-empty">{{ loading ? '正在查询…' : '所选区间暂无每日汇总' }}</td></tr></tbody><tfoot><tr><th scope="row">汇总</th><th>{{ month?.total_task_count || 0 }}</th><th>{{ duration(month?.total_work_time_seconds) }}</th><th>{{ valueText(month?.summary?.electricity) }}</th><th>{{ valueText(month?.summary?.gas) }}</th></tr></tfoot></table></div>
    </div>
  </section>

  <p v-if="periodStart !== periodEnd" class="report-source-note">区间智能分析将在导出报告时生成，并与前一个等长区间对比。</p>
  <AnalysisPanel v-else report-type="equipment" :report-date="periodStart" :ready="analysisReady" :context-version="`${summary.task_count}-${summary.total_work_time}`" />
</template>

<style scoped>
.equipment-hero > div:first-child { min-width:0; flex:1 1 auto; }
.energy-period-query { flex:0 0 auto; }
.energy-config-note { display:grid; gap:6px; padding:14px 18px; margin-bottom:20px; background:#fff7e9; border:1px solid #eddbb6; color:#85571c; font-size:13px; }
.energy-period-panel { margin-bottom:22px; overflow:hidden; }
.energy-section-heading { gap:14px; flex-wrap:wrap; }
.energy-section-heading small { color:#728595; font-size:12px; }
.energy-stat-strip { display:grid; grid-template-columns:repeat(4,1fr); gap:14px; margin:20px 18px 0; }
.energy-stat-strip > div { min-width:0; min-height:112px; padding:18px 20px; border-top:3px solid var(--cyan); background:#fff; box-shadow:0 7px 22px rgba(21,54,78,.055); }
.energy-stat-strip span { display:block; color:var(--muted); font-size:11px; }
.energy-stat-strip strong { color:var(--navy); font-size:24px; display:block; margin-top:11px; }
.energy-stat-strip small,.energy-stat-strip p { color:#687d8d; display:block; font-size:11px; margin:6px 0 0; }
.energy-period-split { display:grid; grid-template-columns:minmax(330px,.9fr) minmax(0,1.25fr); gap:14px; padding:16px 18px 20px; }
.energy-period-chart-wrap,.energy-period-table-wrap { min-width:0; border:1px solid #e0e7ec; background:#fff; }
.energy-period-chart { width:100%; height:260px; }
.energy-period-table-wrap { height:260px; max-height:260px; overflow:auto; }
.energy-period-table { width:100%; border-collapse:collapse; font-size:12px; }
.energy-period-table th,.energy-period-table td { height:47px; padding:10px; border-bottom:1px solid #dde5ea; text-align:center; white-space:nowrap; }
.energy-task-table th:nth-child(2),.energy-task-table td:nth-child(2),.energy-task-table th:nth-child(3),.energy-task-table td:nth-child(3),.energy-task-table th:nth-child(4),.energy-task-table td:nth-child(4) { text-align:left; }
.energy-period-table thead th { position:sticky; top:0; z-index:2; color:#fff; background:#0b4f76; }
.energy-period-table tfoot th { position:sticky; bottom:0; background:#eef4f7; color:var(--navy); }
.energy-task-table tfoot th:nth-child(2),.energy-task-table tfoot th:nth-child(3),.energy-task-table tfoot th:nth-child(4) { text-align:left; }
.energy-empty { height:120px; color:#8a9aa5; }
.history-pagination { display:flex; justify-content:space-between; align-items:center; padding:0 18px 18px; }
.history-pagination button { margin-left:8px; }
@media (max-width:900px) { .energy-stat-strip { grid-template-columns:1fr 1fr; }.energy-period-split { grid-template-columns:1fr; }.energy-period-table-wrap { height:auto; max-height:420px; } }
@media (max-width:560px) { .energy-stat-strip { grid-template-columns:1fr; } }
</style>
