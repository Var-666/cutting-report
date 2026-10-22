<script setup>
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { init, use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { getAlarmStatus, getEnergyMonth, getLeadershipReport, listAlarmIncidents, listReports } from '../api/reports'
import { reportToday } from '../api/reportDates.js'
import AppIcon from '../components/AppIcon.vue'
import HomeKpiCard from '../components/HomeKpiCard.vue'
import HomeSectionHeader from '../components/HomeSectionHeader.vue'
import { session } from '../state/session'
import { formatChineseDate, formatChineseDateTime, formatChineseMonth } from '../utils/dateDisplay.js'
import { formatDeviceDisplay } from '../utils/deviceDisplay.js'
import heroImage from '../assets/home-laser-cutting.webp'

use([BarChart, LineChart, PieChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

const reportDate = reportToday()
const reportMonth = reportDate.slice(0, 7)
const overviewLoading = ref(true)
const historyLoading = ref(true)
const energyLoading = ref(true)
const alarmLoading = ref(true)
const overview = ref(null)
const energy = ref(null)
const alarmStatus = ref(null)
const incidents = ref([])
const recentReports = ref([])
const historyTotal = ref(0)
const error = ref('')
const currentTime = ref(new Date())
const trendElement = ref(null)
const statusElement = ref(null)
let trendChart = null
let statusChart = null
let clockTimer = null

const total = computed(() => overview.value?.current_month_total || { task_count: 0, cut_length_m: 0, work_time_seconds: 0 })
const previousTotal = computed(() => overview.value?.comparison_total || {})
const dailyRecords = computed(() => [...(overview.value?.daily_records || [])].sort((left, right) => left.period_start.localeCompare(right.period_start)))
const activeDays = computed(() => dailyRecords.value.filter((item) => Number(item.task_count) > 0).length)
const elapsedDays = computed(() => Math.max(activeDays.value, Number(energy.value?.elapsed_day_count || reportDate.slice(8))))
const energySummary = computed(() => energy.value?.summary || {})
const energyConnected = computed(() => energy.value?.energy_connected !== false)
const electricity = computed(() => energySummary.value.electricity?.value)
const gas = computed(() => energySummary.value.gas?.value)
const openAlarms = computed(() => incidents.value.filter((item) => item.status === 'open'))
const dataSourceLabel = computed(() => ({ demo: '演示数据', sqlserver: 'SQL Server' }[session.systemInfo?.data_source_mode] || '正在检查'))
const displayDate = computed(() => new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: 'long', day: 'numeric', weekday: 'short', timeZone: 'Asia/Shanghai' }).format(currentTime.value))
const displayTime = computed(() => new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false, timeZone: 'Asia/Shanghai' }).format(currentTime.value))
const sourceUpdateTime = computed(() => formatChineseDateTime(overview.value?.snapshot?.data_as_of).split(' ').at(-1))
const systemState = computed(() => error.value ? '部分数据待检查' : overviewLoading.value ? '正在更新' : '服务正常')
const metricSeries = computed(() => ({
  tasks: dailyRecords.value.map((item) => item.task_count),
  length: dailyRecords.value.map((item) => item.cut_length_m),
  hours: dailyRecords.value.map((item) => Number(item.work_time_seconds || 0) / 3600),
  days: dailyRecords.value.map((item) => Number(item.task_count) > 0 ? 1 : 0),
  electricity: (energy.value?.rows || []).filter((item) => !item.is_future).map((item) => item.electricity?.value),
  gas: (energy.value?.rows || []).filter((item) => !item.is_future).map((item) => item.gas?.value),
}))

const reportTypeText = { leadership: '领导报表', workshop: '车间报表', equipment: '设备报表', custom: '自定义报表' }
const statusText = { pending: '等待中', querying: '查询中', calculating: '统计中', analyzing: '分析中', rendering: '渲染中', completed: '已完成', failed: '失败' }
const severityText = { critical: '严重', high: '高', medium: '一般', low: '提示' }

function formatNumber(value, digits = 1) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '--'
  return new Intl.NumberFormat('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(Number(value))
}

function compactDuration(value) {
  const seconds = Math.max(0, Math.round(Number(value || 0)))
  return `${Math.floor(seconds / 3600)}小时 ${String(Math.floor((seconds % 3600) / 60)).padStart(2, '0')}分`
}

function changeRate(current, previous) {
  const now = Number(current || 0)
  const before = Number(previous || 0)
  if (!before) return { value: now ? 100 : 0, direction: now ? 'up' : 'flat' }
  const value = Math.round(((now - before) / Math.abs(before)) * 100)
  return { value: Math.abs(value), direction: value > 0 ? 'up' : value < 0 ? 'down' : 'flat' }
}

