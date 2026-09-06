<script setup>
import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { init, use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { computed, nextTick, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, watch } from 'vue'
import { getLeadershipReport } from '../api/reports'
import AnalysisPanel from '../components/AnalysisPanel.vue'
import ReportExportMenu from '../components/ReportExportMenu.vue'
import { reportToday } from '../api/reportDates.js'

defineProps({ exporting: Boolean })
const emit = defineEmits(['export'])

use([BarChart, LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

const currentMonth = reportToday().slice(0, 7)

const reportMonth = ref(currentMonth)
const chartType = ref('bar_line')
const report = ref(null)
const loading = ref(false)
const error = ref('')
const dailyChartElement = ref(null)
const monthlyChartElement = ref(null)
let dailyChart = null
let monthlyChart = null

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

function roundNumber(value, digits = 3) {
  const factor = 10 ** digits
  return Math.round((Number(value || 0) + Number.EPSILON) * factor) / factor
}

function formatLength(value) {
  return new Intl.NumberFormat('zh-CN', {
    minimumFractionDigits: 3,
    maximumFractionDigits: 3,
  }).format(Number(value || 0))
}

function formatDuration(value) {
  const totalSeconds = Math.max(0, Math.round(Number(value || 0)))
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  return [hours, minutes, seconds].map((part) => String(part).padStart(2, '0')).join(':')
}

function formatDay(value) {
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    timeZone: 'Asia/Shanghai',
  }).format(new Date(`${value}T00:00:00+08:00`))
}

function formatMonth(value) {
  const [year, month] = value.split('-')
  return `${year}年${Number(month)}月`
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
  if (!reportMonth.value) return
  loading.value = true
  error.value = ''
  try {
    report.value = await getLeadershipReport(reportMonth.value, { force })
    await nextTick()
    renderCharts()
  } catch (exception) {
    error.value = exception.message
  } finally {
    loading.value = false
  }
}

function renderCharts() {
  if (!dailyChartElement.value || !monthlyChartElement.value || !report.value) return
  dailyChart?.dispose()
  monthlyChart?.dispose()
  dailyChart = init(dailyChartElement.value)
  monthlyChart = init(monthlyChartElement.value)
  const dailyChartRecords = [...dailyRecords.value].sort((left, right) => left.period_start.localeCompare(right.period_start))
  const monthlyChartRecords = [...monthlyRecords.value].sort((left, right) => left.period_start.localeCompare(right.period_start))

  dailyChart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { data: ['切割长度', '坡口长度', '切割工时'] },
    grid: { left: 58, right: 58, bottom: 52, top: 48 },
    xAxis: { type: 'category', data: dailyChartRecords.map((row) => row.period_start.slice(5)) },
    yAxis: [{ type: 'value', name: '长度（米）' }, { type: 'value', name: '工时（小时）' }],
    series: [
      { name: '切割长度', type: 'bar', data: dailyChartRecords.map((row) => roundNumber(row.cut_length_m)), tooltip: { valueFormatter: (value) => `${formatLength(value)} 米` }, itemStyle: { color: '#1769aa' } },
      { name: '坡口长度', type: 'bar', data: dailyChartRecords.map((row) => roundNumber(row.groove_length_m)), tooltip: { valueFormatter: (value) => `${formatLength(value)} 米` }, itemStyle: { color: '#56a3a6' } },
      { name: '切割工时', type: 'line', yAxisIndex: 1, smooth: true, data: dailyChartRecords.map((row) => roundNumber(Number(row.work_time_seconds) / 3600)), tooltip: { valueFormatter: (value) => `${formatLength(value)} 小时` }, itemStyle: { color: '#e58e26' } },
    ].map(applyChartType),
  })
  monthlyChart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { data: ['切割长度', '坡口长度', '完成任务'] },
    grid: { left: 58, right: 58, bottom: 52, top: 48 },
    xAxis: { type: 'category', data: monthlyChartRecords.map((row) => row.period_start.slice(0, 7)) },
    yAxis: [{ type: 'value', name: '长度（米）' }, { type: 'value', name: '任务数' }],
    series: [
      { name: '切割长度', type: 'bar', data: monthlyChartRecords.map((row) => roundNumber(row.cut_length_m)), tooltip: { valueFormatter: (value) => `${formatLength(value)} 米` }, itemStyle: { color: '#1769aa' } },
      { name: '坡口长度', type: 'bar', data: monthlyChartRecords.map((row) => roundNumber(row.groove_length_m)), tooltip: { valueFormatter: (value) => `${formatLength(value)} 米` }, itemStyle: { color: '#56a3a6' } },
      { name: '完成任务', type: 'line', yAxisIndex: 1, smooth: true, data: monthlyChartRecords.map((row) => row.task_count), itemStyle: { color: '#d47f22' } },
    ].map(applyChartType),
  })
}

