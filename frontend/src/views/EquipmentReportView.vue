<script setup>
import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { init, use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { computed, nextTick, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, watch } from 'vue'
import { getEquipmentReport } from '../api/reports'
import AnalysisPanel from '../components/AnalysisPanel.vue'

const emit = defineEmits(['export'])

use([BarChart, LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

const reportDate = ref('')
const chartType = ref('bar_line')
const report = ref(null)
const loading = ref(false)
const error = ref('')
const page = ref(1)
const pageSize = 10
const chartElement = ref(null)
let chart = null

const records = computed(() => report.value?.records || [])
const summary = computed(() => report.value?.summary || {
  task_count: 0,
  total_work_time: 0,
  total_gas_consumption: 0,
  total_electricity_consumption: 0,
})
const pageCount = computed(() => Math.max(1, Math.ceil(records.value.length / pageSize)))
const pageRows = computed(() => records.value.slice((page.value - 1) * pageSize, page.value * pageSize))

function roundNumber(value, digits = 3) {
  const factor = 10 ** digits
  return Math.round((Number(value || 0) + Number.EPSILON) * factor) / factor
}

function formatNumber(value, digits = 3) {
  return new Intl.NumberFormat('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(Number(value || 0))
}

function formatDuration(value) {
  const totalSeconds = Math.max(0, Math.round(Number(value || 0)))
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  return [hours, minutes, seconds].map((part) => String(part).padStart(2, '0')).join(':')
}

function formatDateTime(value) {
  if (!value) return '--'
  return String(value).replace('T', ' ').slice(0, 16)
}

function timeLabel(value, index) {
  if (!value) return `任务${index + 1}`
  return String(value).replace('T', ' ').slice(11, 16)
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
  loading.value = true
  error.value = ''
  try {
    const result = await getEquipmentReport(reportDate.value, { force })
    report.value = result
    if (result.report_date) reportDate.value = result.report_date
    page.value = 1
    await nextTick()
    renderChart()
  } catch (exception) {
    error.value = exception.message
  } finally {
    loading.value = false
  }
}

function renderChart() {
  if (!chartElement.value || !report.value) return
  chart?.dispose()
  chart = init(chartElement.value)
  const rows = records.value
  chart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { data: ['气耗', '电耗'] },
    grid: { left: 54, right: 50, bottom: 48, top: 50 },
    xAxis: {
      type: 'category',
      data: rows.map((row, index) => timeLabel(row.production_time, index)),
      axisLabel: { hideOverlap: true },
    },
    yAxis: [
      { type: 'value', name: '气耗 Nm³' },
      { type: 'value', name: '电耗 kWh' },
    ],
    series: [
      {
        name: '气耗',
        type: 'bar',
        data: rows.map((row) => roundNumber(row.gas_consumption)),
        tooltip: { valueFormatter: (value) => `${formatNumber(value)} Nm³` },
        itemStyle: { color: '#56a3a6' },
      },
      {
        name: '电耗',
        type: 'line',
        yAxisIndex: 1,
        smooth: true,
        data: rows.map((row) => roundNumber(row.electricity_consumption)),
        tooltip: { valueFormatter: (value) => `${formatNumber(value)} kWh` },
        itemStyle: { color: '#e58e26' },
      },
    ].map(applyChartType),
  })
}

function resizeChart() {
  chart?.resize()
}

function exportReport() {
  if (!reportDate.value) return
  emit('export', {
    report_type: 'equipment',
    report_date: reportDate.value,
    project_code: null,
    ship_no: null,
    parameters: { chart_type: chartType.value },
  })
}

onMounted(() => {
  loadReport()
})
onActivated(async () => {
  window.addEventListener('resize', resizeChart)
  await nextTick()
  resizeChart()
})
onDeactivated(() => {
  window.removeEventListener('resize', resizeChart)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', resizeChart)
  chart?.dispose()
})
watch(chartType, async () => {
  await nextTick()
  renderChart()
})
</script>

<template>
  <section class="equipment-hero">
    <div>
      <span class="eyebrow">EQUIPMENT REPORT</span>
      <h2>设备生产日报</h2>
      <p>进入页面自动展示最近有数据的设备日报，表格格式与生产报表保持一致。</p>
    </div>
    <form class="equipment-query" @submit.prevent="loadReport(true)">
      <label><span>生产日期</span><input v-model="reportDate" type="date" required /></label>
      <label><span>图表类型</span><select v-model="chartType"><option value="bar_line">柱线组合</option><option value="bar">柱状图</option><option value="line">折线图</option><option value="area">面积图</option></select></label>
      <button class="primary" type="submit" :disabled="loading"><span v-if="loading" class="spinner"></span>{{ loading ? '查询中' : '查询报表' }}</button>
      <button type="button" :disabled="loading || !records.length" @click="exportReport">导出 HTML</button>
    </form>
  </section>

  <div v-if="error" class="global-error"><strong>设备报表读取失败</strong><span>{{ error }}</span><button @click="error = ''">关闭</button></div>

  <section class="equipment-kpis" aria-label="设备日报汇总">
    <article><span>任务数量</span><strong>{{ summary.task_count }}</strong><small>项</small></article>
    <article><span>切割时长</span><strong>{{ formatDuration(summary.total_work_time) }}</strong><small>HH:mm:ss</small></article>
    <article><span>总气耗</span><strong>{{ formatNumber(summary.total_gas_consumption, 2) }}</strong><small>Nm³ · 模拟</small></article>
    <article><span>总电耗</span><strong>{{ formatNumber(summary.total_electricity_consumption, 2) }}</strong><small>kWh · 模拟</small></article>
  </section>

  <section class="panel equipment-panel">
    <div class="panel-title">
      <div><span>01</span><h2>设备生产明细与能耗图</h2></div>
      <p>{{ report?.energy_notice || '气耗和电耗暂为模拟数据' }}</p>
    </div>
    <div class="equipment-split">
      <div ref="chartElement" class="equipment-chart" aria-label="设备能耗图"></div>
      <div class="equipment-table-area">
        <div class="equipment-table-wrap">
          <table class="equipment-table">
            <colgroup><col class="eq-index" /><col class="eq-project" /><col class="eq-ship" /><col class="eq-segment" /><col class="eq-start" /><col class="eq-duration" /><col class="eq-energy" /><col class="eq-energy" /><col class="eq-remark" /></colgroup>
            <thead>
              <tr class="equipment-group-title"><th colspan="9">设备</th></tr>
              <tr><th>序号</th><th>工程号</th><th>船号</th><th>分段名</th><th>开始时间</th><th>切割时长</th><th>气耗</th><th>电耗</th><th>备注</th></tr>
            </thead>
            <tbody>
              <tr v-for="(row, index) in pageRows" :key="row.task_id">
                <td>{{ (page - 1) * pageSize + index + 1 }}</td>
                <td :title="row.project_code || ''">{{ row.project_code || '--' }}</td>
                <td :title="row.ship_no || ''">{{ row.ship_no || '--' }}</td>
                <td :title="row.segment_name || ''">{{ row.segment_name || '--' }}</td>
                <td>{{ formatDateTime(row.production_time) }}</td>
                <td class="duration-cell">{{ formatDuration(row.work_time) }}</td>
                <td>{{ formatNumber(row.gas_consumption) }}</td>
                <td>{{ formatNumber(row.electricity_consumption) }}</td>
                <td :title="row.remark || ''">{{ row.remark || '--' }}</td>
              </tr>
              <tr v-if="!loading && !pageRows.length"><td colspan="9" class="table-empty">所选日期暂无设备任务</td></tr>
            </tbody>
          </table>
        </div>
        <div class="equipment-pagination" aria-label="设备明细分页">
          <span>共 {{ records.length }} 项 · 第 {{ page }} / {{ pageCount }} 页</span>
          <div><button type="button" :disabled="page <= 1" @click="page -= 1">上一页</button><button type="button" :disabled="page >= pageCount" @click="page += 1">下一页</button></div>
        </div>
      </div>
    </div>
  </section>

  <AnalysisPanel
    report-type="equipment"
    :report-date="reportDate"
    :ready="Boolean(report?.report_date)"
    :context-version="`${summary.task_count}-${summary.total_work_time}-${summary.total_electricity_consumption}`"
  />

  <p class="report-source-note">数据按开始时间从早到晚排列；气耗、电耗为稳定模拟值，真实能耗表接入后再替换。</p>
</template>