function comparisonStatus(current, previous) {
  if (previous === null || previous === undefined || Number(previous) === 0) return { text: '暂无上月对比', direction: 'flat' }
  const change = changeRate(current, previous)
  if (change.direction === 'flat') return { text: '与上月持平', direction: 'flat' }
  return { text: `${change.direction === 'up' ? '↑' : '↓'} ${change.value}% 较上月`, direction: change.direction }
}

const taskComparison = computed(() => comparisonStatus(total.value.task_count, previousTotal.value.task_count))
const lengthComparison = computed(() => comparisonStatus(total.value.cut_length_m, previousTotal.value.cut_length_m))
const timeComparison = computed(() => comparisonStatus(total.value.work_time_seconds, previousTotal.value.work_time_seconds))
const insightTrend = computed(() => {
  if (lengthComparison.value.direction === 'flat') return lengthComparison.value.text === '暂无上月对比' ? '暂无可比较的切割长度数据' : '切割长度与上月持平'
  return `切割长度${lengthComparison.value.direction === 'up' ? '高于' : '低于'}上月 ${lengthComparison.value.text.match(/\d+%/)?.[0] || ''}`
})
const insightAdvice = computed(() => openAlarms.value.length ? '优先处理未解除设备告警' : !energyConnected.value ? '当前设备尚未接入能耗仪表' : electricity.value == null || gas.value == null ? '先核对能耗数据是否完整' : '持续关注生产与能耗变化')

function reportPeriod(item) {
  if (!item?.report_date) return '--'
  return item.report_type === 'leadership' ? formatChineseMonth(item.report_date) : formatChineseDate(item.report_date)
}

function shortDateTime(value) {
  if (!value) return '--'
  return formatChineseDateTime(value).replace(/年|月/g, '-').replace('日', '')
}

function resizeCharts() { trendChart?.resize(); statusChart?.resize() }

function renderCharts() {
  if (trendElement.value && overview.value) {
    trendChart ||= init(trendElement.value)
    const rows = dailyRecords.value.slice(-7)
    trendChart.setOption({
      animationDuration: 240,
      color: ['#3284d6', '#16a3b6'],
      tooltip: { trigger: 'axis', borderWidth: 0, backgroundColor: '#12374f', textStyle: { color: '#fff', fontSize: 12 } },
      legend: { top: 4, right: 8, itemWidth: 11, itemHeight: 7, textStyle: { color: '#415e75', fontSize: 12 } },
      grid: { left: 48, right: 44, top: 38, bottom: 31 },
      xAxis: { type: 'category', data: rows.map((item) => item.period_start.slice(5)), axisTick: { show: false }, axisLine: { lineStyle: { color: '#dfe7ee' } }, axisLabel: { color: '#415e75', fontSize: 12 } },
      yAxis: [{ type: 'value', splitNumber: 3, splitLine: { lineStyle: { color: '#e8edf2' } }, axisLabel: { color: '#415e75', fontSize: 12 } }, { type: 'value', splitNumber: 3, splitLine: { show: false }, axisLabel: { show: false } }],
      series: [
        { name: '切割长度（米）', type: 'bar', barMaxWidth: 28, data: rows.map((item) => Number(item.cut_length_m || 0).toFixed(1)), itemStyle: { borderRadius: [2, 2, 0, 0] } },
        { name: '设备工时（小时）', type: 'line', yAxisIndex: 1, smooth: true, symbolSize: 6, data: rows.map((item) => (Number(item.work_time_seconds || 0) / 3600).toFixed(1)), lineStyle: { width: 2 }, areaStyle: { opacity: .06 } },
      ],
    }, true)
  }
  if (statusElement.value && overview.value) {
    statusChart ||= init(statusElement.value)
    const active = activeDays.value
    const idle = Math.max(0, elapsedDays.value - active)
    statusChart.setOption({
      animationDuration: 240,
      color: ['#2a9d8f', '#b8cbd6'],
      tooltip: { trigger: 'item', formatter: '{b}：{c} 天' },
      series: [{ type: 'pie', radius: ['61%', '79%'], center: ['50%', '50%'], label: { show: false }, emphasis: { scaleSize: 3 }, data: [{ name: '生产日', value: active }, { name: '无生产记录', value: idle || 0.001 }] }],
    }, true)
  }
}

