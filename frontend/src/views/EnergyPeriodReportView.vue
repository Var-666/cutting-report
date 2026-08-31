<script setup>
import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { init, use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { computed, nextTick, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, watch } from 'vue'
import { getEnergyDay, getEnergyMonth } from '../api/reports'

use([BarChart, LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

const day = ref(null)
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

const units = computed(() => day.value?.units || month.value?.units || { electricity: '原始单位待确认', gas: 'Nm³' })
const configurationError = computed(() => day.value?.configuration_error || month.value?.configuration_error)
const dayRows = computed(() => day.value?.rows || [])
const monthRows = computed(() => month.value?.rows || [])
const busy = computed(() => syncing.value || dayLoading.value || monthLoading.value)

function format(value, digits = 3) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '--'
  return new Intl.NumberFormat('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(Number(value))
}

function estimated(item) {
  return item?.status === 'interpolated' || item?.estimated_count > 0
}

function valueText(item) {
  return `${item?.value !== null && item?.value !== undefined && estimated(item) ? '≈ ' : ''}${format(item?.value)}`
}

function statusText(item) {
  if (!item) return '尚未查询'
  if (item.status === 'measured') return '读数差值'
  if (item.status === 'interpolated') return '插值估算'
  return item.label || '缺失'
}

function coverage(item, suffix) {
  return `${item?.valid_count || 0}/${item?.expected_count || 0} ${suffix}`
}

function timestamp(value) {
  return value ? value.replace('T', ' ').slice(0, 19) : '尚未保存'
}

async function loadDay(force = false, reportDate = selectedDate.value) {
  dayController?.abort()
  const controller = new AbortController()
  dayController = controller
  dayLoading.value = true
  error.value = ''
  try {
    const result = await getEnergyDay(reportDate, { force, signal: controller.signal })
    if (controller.signal.aborted) return
    day.value = result
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

async function loadMonth() {
  if (!selectedMonth.value) return
  monthController?.abort()
  const controller = new AbortController()
  monthController = controller
  monthLoading.value = true
  try {
    const result = await getEnergyMonth(selectedMonth.value, { signal: controller.signal })
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
  const targets = month.value.rows.filter((row) => !row.is_future && (force || !row.saved || row.is_today || row.gas.complete_count < 24 || row.electricity.complete_count < 24))
  if (!targets.length) {
    syncProgress.value = '本月已保存日期均已完整；如需获取修订数据，请点击重新查询本月。'
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
  return { tooltip: { trigger: 'axis', confine: true }, legend: { top: 6 },
    grid: { top: 70, bottom: 50, left: 65, right: 65, containLabel: true },
    xAxis: { type: 'category', data: labels, axisLabel: { hideOverlap: true }, axisTick: { alignWithLabel: true } },
    yAxis: [{ type: 'value', name: `电耗\n${reportUnits.electricity}`, nameGap: 14, splitLine: { lineStyle: { color: '#edf1f4' } } },
      { type: 'value', name: `气耗 ${reportUnits.gas}`, nameGap: 18, splitLine: { show: false } }], series }
}

function renderCharts() {
  if (!active) return
  if (dayChartElement.value && day.value) {
    dayChart ||= init(dayChartElement.value)
    dayChart.setOption(options(dayRows.value, dayRows.value.map((row) => `${String(row.hour).padStart(2, '0')}:00`), day.value.units), true)
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
  <section class="energy-period-hero">
    <div><span class="eyebrow">ENERGY / DAILY & MONTHLY</span><h2>电耗 · 气耗日月报</h2><p>三块电表合计与气表独立统计 · 暂不绑定设备 · 查询后保存日记录</p></div>
    <label class="energy-chart-select">图表形式<select v-model="chartType" aria-label="图表形式"><option value="bar_line">电耗柱状 / 气耗折线</option><option value="bar">双柱状图</option><option value="line">双折线图</option></select></label>
  </section>
  <div v-if="error" class="global-error"><strong>查询提示</strong><span>{{ error }}</span><button type="button" @click="error = ''">关闭</button></div>
  <div v-if="configurationError" class="energy-config-note"><strong>能管接入待配置</strong><span>{{ configurationError }} 页面结构已可用，尚未取到的数值显示 --。</span></div>

  <section class="panel energy-period-panel" aria-labelledby="energy-day-title">
    <div class="energy-section-heading">
      <div><span class="energy-section-number">01</span><h2 id="energy-day-title">每日能耗明细</h2><small>{{ day?.report_date || '加载中' }} · 按小时</small></div>
      <form @submit.prevent="loadDay(true)"><label>查询日期<input v-model="selectedDate" type="date" required :disabled="busy" /></label><button class="primary" type="submit" :disabled="busy"><span v-if="dayLoading" class="spinner"></span>{{ dayLoading ? '查询中' : '查询 / 更新当日' }}</button></form>
    </div>
    <div class="energy-stat-strip">
      <div><span>当日电耗{{ day?.summary?.electricity?.status === 'partial' ? ' · 部分合计' : '合计' }}</span><strong>{{ valueText(day?.summary?.electricity) }}</strong><small>{{ units.electricity }}</small></div>
      <div><span>当日气耗{{ day?.summary?.gas?.status === 'partial' ? ' · 部分合计' : '合计' }}</span><strong>{{ valueText(day?.summary?.gas) }}</strong><small>{{ units.gas }}</small></div>
      <div class="energy-coverage"><span>有效小时</span><p>电耗 {{ coverage(day?.summary?.electricity, '小时') }}</p><p>气耗 {{ coverage(day?.summary?.gas, '小时') }}</p></div>
      <div class="energy-saved"><span>日记录保存时间</span><p>{{ timestamp(day?.updated_at) }}</p><small>{{ day?.is_today ? '今日仅统计已结束的完整小时' : '00:00 至次日 00:00' }}</small></div>
    </div>
    <p v-if="day?.warning" class="energy-inline-warning" role="status">{{ day.warning }}</p>
    <div class="energy-period-chart-wrap"><div ref="dayChartElement" class="energy-period-chart" role="img" aria-label="每日分小时电耗和气耗图"></div><p>横轴为小时开始时刻 · 双纵轴单位不同 · 缺失不画成零 · 估算与部分合计以表格标记为准</p></div>
    <div class="energy-period-table-wrap">
      <table class="energy-period-table" aria-label="每日能耗明细表">
        <thead><tr><th scope="col">时段</th><th scope="col">电耗 <small>{{ units.electricity }}</small></th><th scope="col">电耗状态</th><th scope="col">气耗 <small>{{ units.gas }}</small></th><th scope="col">气耗状态</th></tr></thead>
        <tbody><tr v-for="row in dayRows" :key="row.hour"><th scope="row">{{ row.label }}</th><td>{{ valueText(row.electricity) }}</td><td><span class="energy-status" :class="row.electricity.status">{{ statusText(row.electricity) }}</span></td><td>{{ valueText(row.gas) }}</td><td><span class="energy-status" :class="row.gas.status">{{ statusText(row.gas) }}</span></td></tr><tr v-if="!dayRows.length"><td colspan="5" class="energy-empty">{{ dayLoading ? '正在查询能耗数据…' : '请选择日期查询' }}</td></tr></tbody>
        <tfoot><tr><th scope="row">当日汇总</th><td>{{ valueText(day?.summary?.electricity) }}</td><td>{{ statusText(day?.summary?.electricity) }} · {{ coverage(day?.summary?.electricity, '小时') }}</td><td>{{ valueText(day?.summary?.gas) }}</td><td>{{ statusText(day?.summary?.gas) }} · {{ coverage(day?.summary?.gas, '小时') }}</td></tr></tfoot>
      </table>
    </div>
  </section>

  <section class="panel energy-period-panel" aria-labelledby="energy-month-title">
    <div class="energy-section-heading">
      <div><span class="energy-section-number">02</span><h2 id="energy-month-title">月度每日能耗记录</h2><small>{{ month?.report_month || '--' }} · {{ month?.calendar_day_count || 0 }} 天</small></div>
      <form @submit.prevent="loadMonth"><label>统计月份<input v-model="selectedMonth" type="month" required :disabled="busy" /></label><button type="submit" :disabled="busy">查看月份</button><button type="button" class="primary" :disabled="busy || !month || selectedMonth !== month.report_month || Boolean(configurationError)" @click="syncMonth(false)">同步本月</button><button type="button" :disabled="busy || !month || selectedMonth !== month.report_month || Boolean(configurationError)" @click="syncMonth(true)">重新查询本月</button><button v-if="syncing" type="button" @click="stopSync">停止同步</button></form>
    </div>
    <div class="energy-stat-strip">
      <div><span>月度电耗{{ month?.summary?.electricity?.status === 'partial' ? ' · 部分合计' : '合计' }}</span><strong>{{ valueText(month?.summary?.electricity) }}</strong><small>{{ month?.units?.electricity || units.electricity }}</small></div>
      <div><span>月度气耗{{ month?.summary?.gas?.status === 'partial' ? ' · 部分合计' : '合计' }}</span><strong>{{ valueText(month?.summary?.gas) }}</strong><small>{{ month?.units?.gas || units.gas }}</small></div>
      <div class="energy-coverage"><span>已有耗量的日期</span><p>电耗 {{ coverage(month?.summary?.electricity, '天') }} · 完整 {{ month?.summary?.electricity?.complete_count || 0 }} 天</p><p>气耗 {{ coverage(month?.summary?.gas, '天') }} · 完整 {{ month?.summary?.gas?.complete_count || 0 }} 天</p></div>
      <div class="energy-saved"><span>已保存日记录</span><strong>{{ month?.saved_day_count || 0 }} / {{ month?.elapsed_day_count || 0 }}</strong><small>未来日期不查询、不当作零</small></div>
    </div>
    <p class="energy-storage-note">{{ month?.storage_notice || '读取本地保存的每日能耗记录。' }} “同步本月”补齐缺失或不完整日期；“重新查询本月”更新全部已到日期。</p>
    <p v-if="syncProgress" class="energy-sync-progress" role="status" aria-live="polite">{{ syncProgress }}</p>
    <div class="energy-period-chart-wrap"><div ref="monthChartElement" class="energy-period-chart" role="img" aria-label="月度每日电耗和气耗图"></div><p>点击下表日期可查看当天明细 · 未查询或缺失日期显示断点 · 部分日期不是完整全天耗量</p></div>
    <div class="energy-period-table-wrap energy-month-table-wrap">
      <table class="energy-period-table" aria-label="月度每日能耗记录表">
        <thead><tr><th scope="col">日期</th><th scope="col">电耗 <small>{{ month?.units?.electricity || units.electricity }}</small></th><th scope="col">电耗覆盖</th><th scope="col">气耗 <small>{{ month?.units?.gas || units.gas }}</small></th><th scope="col">气耗覆盖</th><th scope="col">保存状态</th></tr></thead>
        <tbody><tr v-for="row in monthRows" :key="row.report_date" :class="{ 'energy-selected-day': row.report_date === day?.report_date }"><th scope="row"><button class="energy-date-link" type="button" :disabled="busy || row.is_future" @click="loadDay(false, row.report_date)">{{ row.report_date }}{{ row.is_today ? ' · 今日' : '' }}</button></th><td>{{ valueText(row.electricity) }}</td><td><span class="energy-status" :class="row.electricity.status">{{ statusText(row.electricity) }}</span><small>{{ coverage(row.electricity, '小时') }}</small></td><td>{{ valueText(row.gas) }}</td><td><span class="energy-status" :class="row.gas.status">{{ statusText(row.gas) }}</span><small>{{ coverage(row.gas, '小时') }}</small></td><td :title="timestamp(row.updated_at)">{{ row.is_future ? '日期未到' : row.saved ? '已保存' : '尚未查询' }}</td></tr><tr v-if="!monthRows.length"><td colspan="6" class="energy-empty">{{ monthLoading ? '读取月度记录中…' : '请选择月份查看' }}</td></tr></tbody>
        <tfoot><tr><th scope="row">当月汇总</th><td>{{ valueText(month?.summary?.electricity) }}</td><td>{{ statusText(month?.summary?.electricity) }} · {{ coverage(month?.summary?.electricity, '天') }}</td><td>{{ valueText(month?.summary?.gas) }}</td><td>{{ statusText(month?.summary?.gas) }} · {{ coverage(month?.summary?.gas, '天') }}</td><td>{{ month?.saved_day_count || 0 }} 天已保存</td></tr></tfoot>
      </table>
    </div>
  </section>
  <p class="report-source-note energy-method-note">{{ day?.notice || month?.notice }} 小时/日界取不到读数时不以当天第一条、最后一条替代。累计量口径和质量状态码仍需平台核实，本页面不自动做结算判断。</p>
</template>

<style scoped>
.energy-period-hero { display:flex; justify-content:space-between; align-items:center; gap:24px; padding:28px 0; }
.energy-period-hero h2 { margin:8px 0; font-size:30px; color:#153952; }
.energy-period-hero p { margin:0; color:#637987; font-size:13px; }
.energy-chart-select { display:grid; gap:7px; font-size:12px; color:#526875; min-width:200px; }
.energy-config-note { display:grid; gap:6px; padding:14px 18px; margin-bottom:20px; background:#fff7e9; border:1px solid #eddbb6; color:#85571c; font-size:13px; }
.energy-period-panel { margin-bottom:26px; overflow:hidden; }
.energy-section-heading { display:flex; justify-content:space-between; align-items:center; gap:14px; padding:18px 22px; border-bottom:1px solid #e2e9ee; flex-wrap:wrap; }
.energy-section-heading > div { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
.energy-section-heading h2 { margin:0; font-size:18px; color:#183d53; }
.energy-section-heading small { color:#728595; font-size:12px; }
.energy-section-number { color:#137b99; font-size:12px; border-bottom:2px solid #137b99; padding-bottom:3px; }
.energy-section-heading form { display:flex; gap:8px; align-items:flex-end; flex-wrap:wrap; }
.energy-section-heading label { display:grid; gap:5px; color:#687d8d; font-size:11px; }
.energy-section-heading input { width:150px; }
.energy-section-heading button { font-size:12px; padding:10px 13px; }
.energy-stat-strip { display:grid; grid-template-columns:1fr 1fr 1fr 1.2fr; margin:20px 22px 0; background:#f6f9fb; border:1px solid #e5edf1; }
.energy-stat-strip > div { min-width:0; padding:16px 18px; border-right:1px solid #e1e9ee; }
.energy-stat-strip > div:last-child { border-right:0; }
.energy-stat-strip span { display:block; color:#657d8b; font-size:11px; margin-bottom:8px; }
.energy-stat-strip strong { color:#173f57; font-size:24px; font-variant-numeric:tabular-nums; display:block; }
.energy-stat-strip small { color:#738995; display:block; font-size:11px; margin-top:6px; }
.energy-stat-strip p { margin:6px 0 0; font-size:12px; color:#385368; font-variant-numeric:tabular-nums; }
.energy-inline-warning,.energy-sync-progress,.energy-storage-note { margin:14px 22px; font-size:12px; line-height:1.7; }
.energy-inline-warning { color:#986623; background:#fff9ee; padding:10px 12px; border-left:3px solid #dfa94a; }
.energy-sync-progress { color:#0e647e; background:#edf8fa; padding:8px 12px; }
.energy-storage-note { color:#6e8191; }
.energy-period-chart-wrap { margin:16px 22px; border:1px solid #e6edf1; background:#fff; }
.energy-period-chart { width:100%; height:290px; }
.energy-period-chart-wrap > p { font-size:11px; color:#738797; margin:0; padding:8px 14px 12px; }
.energy-period-table-wrap { margin:0 22px 22px; max-height:440px; overflow:auto; border:1px solid #dbe5eb; }
.energy-month-table-wrap { max-height:510px; }
.energy-period-table { width:100%; min-width:650px; border-collapse:separate; border-spacing:0; font-size:12px; font-variant-numeric:tabular-nums; }
.energy-period-table th,.energy-period-table td { padding:11px 14px; border-bottom:1px solid #e5ecf1; text-align:right; white-space:nowrap; }
.energy-period-table th:first-child,.energy-period-table td:first-child { text-align:left; }
.energy-period-table thead th { position:sticky; top:0; z-index:2; background:#edf3f7; color:#345468; font-weight:600; }
.energy-period-table thead small { display:block; color:#718593; font-weight:400; margin-top:3px; }
.energy-period-table tbody th { font-weight:500; color:#526b7c; }
.energy-period-table tbody tr:nth-child(even) { background:#fbfcfd; }
.energy-period-table tbody tr:hover,.energy-period-table tbody .energy-selected-day { background:#eef7fa; }
.energy-period-table tfoot th,.energy-period-table tfoot td { position:sticky; bottom:0; z-index:2; background:#e5f0f5; color:#164c65; font-weight:700; border-top:2px solid #b9d4e1; border-bottom:0; }
.energy-period-table td small { display:block; color:#84939e; margin-top:3px; font-size:10px; }
.energy-status { font-size:11px; color:#85949e; }
.energy-status.measured { color:#237a68; }
.energy-status.interpolated,.energy-status.partial { color:#a66d19; }
.energy-status.counter_reset,.energy-status.invalid_reading { color:#b84f42; }
.energy-date-link { border:0; background:none; padding:0; color:#176885; cursor:pointer; font-size:12px; }
.energy-date-link:hover { text-decoration:underline; }
.energy-date-link:disabled { color:#91a0aa; cursor:default; }
.energy-empty { height:130px; text-align:center!important; color:#8a9aa5; }
.energy-method-note { line-height:1.9; margin-bottom:30px; }
@media (max-width:900px) { .energy-stat-strip { grid-template-columns:1fr 1fr; }.energy-stat-strip > div { border-bottom:1px solid #e1e9ee; }.energy-period-hero { align-items:flex-start; flex-direction:column; }.energy-section-heading { padding:16px; }.energy-period-table-wrap,.energy-period-chart-wrap { margin-left:14px; margin-right:14px; }.energy-stat-strip { margin-left:14px; margin-right:14px; } }
@media (max-width:560px) { .energy-section-heading form { width:100%; }.energy-section-heading input { width:145px; }.energy-stat-strip > div { padding:12px; }.energy-stat-strip strong { font-size:21px; }.energy-period-chart { height:260px; }.energy-period-hero h2 { font-size:25px; } }
</style>