function resizeCharts() {
  dailyChart?.resize()
  monthlyChart?.resize()
}

function exportReport(format = 'html') {
  emit('export', {
    report_type: 'leadership',
    report_date: `${reportMonth.value}-01`,
    project_code: null,
    ship_no: null,
    parameters: { chart_type: chartType.value },
  }, format)
}

onMounted(() => {
  loadReport()
})
onActivated(async () => {
  window.addEventListener('resize', resizeCharts)
  await nextTick()
  resizeCharts()
})
onDeactivated(() => {
  window.removeEventListener('resize', resizeCharts)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', resizeCharts)
  dailyChart?.dispose()
  monthlyChart?.dispose()
})
watch(chartType, async () => {
  await nextTick()
  renderCharts()
})
</script>

<template>
  <section class="leadership-hero">
    <div>
      <span class="eyebrow">MANAGEMENT OVERVIEW</span>
      <h2>领导生产报表</h2>
      <p>完成任务按日汇总，当月明细与最近十二个月趋势统一展示。</p>
    </div>
    <form class="month-query" @submit.prevent="loadReport(true)">
      <label><span>报表月份</span><input v-model="reportMonth" type="month" required /></label>
      <label><span>图表类型</span><select v-model="chartType"><option value="bar_line">柱线组合</option><option value="bar">柱状图</option><option value="line">折线图</option><option value="area">面积图</option></select></label>
      <button class="primary" type="submit" :disabled="loading">
        <span v-if="loading" class="spinner"></span>{{ loading ? '查询中' : '刷新报表' }}
      </button>
      <ReportExportMenu :disabled="loading || !report" :loading="exporting" @select="exportReport" />
    </form>
  </section>

  <div v-if="error" class="global-error"><strong>报表读取失败</strong><span>{{ error }}</span><button @click="error = ''">关闭</button></div>

  <section class="leadership-kpis" aria-label="当月汇总">
    <article><span>完成任务</span><strong>{{ total.task_count }}</strong><small>项</small></article>
    <article><span>切割长度</span><strong>{{ formatLength(total.cut_length_m) }}</strong><small>米</small></article>
    <article><span>坡口长度</span><strong>{{ formatLength(total.groove_length_m) }}</strong><small>米</small></article>
    <article><span>切割时长</span><strong>{{ formatDuration(total.work_time_seconds) }}</strong><small>HH:mm:ss</small></article>
  </section>

  <section class="panel leadership-panel">
    <div class="panel-title">
      <div><span>01</span><h2>当月明细与趋势</h2></div>
      <p>仅统计 STATUS = 2 且 WORK_TIME ≥ 0 · 长度单位：米 · 全部机台</p>
    </div>
    <div class="leadership-split">
      <div ref="dailyChartElement" class="leadership-chart" aria-label="当月每日趋势图"></div>
      <div class="leadership-table-wrap">
        <table v-content-table class="leadership-table">
        <colgroup><col class="col-index" /><col class="col-period" /><col class="col-count" /><col class="col-number" /><col class="col-number" /><col class="col-duration" /><col class="col-remark" /></colgroup>
        <thead><tr><th>序号</th><th>日期</th><th>任务数</th><th>切割长度</th><th>坡口长度</th><th>切割时长</th><th>备注</th></tr></thead>
        <tbody>
          <tr v-for="(row, index) in dailyRecords" :key="row.period_start">
            <td>{{ index + 1 }}</td><td>{{ formatDay(row.period_start) }}</td><td>{{ row.task_count }}</td>
            <td>{{ formatLength(row.cut_length_m) }}</td><td>{{ formatLength(row.groove_length_m) }}</td>
            <td class="duration-cell">{{ formatDuration(row.work_time_seconds) }}</td><td>{{ row.remark || '--' }}</td>
          </tr>
          <tr v-if="!loading && !dailyRecords.length"><td colspan="7" class="table-empty">所选月份暂无已完成任务</td></tr>
        </tbody>
        <tfoot>
          <tr><th aria-hidden="true"></th><th>汇总</th><th>{{ total.task_count }}</th><th>{{ formatLength(total.cut_length_m) }}</th><th>{{ formatLength(total.groove_length_m) }}</th><th class="duration-cell">{{ formatDuration(total.work_time_seconds) }}</th><th>--</th></tr>
        </tfoot>
        </table>
      </div>
    </div>
  </section>

  <section class="panel leadership-panel">
    <div class="panel-title">
      <div><span>02</span><h2>历月对比与趋势</h2></div>
      <p>所选月份及其前十一个月</p>
    </div>
    <div class="leadership-split">
      <div ref="monthlyChartElement" class="leadership-chart" aria-label="最近十二个月趋势图"></div>
      <div class="leadership-table-wrap">
        <table v-content-table class="leadership-table">
        <colgroup><col class="col-index" /><col class="col-period" /><col class="col-count" /><col class="col-number" /><col class="col-number" /><col class="col-duration" /><col class="col-remark" /></colgroup>
        <thead><tr><th>序号</th><th>月份</th><th>任务数</th><th>切割长度</th><th>坡口长度</th><th>切割时长</th><th>备注</th></tr></thead>
        <tbody>
          <tr v-for="(row, index) in monthlyRecords" :key="row.period_start" :class="{ 'current-period': row.period_start.startsWith(reportMonth) }">
            <td>{{ index + 1 }}</td><td>{{ formatMonth(row.period_start) }}</td><td>{{ row.task_count }}</td>
            <td>{{ formatLength(row.cut_length_m) }}</td><td>{{ formatLength(row.groove_length_m) }}</td>
            <td class="duration-cell">{{ formatDuration(row.work_time_seconds) }}</td><td>{{ row.remark || '--' }}</td>
          </tr>
          <tr v-if="!loading && !monthlyRecords.length"><td colspan="7" class="table-empty">最近十二个月暂无已完成任务</td></tr>
        </tbody>
        <tfoot>
          <tr><th aria-hidden="true"></th><th>汇总</th><th>{{ monthlyTotal.task_count }}</th><th>{{ formatLength(monthlyTotal.cut_length_m) }}</th><th>{{ formatLength(monthlyTotal.groove_length_m) }}</th><th class="duration-cell">{{ formatDuration(monthlyTotal.work_time_seconds) }}</th><th>--</th></tr>
        </tfoot>
        </table>
      </div>
    </div>
  </section>

  <AnalysisPanel
    report-type="leadership"
    :report-date="`${reportMonth}-01`"
    :ready="Boolean(report)"
    :context-version="`${total.task_count}-${total.work_time_seconds}`"
  />

  <p v-if="report" class="report-source-note">
    数据源：{{ report.source_mode === 'demo' ? '演示数据' : report.source_mode === 'excel' ? 'TASK.xlsx 本地任务表' : 'SQL Server / dbo.task' }}；负工时任务已剔除；按 OP_DATE 归属日期，缺失时回退 START_TIME；任务按 TASK_ID 逐条统计。
  </p>
</template>