async function loadDashboard() {
  trendChart?.dispose()
  statusChart?.dispose()
  trendChart = null
  statusChart = null
  overviewLoading.value = true
  historyLoading.value = true
  energyLoading.value = true
  alarmLoading.value = true
  error.value = ''
  const overviewRequest = getLeadershipReport(reportMonth)
    .then((data) => { overview.value = data })
    .catch((exception) => { error.value = exception.message })
    .finally(async () => { overviewLoading.value = false; await nextTick(); renderCharts() })
  const historyRequest = listReports({ page: 1, pageSize: 10 })
    .then((data) => { recentReports.value = data.items.slice(0, 3); historyTotal.value = data.total })
    .catch((exception) => { error.value ||= exception.message })
    .finally(() => { historyLoading.value = false })
  const energyRequest = getEnergyMonth(reportMonth)
    .then(async (data) => { energy.value = data; await nextTick(); renderCharts() })
    .catch((exception) => { error.value ||= exception.message })
    .finally(() => { energyLoading.value = false })
  const alarmRequest = Promise.all([getAlarmStatus(), listAlarmIncidents({ limit: 4 })])
    .then(([status, rows]) => { alarmStatus.value = status; incidents.value = rows })
    .catch((exception) => { error.value ||= exception.message })
    .finally(() => { alarmLoading.value = false })
  await Promise.allSettled([overviewRequest, historyRequest, energyRequest, alarmRequest])
}

onMounted(() => {
  loadDashboard()
  clockTimer = window.setInterval(() => { currentTime.value = new Date() }, 1000)
  window.addEventListener('resize', resizeCharts)
})
onBeforeUnmount(() => {
  window.clearInterval(clockTimer)
  window.removeEventListener('resize', resizeCharts)
  trendChart?.dispose()
  statusChart?.dispose()
})
</script>

