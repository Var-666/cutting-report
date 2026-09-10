<script setup>
import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { init, use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { computed, nextTick, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, watch } from 'vue'
import { getEnergyDay, getEnergyMonth, getTaskEnergyDay } from '../api/reports'
import AnalysisPanel from '../components/AnalysisPanel.vue'
import ReportExportMenu from '../components/ReportExportMenu.vue'

use([BarChart, LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])
defineProps({ exporting: Boolean })
const emit = defineEmits(['export'])

const day = ref(null)
const taskDay = ref(null)
const month = ref(null)
const selectedDate = ref('')
const selectedMonth = ref('')
const chartType = ref('bar_line')
const dayLoading = ref(false)
const monthLoading = ref(false)
const syncing = ref(false)
const syncProgress = ref('')
const error = ref('')
const dayChartElement = ref(null)
const monthChartElement = ref(null)
let dayChart = null
let monthChart = null
let dayController = null
let monthController = null
let syncController = null
let resizeObserver = null
let active = true

const units = computed(() => taskDay.value?.units || day.value?.units || month.value?.units || { electricity: 'kWh', gas: 'Nm³' })
const configurationError = computed(() => taskDay.value?.configuration_error || day.value?.configuration_error || month.value?.configuration_error)
const taskRows = computed(() => taskDay.value?.records || [])
const equipmentScopeLabel = computed(() => {
  const scope = taskDay.value?.equipment_scope
  return scope?.length ? scope.join(' / ') : 'Cutter01'
})
const taskSummary = computed(() => taskDay.value?.summary || {
  task_count: 0,
  total_work_time: 0,
  total_gas_consumption: null,
  total_electricity_consumption: null,
  gas_record_count: 0,
  electricity_record_count: 0,
})
const monthRows = computed(() => month.value?.rows || [])
const busy = computed(() => syncing.value || dayLoading.value || monthLoading.value)
const analysisReady = computed(() => Boolean(
  taskDay.value
  && taskDay.value.report_date === selectedDate.value
  && !dayLoading.value,
))
const analysisVersion = computed(() => {
  const summary = taskSummary.value
  return [
    taskDay.value?.report_date || '',
    summary.task_count || 0,
    summary.electricity_record_count || 0,
    summary.gas_record_count || 0,
    summary.total_electricity_consumption ?? '',
    summary.total_gas_consumption ?? '',
  ].join('-')
})

function format(value, digits = 3) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '--'
  return new Intl.NumberFormat('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(Number(value))
}

function valueText(item) {
  return format(item?.value)
}

function dateTime(value) {
  return value ? String(value).replace('T', ' ').slice(0, 19) : '--'
}

function duration(value) {
  const total = Math.max(0, Math.round(Number(value || 0)))
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const seconds = total % 60
  return [hours, minutes, seconds].map((part) => String(part).padStart(2, '0')).join(':')
}

function taskValue(value) {
  return format(value)
}

function exportReport(format = 'html') {
  if (!taskDay.value || !month.value || busy.value) return
  emit('export', {
    report_type: 'equipment',
    report_date: selectedDate.value,
    project_code: null,
    ship_no: null,
    parameters: {
      chart_type: chartType.value,
      report_month: selectedMonth.value,
    },
  }, format)
}

function taskLabel(row, index) {
  const segment = row.segment_name || row.project_code || `任务${index + 1}`
  const clock = row.production_time ? String(row.production_time).slice(11, 16) : ''
  return clock ? `${clock} ${segment}` : segment
}

async function loadDay(force = false, reportDate = selectedDate.value) {
  dayController?.abort()
  const controller = new AbortController()
  dayController = controller
  dayLoading.value = true
  error.value = ''
  try {
    const [result, tasks] = await Promise.all([
      getEnergyDay(reportDate, { force, signal: controller.signal }),
      getTaskEnergyDay(reportDate, { force, signal: controller.signal }),
    ])
    if (controller.signal.aborted) return
    day.value = result
    taskDay.value = tasks
    selectedDate.value = result.report_date
    if (!selectedMonth.value) selectedMonth.value = result.report_date.slice(0, 7)
    await nextTick()
    renderCharts()
    await loadMonth()
  } catch (exception) {
    if (exception.name !== 'AbortError') error.value = exception.message
  } finally {
    if (dayController === controller) dayLoading.value = false
  }
}

async function loadMonth(force = false) {
  if (!selectedMonth.value) return
  monthController?.abort()
  const controller = new AbortController()
  monthController = controller
  monthLoading.value = true
  try {
    const result = await getEnergyMonth(selectedMonth.value, { force, signal: controller.signal })
    if (controller.signal.aborted) return
    month.value = result
    await nextTick()
    renderCharts()
  } catch (exception) {
    if (exception.name !== 'AbortError') error.value = exception.message
  } finally {
    if (monthController === controller) monthLoading.value = false
  }
}

async function syncMonth(force = false) {
  if (!month.value || syncing.value || selectedMonth.value !== month.value.report_month) return
  const requestedMonth = month.value.report_month
  const targets = month.value.rows.filter((row) => !row.is_future && (force || !row.saved || row.is_today))
  if (!targets.length) {
    syncProgress.value = '本月已有记录均已保存；如需更新，请点击重新查询本月。'
    return
  }
  syncing.value = true
  error.value = ''
  const controller = new AbortController()
  syncController = controller
  let completed = 0
  let failed = false
  try {
    for (const row of targets) {
      syncProgress.value = `正在同步 ${requestedMonth} · ${completed}/${targets.length} 天 · ${row.report_date}`
      const result = await getEnergyDay(row.report_date, { force: true, signal: controller.signal })
      if (controller.signal.aborted) break
      if (!result.sync_ok) {
        error.value = result.warning || '能管查询失败，已停止本月同步；已保存记录不会丢失。'
        failed = true
        break
      }
      completed += 1
      if (day.value?.report_date === result.report_date) day.value = result
      await loadMonth()
    }
    syncProgress.value = `${failed || controller.signal.aborted ? '同步已停止' : '同步完成'}：${completed}/${targets.length} 天已查询并保存；缺数日期会保留缺失提示。`
  } catch (exception) {
    if (exception.name === 'AbortError') syncProgress.value = `已停止同步；已完成的 ${completed} 天记录已保存。`
    else { error.value = exception.message; syncProgress.value = `同步中断，已保存 ${completed} 天。` }
  } finally {
    syncing.value = false
    if (active) await loadMonth()
    await nextTick()
    if (active) renderCharts()
  }
}

function stopSync() {
  syncController?.abort()
}

function options(rows, labels, reportUnits) {
  const series = [
    { name: '电耗', type: chartType.value === 'line' ? 'line' : 'bar', yAxisIndex: 0, color: '#db8c32',
      data: rows.map((row) => row.electricity?.value ?? null),
      tooltip: { valueFormatter: (value) => `${format(value)} ${reportUnits.electricity}` } },
    { name: '气耗', type: chartType.value === 'bar' ? 'bar' : 'line', yAxisIndex: 1, color: '#167e85',
      data: rows.map((row) => row.gas?.value ?? null),
      tooltip: { valueFormatter: (value) => `${format(value)} ${reportUnits.gas}` } },
  ].map((item) => ({ ...item, connectNulls: false, smooth: false, symbolSize: 6, barMaxWidth: 22 }))
  return { tooltip: { trigger: 'axis', confine: true }, legend: { data: ['电耗', '气耗'], top: 6 },
    grid: { left: 22, right: 28, bottom: 18, top: 54, containLabel: true },
    xAxis: { type: 'category', data: labels, axisLabel: { hideOverlap: true, margin: 7 }, axisTick: { alignWithLabel: true } },
    yAxis: [{ type: 'value', name: `电耗\n${reportUnits.electricity}`, nameGap: 6, nameTextStyle: { lineHeight: 13 }, splitLine: { lineStyle: { color: '#edf1f4' } } },
      { type: 'value', name: `气耗\n${reportUnits.gas}`, nameGap: 6, nameTextStyle: { lineHeight: 13 }, splitLine: { show: false } }], series }
}

function renderCharts() {
  if (!active) return
  if (dayChartElement.value && taskDay.value) {
    dayChart ||= init(dayChartElement.value)
    const rows = taskRows.value.map((row) => ({
      electricity: { value: row.electricity_consumption },
      gas: { value: row.gas_consumption },
    }))
    dayChart.setOption(options(rows, taskRows.value.map(taskLabel), taskDay.value.units), true)
  }
  if (monthChartElement.value && month.value) {
    monthChart ||= init(monthChartElement.value)
    monthChart.setOption(options(monthRows.value, monthRows.value.map((row) => row.report_date.slice(8)), month.value.units), true)
  }
}

function resizeCharts() {
  if (active) { dayChart?.resize(); monthChart?.resize() }
}

function pause() {
  active = false
  dayController?.abort()
  monthController?.abort()
  syncController?.abort()
  window.removeEventListener('resize', resizeCharts)
}

onMounted(() => {
  resizeObserver = new ResizeObserver(resizeCharts)
  if (dayChartElement.value) resizeObserver.observe(dayChartElement.value)
  if (monthChartElement.value) resizeObserver.observe(monthChartElement.value)
  loadDay()
})
onActivated(async () => {
  active = true
  window.addEventListener('resize', resizeCharts)
  if (!day.value && !dayLoading.value) loadDay()
  await nextTick()
  renderCharts()
  resizeCharts()
})
onDeactivated(pause)
onBeforeUnmount(() => { pause(); resizeObserver?.disconnect(); dayChart?.dispose(); monthChart?.dispose() })
watch(chartType, async () => { await nextTick(); renderCharts() })
</script>

<template>
  <section class="page-hero report-hero equipment-hero">
    <div><span class="eyebrow">EQUIPMENT ENERGY REPORT</span><h2>设备报表</h2><p>查看 {{ equipmentScopeLabel }} 的任务和月度能耗数据</p></div>
    <form class="report-query equipment-query energy-period-query" @submit.prevent="loadDay(true)">
      <label><span>查询日期</span><AppDatePicker v-model="selectedDate" required :disabled="busy" /></label>
      <label><span>图表类型</span><AppSelect v-model="chartType" aria-label="图表类型"><option value="bar_line">柱线组合</option><option value="bar">双柱状图</option><option value="line">双折线图</option></AppSelect></label>
      <button class="primary" type="submit" :disabled="busy"><span v-if="dayLoading" class="spinner"></span>{{ dayLoading ? '查询中' : '查询报表' }}</button>
      <ReportExportMenu :disabled="busy || !taskDay || !month" :loading="exporting" @select="exportReport" />
    </form>
  </section>
  <div v-if="error" class="global-error"><strong>查询提示</strong><span>{{ error }}</span><button type="button" @click="error = ''">关闭</button></div>
  <div v-if="configurationError" class="energy-config-note"><strong>能管接入待配置</strong><span>{{ configurationError }} 页面结构已可用，尚未取到的数值显示 --。</span></div>

  <section class="panel energy-period-panel" aria-labelledby="energy-day-title">
    <div class="panel-title energy-section-heading">
      <div><span>01</span><h2 id="energy-day-title">任务能耗明细</h2><small>{{ taskDay?.report_date || '加载中' }}</small></div>
    </div>
    <div class="energy-stat-strip">
      <div><span>任务电耗合计</span><strong>{{ format(taskSummary.total_electricity_consumption) }}</strong><small>{{ units.electricity }}</small></div>
      <div><span>任务气耗合计</span><strong>{{ format(taskSummary.total_gas_consumption) }}</strong><small>{{ units.gas }}</small></div>
      <div class="energy-coverage"><span>任务匹配情况</span><p>电耗 {{ taskSummary.electricity_record_count || 0 }}/{{ taskSummary.task_count || 0 }} 项</p><p>气耗 {{ taskSummary.gas_record_count || 0 }}/{{ taskSummary.task_count || 0 }} 项</p></div>
      <div class="energy-saved"><span>{{ equipmentScopeLabel }} 当日任务</span><strong>{{ taskSummary.task_count || 0 }} 项</strong><small>累计切割时长 {{ duration(taskSummary.total_work_time) }}</small></div>
    </div>
    <p v-if="taskDay?.warning" class="energy-inline-warning" role="status">{{ taskDay.warning }}</p>
    <div class="energy-period-split">
      <div class="energy-period-chart-wrap"><div ref="dayChartElement" class="energy-period-chart" role="img" aria-label="每项任务电耗和气耗图"></div></div>
      <div class="energy-period-table-wrap energy-task-table-wrap">
        <table v-content-table class="energy-period-table energy-task-table summary-table" aria-label="任务能耗明细表">
          <thead><tr><th scope="col">序号</th><th scope="col">工程号</th><th scope="col">船号</th><th scope="col">分段名</th><th scope="col">任务开始</th><th scope="col">切割时长</th><th scope="col">电耗 <small>{{ units.electricity }}</small></th><th scope="col">气耗 <small>{{ units.gas }}</small></th></tr></thead>
          <tbody>
            <tr v-for="(row, index) in taskRows" :key="row.task_id">
              <th scope="row">{{ index + 1 }}</th><td>{{ row.project_code || '--' }}</td><td>{{ row.ship_no || '--' }}</td><td>{{ row.segment_name || '--' }}</td><td>{{ dateTime(row.production_time) }}</td><td>{{ duration(row.work_time) }}</td><td>{{ taskValue(row.electricity_consumption) }}</td><td>{{ taskValue(row.gas_consumption) }}</td>
            </tr>
            <tr v-if="!taskRows.length"><td colspan="8" class="energy-empty">{{ dayLoading ? '正在查询任务能耗…' : `所选日期暂无 ${equipmentScopeLabel} 任务` }}</td></tr>
          </tbody>
          <tfoot><tr><th aria-hidden="true"></th><th scope="row">汇总</th><th>{{ equipmentScopeLabel }}</th><th>{{ taskSummary.task_count || 0 }} 项</th><th>--</th><th class="duration-cell">{{ duration(taskSummary.total_work_time) }}</th><th>{{ format(taskSummary.total_electricity_consumption) }}</th><th>{{ format(taskSummary.total_gas_consumption) }}</th></tr></tfoot>
        </table>
      </div>
    </div>
  </section>

  <section class="panel energy-period-panel" aria-labelledby="energy-month-title">
    <div class="panel-title energy-section-heading">
      <div><span>02</span><h2 id="energy-month-title">月度每日能耗汇总</h2><small>{{ month?.report_month || '--' }} · {{ month?.calendar_day_count || 0 }} 天</small></div>
      <form @submit.prevent="loadMonth(true)"><label>统计月份<AppDatePicker v-model="selectedMonth" mode="month" required :disabled="busy" /></label><button type="submit" :disabled="busy">查看月份</button><button type="button" class="primary" :disabled="busy || !month || selectedMonth !== month.report_month || Boolean(configurationError)" @click="syncMonth(false)">同步本月</button><button type="button" :disabled="busy || !month || selectedMonth !== month.report_month || Boolean(configurationError)" @click="syncMonth(true)">重新查询本月</button><button v-if="syncing" type="button" @click="stopSync">停止同步</button></form>
    </div>
    <div class="energy-stat-strip">
      <div><span>月度电耗合计</span><strong>{{ valueText(month?.summary?.electricity) }}</strong><small>{{ month?.units?.electricity || units.electricity }}</small></div>
      <div><span>月度气耗合计</span><strong>{{ valueText(month?.summary?.gas) }}</strong><small>{{ month?.units?.gas || units.gas }}</small></div>
      <div class="energy-coverage"><span>已有耗量的日期</span><p>电耗 {{ month?.summary?.electricity?.valid_count || 0 }} 天</p><p>气耗 {{ month?.summary?.gas?.valid_count || 0 }} 天</p></div>
      <div class="energy-saved"><span>已保存日记录</span><strong>{{ month?.saved_day_count || 0 }} / {{ month?.elapsed_day_count || 0 }}</strong></div>
    </div>
    <p v-if="syncProgress" class="energy-sync-progress" role="status" aria-live="polite">{{ syncProgress }}</p>
    <div class="energy-period-split">
      <div class="energy-period-chart-wrap"><div ref="monthChartElement" class="energy-period-chart" role="img" aria-label="月度每日电耗和气耗图"></div></div>
      <div class="energy-period-table-wrap energy-month-table-wrap">
        <table v-content-table class="leadership-table energy-month-table summary-table" aria-label="月度每日能耗汇总表">
          <thead><tr><th scope="col">日期</th><th scope="col">任务数</th><th scope="col">切割时长</th><th scope="col">电耗 <small>{{ month?.units?.electricity || units.electricity }}</small></th><th scope="col">气耗 <small>{{ month?.units?.gas || units.gas }}</small></th></tr></thead>
          <tbody><tr v-for="row in monthRows" :key="row.report_date" :class="{ 'current-period': row.report_date === day?.report_date }"><td><button class="energy-date-link" type="button" :disabled="busy || row.is_future" @click="loadDay(false, row.report_date)">{{ row.report_date }}{{ row.is_today ? ' · 今日' : '' }}</button></td><td>{{ row.is_future ? '--' : row.task_count || 0 }}</td><td class="duration-cell">{{ row.is_future ? '--' : duration(row.work_time_seconds) }}</td><td>{{ valueText(row.electricity) }}</td><td>{{ valueText(row.gas) }}</td></tr><tr v-if="!monthRows.length"><td colspan="5" class="energy-empty">{{ monthLoading ? '读取月度记录中…' : '请选择月份查看' }}</td></tr></tbody>
          <tfoot><tr><th scope="row">汇总</th><th>{{ month?.total_task_count || 0 }}</th><th class="duration-cell">{{ duration(month?.total_work_time_seconds) }}</th><th>{{ valueText(month?.summary?.electricity) }}</th><th>{{ valueText(month?.summary?.gas) }}</th></tr></tfoot>
        </table>
      </div>
    </div>
  </section>

  <AnalysisPanel
    report-type="equipment"
    :report-date="selectedDate"
    :ready="analysisReady"
    :context-version="analysisVersion"
  />

</template>

<style scoped>
.equipment-hero > div:first-child { min-width:0; flex:1 1 auto; }
.energy-period-query { flex:0 0 auto; }
.energy-period-query > label { min-width:0; }
.energy-config-note { display:grid; gap:6px; padding:14px 18px; margin-bottom:20px; background:#fff7e9; border:1px solid #eddbb6; color:#85571c; font-size:13px; }
.energy-period-panel { margin-bottom:22px; overflow:hidden; }
.energy-section-heading { gap:14px; flex-wrap:wrap; }
.energy-section-heading > div { flex-wrap:wrap; }
.energy-section-heading small { color:#728595; font-size:12px; }
.energy-section-heading form { display:flex; gap:8px; align-items:flex-end; flex-wrap:wrap; }
.energy-section-heading label { display:grid; gap:5px; color:#687d8d; font-size:11px; }
.energy-section-heading .app-date-picker { width:150px; }
.energy-section-heading button { font-size:12px; padding:10px 13px; }
.energy-stat-strip { display:grid; grid-template-columns:repeat(4,1fr); gap:14px; margin:20px 18px 0; }
.energy-stat-strip > div { min-width:0; min-height:126px; padding:21px 22px; border-top:3px solid var(--cyan); background:#fff; box-shadow:0 7px 22px rgba(21,54,78,.055); }
.energy-stat-strip span { display:block; color:var(--muted); font-size:11px; margin-bottom:0; }
.energy-stat-strip strong { color:var(--navy); font-size:25px; font-variant-numeric:tabular-nums; display:block; margin-top:13px; }
.energy-stat-strip small { color:#82929d; display:block; font-size:11px; margin-top:6px; }
.energy-stat-strip p { margin:6px 0 0; font-size:12px; color:#385368; font-variant-numeric:tabular-nums; }
.energy-inline-warning,.energy-sync-progress { margin:14px 22px; font-size:12px; line-height:1.7; }
.energy-inline-warning { color:#986623; background:#fff9ee; padding:10px 12px; border-left:3px solid #dfa94a; }
.energy-sync-progress { color:#0e647e; background:#edf8fa; padding:8px 12px; }
.energy-period-split { display:grid; grid-template-columns:minmax(330px,.9fr) minmax(0,1.25fr); gap:14px; align-items:stretch; padding:16px 18px 20px; }
.energy-period-chart-wrap { min-width:0; margin:0; border:1px solid #e0e7ec; background:#fff; }
.energy-period-chart { width:100%; height:260px; }
.energy-period-table-wrap { min-width:0; height:260px; max-height:260px; margin:0; overflow:auto; border:1px solid #e0e7ec; }
.energy-period-table { width:100%; border-collapse:collapse; font-size:13px; font-variant-numeric:tabular-nums; }
.energy-task-table th:nth-child(2),.energy-task-table td:nth-child(2),.energy-task-table th:nth-child(3),.energy-task-table td:nth-child(3),.energy-task-table th:nth-child(4),.energy-task-table td:nth-child(4) { text-align:left; }
.energy-period-table th,.energy-period-table td { height:47px; padding:10px 12px; border-bottom:1px solid #dde5ea; text-align:right; vertical-align:middle; white-space:nowrap; }
.energy-period-table th:first-child,.energy-period-table td:first-child { text-align:center; }
.energy-month-table th,.energy-month-table td { text-align:center!important; }
.energy-period-table thead th { position:sticky; top:0; z-index:3; color:#fff; background:#0b4f76; font-size:11px; font-weight:600; letter-spacing:.4px; }
.energy-period-table thead small,.energy-month-table thead small { display:inline; color:inherit; font-weight:400; margin-left:3px; font-size:10px; }
.energy-period-table tbody th { font-weight:500; color:#526b7c; }
.energy-period-table tbody tr:hover { background:#f4f9fb; }
.energy-period-table tbody .energy-selected-day { background:#eaf6f7; }
.energy-period-table tfoot th { position:sticky; bottom:0; z-index:2; color:var(--navy); background:#eef4f7; font-weight:700; border-top:2px solid #7da4b6; border-bottom:0; }
.energy-task-table tfoot th:nth-child(2),.energy-task-table tfoot th:nth-child(3),.energy-task-table tfoot th:nth-child(4) { text-align:left; }
.energy-period-table td small { display:block; color:#84939e; margin-top:3px; font-size:10px; }
.energy-date-link { border:0; background:none; padding:0; color:inherit; cursor:pointer; font-size:13px; }
.energy-date-link:hover { text-decoration:underline; }
.energy-date-link:disabled { color:#91a0aa; cursor:default; }
.energy-empty { height:130px; text-align:center!important; color:#8a9aa5; }
@media (max-width:900px) { .energy-stat-strip { grid-template-columns:1fr 1fr; }.energy-period-split { grid-template-columns:1fr; }.energy-period-table-wrap { height:auto; max-height:none; }.energy-stat-strip { margin-left:14px; margin-right:14px; } }
@media (max-width:560px) { .energy-stat-strip { grid-template-columns:1fr; }.energy-section-heading form { width:100%; }.energy-section-heading .app-date-picker { width:145px; }.energy-stat-strip > div { padding:12px; }.energy-stat-strip strong { font-size:21px; }.energy-period-chart { height:240px; } }
</style>