<template>
  <div class="command-home">
    <section class="command-hero" aria-labelledby="command-home-title" :style="{ '--hero-image': `url(${heroImage})` }">
      <div class="command-hero__copy">
        <span class="command-eyebrow">PRODUCTION COMMAND CENTER</span>
        <h1 id="command-home-title">生产指挥中心</h1>
        <p class="command-subtitle">数据驱动生产 · 智能助力制造</p>
        <p class="command-intro">{{ session.user?.display_name }}，欢迎进入切割生产智能产报系统。<br>从这里查看生产运行状态、掌握关键指标，快速进入各类报表与管理工具。</p>
        <div class="command-date"><AppIcon name="calendar" :size="17" /><span>{{ displayDate }}</span><b>{{ displayTime }}</b></div>
      </div>
      <aside class="system-summary" aria-label="系统运行状态">
        <div class="system-summary__head"><div><small>系统运行状态</small><strong role="status" aria-live="polite" :class="{ 'needs-attention': !!error }"><i></i>{{ systemState }}</strong></div><RouterLink to="/admin/settings/database" aria-label="查看数据源状态" title="查看数据源状态"><AppIcon name="chevron-right" :size="17" /></RouterLink></div>
        <dl>
          <div><dt>当前数据库</dt><dd>{{ dataSourceLabel }}</dd></div>
          <div><dt>系统版本</dt><dd>V{{ session.systemInfo?.version || '0.1.0' }}</dd></div>
          <div><dt>监控状态</dt><dd>{{ alarmStatus?.enabled ? (alarmStatus.worker_running ? '运行中' : '等待启动') : '未启用' }}</dd></div>
          <div><dt>最近更新</dt><dd>{{ sourceUpdateTime }}</dd></div>
        </dl>
      </aside>
      <div class="command-hero__slogan"><strong>智能制造</strong><span>从数据看见更好的生产</span></div>
    </section>

    <div v-if="error" class="global-error home-error" role="alert"><strong>部分概览数据读取失败</strong><span>{{ error }}</span><button type="button" @click="loadDashboard">重试</button></div>

    <section class="command-kpis" aria-label="本月生产概况">
      <HomeKpiCard title="本月任务" icon="tasks" tone="blue" :value="total.task_count" unit="项" :status="taskComparison.text" :direction="taskComparison.direction" :series="metricSeries.tasks" :loading="overviewLoading" />
      <HomeKpiCard title="切割长度" icon="ruler" tone="teal" :value="formatNumber(total.cut_length_m)" unit="米" :status="lengthComparison.text" :direction="lengthComparison.direction" :series="metricSeries.length" :loading="overviewLoading" />
      <HomeKpiCard title="设备工时" icon="clock" tone="purple" :value="compactDuration(total.work_time_seconds)" :status="timeComparison.text" :direction="timeComparison.direction" :series="metricSeries.hours" :loading="overviewLoading" />
      <HomeKpiCard title="生产天数" icon="factory" tone="green" :value="activeDays" unit="天" status="本月有记录" direction="up" :series="metricSeries.days" :loading="overviewLoading" />
      <HomeKpiCard title="电耗" icon="zap" tone="amber" :value="energyConnected ? formatNumber(electricity) : '暂未接入'" :unit="energy?.units?.electricity || 'kWh'" :status="!energyConnected ? '电表暂未接入' : electricity == null ? '暂无数据' : `已汇总 ${energy.saved_day_count || 0} 天`" :direction="electricity == null ? 'flat' : 'up'" :series="metricSeries.electricity" :loading="energyLoading" />
      <HomeKpiCard title="气耗" icon="wind" tone="sky" :value="energyConnected ? formatNumber(gas) : '暂未接入'" :unit="energy?.units?.gas || 'Nm³'" :status="!energyConnected ? '气表暂未接入' : gas == null ? '暂无数据' : `已汇总 ${energy.saved_day_count || 0} 天`" :direction="gas == null ? 'flat' : 'up'" :series="metricSeries.gas" :loading="energyLoading" />
    </section>

    <section class="dashboard-grid" aria-label="生产看板">
      <article class="dashboard-card trend-card" aria-labelledby="trend-card-title">
        <HomeSectionHeader heading-id="trend-card-title" title="生产趋势" icon="trend" to="/admin/reports/leadership" action="近7天" tone="blue" />
        <div v-if="overviewLoading" class="card-loading" role="status">正在获取生产数据...</div>
        <div v-else-if="!overview" class="card-empty" role="status"><strong>暂无趋势数据</strong><small>请检查生产报表数据后重试</small></div>
        <div v-else ref="trendElement" class="trend-chart" role="img" aria-label="近七天切割长度与设备工时趋势图"></div>
      </article>
      <article class="dashboard-card status-card" aria-labelledby="status-card-title">
        <HomeSectionHeader heading-id="status-card-title" title="生产状态" icon="activity" to="/admin/reports/workshop" action="查看全部" />
        <div v-if="overviewLoading" class="card-loading" role="status">正在获取生产数据...</div>
        <div v-else-if="!overview" class="card-empty" role="status"><strong>暂无生产状态</strong><small>请检查车间报表数据</small></div>
        <template v-else>
          <div class="status-content"><div class="status-visual"><div ref="statusElement" class="status-chart" role="img" aria-label="本月生产日状态图"></div><div class="status-visual__value" aria-hidden="true"><strong>{{ activeDays }}</strong><span>生产天数</span></div></div><ul><li><i class="running"></i><span>生产日</span><b>{{ activeDays }} 天</b><small>{{ elapsedDays ? Math.round(activeDays / elapsedDays * 100) : 0 }}%</small></li><li><i class="idle"></i><span>无生产记录</span><b>{{ Math.max(0, elapsedDays - activeDays) }} 天</b><small>{{ elapsedDays ? Math.round(Math.max(0, elapsedDays - activeDays) / elapsedDays * 100) : 0 }}%</small></li><li><i class="offline"></i><span>统计周期</span><b>{{ elapsedDays }} 天</b><small>—</small></li></ul></div>
          <p class="status-note"><AppIcon name="check" :size="15" />本月已记录 {{ activeDays }} 个生产日{{ overview?.snapshot?.stale ? '，报表快照待更新' : '，数据持续更新中' }}</p>
        </template>
      </article>
      <article class="dashboard-card alarm-card" aria-labelledby="alarm-card-title">
        <HomeSectionHeader heading-id="alarm-card-title" title="最近告警" icon="bell" to="/admin/alarms" action="查看全部" tone="amber" />
        <div v-if="alarmLoading" class="card-loading" role="status">正在检查设备状态...</div>
        <div v-else-if="!incidents.length" class="card-empty" role="status"><span class="card-empty__icon"><AppIcon name="bell" :size="25" /></span><strong>当前暂无告警</strong><small>{{ alarmStatus?.enabled ? '系统持续监控设备状态' : '告警监控未启用' }}</small></div>
        <div v-else class="alarm-list"><RouterLink v-for="item in incidents.slice(0, 4)" :key="item.incident_id" to="/admin/alarms" class="alarm-row"><span class="alarm-symbol" :class="item.severity"><AppIcon name="alert" :size="15" /></span><div><strong>{{ item.alarm_desc || item.alarm_code || '设备告警' }}</strong><small>{{ severityText[item.severity] || '告警' }} · {{ formatDeviceDisplay(item.machine_no) || '切割设备' }} · {{ item.alarm_class || '运行监控' }}</small></div><time>{{ shortDateTime(item.last_seen_at) }}</time><em :class="item.status">{{ item.status === 'open' ? '未解除' : '已解除' }}</em></RouterLink></div>
      </article>
      <article class="dashboard-card reports-card" aria-labelledby="reports-card-title">
        <HomeSectionHeader heading-id="reports-card-title" title="最近生成报告" icon="file" to="/admin/reports/center" :action="`查看全部${historyTotal ? ` ${historyTotal}` : ''}`" />
        <div v-if="historyLoading" class="card-loading" role="status">正在获取报告...</div>
        <div v-else-if="!recentReports.length" class="card-empty compact" role="status"><strong>暂无已生成报告</strong><small>可从报表中心生成第一份报告</small></div>
        <div v-else class="report-list"><div v-for="item in recentReports" :key="item.report_id" class="report-row"><span class="report-symbol"><AppIcon name="file" :size="16" /></span><strong>{{ item.report_title || reportTypeText[item.report_type] || '生产报表' }}</strong><span>{{ reportPeriod(item) }}</span><span>{{ item.task_count ?? '--' }} 项</span><em :data-status="item.status">{{ statusText[item.status] || item.status }}</em><time>{{ shortDateTime(item.generated_at || item.created_at) }}</time><a v-if="item.view_url" :href="item.view_url" target="_blank" rel="noopener">打开</a><span v-else>--</span></div></div>
      </article>
      <article class="dashboard-card quick-card" aria-labelledby="quick-card-title">
        <HomeSectionHeader heading-id="quick-card-title" title="快捷入口" icon="grid" />
        <nav class="quick-links" aria-label="核心报表快速入口">
          <RouterLink class="quick-link quick-link--leadership" to="/admin/reports/leadership"><span class="quick-link__icon"><AppIcon name="briefcase" :size="20" /></span><span class="quick-link__copy"><strong>领导报表</strong><small>全面掌握生产情况</small></span><span class="quick-link__arrow" aria-hidden="true"><AppIcon name="chevron-right" :size="15" /></span></RouterLink>
          <RouterLink class="quick-link quick-link--workshop" to="/admin/reports/workshop"><span class="quick-link__icon"><AppIcon name="factory" :size="20" /></span><span class="quick-link__copy"><strong>车间报表</strong><small>查看生产过程与任务记录</small></span><span class="quick-link__arrow" aria-hidden="true"><AppIcon name="chevron-right" :size="15" /></span></RouterLink>
          <RouterLink class="quick-link quick-link--equipment" to="/admin/reports/equipment"><span class="quick-link__icon"><AppIcon name="chart" :size="20" /></span><span class="quick-link__copy"><strong>设备报表</strong><small>查看设备运行与能耗</small></span><span class="quick-link__arrow" aria-hidden="true"><AppIcon name="chevron-right" :size="15" /></span></RouterLink>
        </nav>
      </article>
      <article class="dashboard-card insights-card" aria-labelledby="insights-card-title">
        <HomeSectionHeader heading-id="insights-card-title" title="智能洞察" icon="sparkles" to="/admin/reports/leadership" action="查看更多" tone="purple" />
        <div v-if="overviewLoading || alarmLoading" class="card-loading" role="status">正在分析生产数据...</div>
        <div v-else-if="!overview" class="card-empty compact" role="status"><strong>暂无生产洞察</strong><small>请检查生产数据后重试</small></div>
        <div v-else class="insight-grid"><div class="insight-item insight-item--blue"><span><AppIcon name="chart" :size="19" /></span><p><strong>生产概况</strong><small>本月完成 {{ total.task_count }} 项任务</small></p></div><div class="insight-item" :class="openAlarms.length ? 'insight-item--danger' : 'insight-item--green'"><span><AppIcon :name="openAlarms.length ? 'alert' : 'check'" :size="19" /></span><p><strong>异常发现</strong><small>{{ openAlarms.length ? `发现 ${openAlarms.length} 项未解除告警` : '当前未发现异常告警' }}</small></p></div><div class="insight-item insight-item--teal"><span><AppIcon name="trend" :size="19" /></span><p><strong>趋势变化</strong><small>{{ insightTrend }}</small></p></div><div class="insight-item insight-item--amber"><span><AppIcon name="lightbulb" :size="19" /></span><p><strong>建议关注</strong><small>{{ insightAdvice }}</small></p></div></div>
      </article>
    </section>
  </div>
</template>

<style scoped src="../assets/admin-home.css"></style>
