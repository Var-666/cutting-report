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
import { session } from '../state/session'
import { formatChineseDate, formatChineseDateTime, formatChineseMonth } from '../utils/dateDisplay.js'
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
const electricity = computed(() => energySummary.value.electricity?.value)
const gas = computed(() => energySummary.value.gas?.value)
const openAlarms = computed(() => incidents.value.filter((item) => item.status === 'open'))
const dataSourceLabel = computed(() => ({ demo: '演示数据', excel: 'TASK.xlsx', sqlserver: 'SQL Server' }[session.systemInfo?.data_source_mode] || '正在检查'))
const displayDate = computed(() => new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: 'long', day: 'numeric', weekday: 'short', timeZone: 'Asia/Shanghai' }).format(currentTime.value))
const displayTime = computed(() => new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false, timeZone: 'Asia/Shanghai' }).format(currentTime.value))

const reportTypeText = { leadership: '领导报表', workshop: '车间报表', equipment: '设备报表', custom: '自定义报表' }
const statusText = { pending: '等待中', querying: '查询中', calculating: '统计中', analyzing: '分析中', rendering: '渲染中', completed: '已完成', completed_with_warnings: '有提示', failed: '失败' }

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
      animationDuration: 550,
      color: ['#3394eb', '#19a6a4'],
      tooltip: { trigger: 'axis', borderWidth: 0, backgroundColor: 'rgba(10,47,75,.92)', textStyle: { color: '#fff', fontSize: 12 } },
      legend: { top: 2, right: 0, itemWidth: 12, itemHeight: 7, textStyle: { color: '#6d8190', fontSize: 11 } },
      grid: { left: 42, right: 40, top: 38, bottom: 28 },
      xAxis: { type: 'category', data: rows.map((item) => item.period_start.slice(5)), axisTick: { show: false }, axisLine: { lineStyle: { color: '#dbe5eb' } }, axisLabel: { color: '#738898', fontSize: 10 } },
      yAxis: [{ type: 'value', splitNumber: 3, splitLine: { lineStyle: { color: '#edf2f5' } }, axisLabel: { color: '#8a9aa5', fontSize: 10 } }, { type: 'value', splitNumber: 3, splitLine: { show: false }, axisLabel: { show: false } }],
      series: [
        { name: '切割长度（米）', type: 'bar', barMaxWidth: 26, data: rows.map((item) => Number(item.cut_length_m || 0).toFixed(1)), itemStyle: { borderRadius: [2, 2, 0, 0] } },
        { name: '设备工时（小时）', type: 'line', yAxisIndex: 1, smooth: true, symbolSize: 6, data: rows.map((item) => (Number(item.work_time_seconds || 0) / 3600).toFixed(1)), lineStyle: { width: 2 }, areaStyle: { opacity: .07 } },
      ],
    }, true)
  }
  if (statusElement.value) {
    statusChart ||= init(statusElement.value)
    const active = activeDays.value
    const idle = Math.max(0, elapsedDays.value - active)
    statusChart.setOption({
      animationDuration: 550,
      color: ['#18a8a5', '#78baf0'],
      tooltip: { trigger: 'item', formatter: '{b}：{c} 天' },
      graphic: [{ type: 'text', left: 'center', top: '41%', style: { text: `已生产\n${active} 天`, textAlign: 'center', fill: '#173b56', font: '700 14px "Microsoft YaHei", "PingFang SC", Arial, sans-serif', lineHeight: 21 } }],
      series: [{ type: 'pie', radius: ['58%', '78%'], center: ['50%', '50%'], label: { show: false }, emphasis: { scaleSize: 4 }, data: [{ name: '生产日', value: active }, { name: '无生产记录', value: idle || 0.001 }] }],
    }, true)
  }
}

async function loadDashboard() {
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
        <div class="command-date"><AppIcon name="calendar" :size="14"/>{{ displayDate }}<b>{{ displayTime }}</b></div>
      </div>
      <aside class="system-summary" aria-label="系统运行状态">
        <div class="system-summary__head"><div><small>系统运行状态</small><strong role="status" aria-live="polite"><i></i>服务正常</strong></div><RouterLink to="/admin/settings/database" aria-label="查看数据源状态" title="查看数据源状态"><AppIcon name="chevron-right" :size="14"/></RouterLink></div>
        <dl>
          <div><dt>当前数据库</dt><dd>{{ dataSourceLabel }}</dd></div>
          <div><dt>系统版本</dt><dd>V{{ session.systemInfo?.version || '0.1.0' }}</dd></div>
          <div><dt>监控状态</dt><dd>{{ alarmStatus?.enabled ? (alarmStatus.worker_running ? '运行中' : '等待启动') : '未启用' }}</dd></div>
          <div><dt>最近更新</dt><dd>{{ displayTime }}</dd></div>
        </dl>
      </aside>
      <div class="command-hero__slogan"><strong>智能制造</strong><span>从数据看见更好的生产</span></div>
    </section>

    <div v-if="error" class="global-error home-error" role="alert"><strong>部分概览数据读取失败</strong><span>{{ error }}</span><button type="button" @click="loadDashboard">重试</button></div>

    <section class="command-kpis" aria-label="本月生产概况">
      <article class="kpi-blue"><span class="metric-icon"><AppIcon name="tasks" :size="18"/></span><div><span>本月任务<small>TASKS</small></span><p><strong>{{ overviewLoading ? '--' : total.task_count }}</strong> 项</p><em :class="changeRate(total.task_count, previousTotal.task_count).direction"><AppIcon :name="changeRate(total.task_count, previousTotal.task_count).direction === 'down' ? 'arrow-down' : 'arrow-up'" :size="11"/>{{ changeRate(total.task_count, previousTotal.task_count).value }}% <small>较上期</small></em></div></article>
      <article class="kpi-teal"><span class="metric-icon"><AppIcon name="ruler" :size="18"/></span><div><span>切割长度<small>CUTTING LENGTH</small></span><p><strong>{{ overviewLoading ? '--' : formatNumber(total.cut_length_m) }}</strong> 米</p><em :class="changeRate(total.cut_length_m, previousTotal.cut_length_m).direction"><AppIcon :name="changeRate(total.cut_length_m, previousTotal.cut_length_m).direction === 'down' ? 'arrow-down' : 'arrow-up'" :size="11"/>{{ changeRate(total.cut_length_m, previousTotal.cut_length_m).value }}% <small>较上期</small></em></div></article>
      <article class="kpi-indigo"><span class="metric-icon"><AppIcon name="clock" :size="18"/></span><div><span>设备工时<small>EQUIPMENT HOURS</small></span><p><strong>{{ overviewLoading ? '--' : compactDuration(total.work_time_seconds) }}</strong></p><em :class="changeRate(total.work_time_seconds, previousTotal.work_time_seconds).direction"><AppIcon :name="changeRate(total.work_time_seconds, previousTotal.work_time_seconds).direction === 'down' ? 'arrow-down' : 'arrow-up'" :size="11"/>{{ changeRate(total.work_time_seconds, previousTotal.work_time_seconds).value }}% <small>较上期</small></em></div></article>
      <article class="kpi-green"><span class="metric-icon"><AppIcon name="factory" :size="18"/></span><div><span>生产天数<small>PRODUCTION DAYS</small></span><p><strong>{{ overviewLoading ? '--' : activeDays }}</strong> 天</p><em class="up"><AppIcon name="check" :size="11"/><small>本月有记录</small></em></div></article>
      <article class="kpi-amber"><span class="metric-icon"><AppIcon name="zap" :size="18"/></span><div><span>电耗<small>POWER CONSUMPTION</small></span><p><strong>{{ energyLoading ? '--' : formatNumber(electricity) }}</strong> {{ energy?.units?.electricity || 'kWh' }}</p><em :class="electricity == null ? 'flat' : 'up'"><AppIcon :name="electricity == null ? 'activity' : 'check'" :size="11"/>{{ electricity == null ? '暂无数据' : `已汇总 ${energy.saved_day_count || 0} 天` }}</em></div></article>
      <article class="kpi-sky"><span class="metric-icon"><AppIcon name="wind" :size="18"/></span><div><span>气耗<small>GAS CONSUMPTION</small></span><p><strong>{{ energyLoading ? '--' : formatNumber(gas) }}</strong> {{ energy?.units?.gas || 'Nm³' }}</p><em :class="gas == null ? 'flat' : 'up'"><AppIcon :name="gas == null ? 'activity' : 'check'" :size="11"/>{{ gas == null ? '暂无数据' : `已汇总 ${energy.saved_day_count || 0} 天` }}</em></div></article>
    </section>

    <section class="dashboard-grid" aria-label="生产看板">
      <article class="dashboard-card trend-card" aria-labelledby="trend-card-title">
        <header><div><span class="section-icon"><AppIcon name="trend" :size="16"/></span><h2 id="trend-card-title">生产趋势<small>PRODUCTION TREND</small></h2></div><RouterLink to="/admin/reports/leadership" aria-label="查看近七天生产趋势详情">近7天 <AppIcon name="chevron-right" :size="11"/></RouterLink></header>
        <div v-if="overviewLoading" class="card-loading" role="status" aria-live="polite"><span class="spinner dark"></span>正在读取趋势…</div><div v-else ref="trendElement" class="trend-chart" role="img" aria-label="近七天切割长度与设备工时趋势图"></div>
      </article>
      <article class="dashboard-card status-card" aria-labelledby="status-card-title">
        <header><div><span class="section-icon"><AppIcon name="activity" :size="16"/></span><h2 id="status-card-title">生产状态<small>PRODUCTION STATUS</small></h2></div><RouterLink to="/admin/reports/workshop" aria-label="查看全部生产状态">查看全部 <AppIcon name="chevron-right" :size="11"/></RouterLink></header>
        <div class="status-content"><div ref="statusElement" class="status-chart" role="img" aria-label="本月生产日状态图"></div><ul><li><i class="running"></i><span>生产日</span><b>{{ activeDays }}</b><small>{{ elapsedDays ? Math.round(activeDays / elapsedDays * 100) : 0 }}%</small></li><li><i class="idle"></i><span>无生产记录</span><b>{{ Math.max(0, elapsedDays - activeDays) }}</b><small>{{ elapsedDays ? Math.round(Math.max(0, elapsedDays - activeDays) / elapsedDays * 100) : 0 }}%</small></li><li><i class="offline"></i><span>统计周期</span><b>{{ elapsedDays }}</b><small>天</small></li></ul></div>
        <p class="status-note"><span><AppIcon name="check" tone="light" :size="11"/></span> 本月已记录 {{ activeDays }} 个生产日，数据持续更新中</p>
      </article>
      <article class="dashboard-card alarm-card" aria-labelledby="alarm-card-title">
        <header><div><span class="section-icon alert"><AppIcon name="bell" :size="16"/></span><h2 id="alarm-card-title">最近告警<small>RECENT ALARMS</small></h2></div><RouterLink to="/admin/alarms" aria-label="查看全部告警">查看全部 <AppIcon name="chevron-right" :size="11"/></RouterLink></header>
        <div v-if="alarmLoading" class="card-loading" role="status" aria-live="polite"><span class="spinner dark"></span>正在读取告警…</div><div v-else-if="!incidents.length" class="card-empty" role="status"><span><AppIcon name="check" tone="light" :size="17"/></span><strong>当前暂无告警</strong><small>系统将持续监控设备状态</small></div><div v-else class="alarm-list"><RouterLink v-for="item in incidents.slice(0, 4)" :key="item.incident_id" to="/admin/alarms" class="alarm-row"><span class="alarm-symbol" :class="item.severity"><AppIcon name="alert" :size="13"/></span><div><strong>{{ item.alarm_desc || item.alarm_code || '设备告警' }}</strong><small>{{ item.machine_no || '切割设备' }} · {{ item.alarm_class || '运行监控' }}</small></div><time>{{ shortDateTime(item.last_seen_at) }}</time><em :class="item.status">{{ item.status === 'open' ? '未解除' : '已解除' }}</em></RouterLink></div>
      </article>
      <article class="dashboard-card reports-card" aria-labelledby="reports-card-title">
        <header><div><span class="section-icon"><AppIcon name="file" :size="16"/></span><h2 id="reports-card-title">最近生成报告<small>RECENT REPORTS</small></h2></div><RouterLink to="/admin/reports/center" aria-label="查看全部生成报告">查看全部{{ historyTotal ? ` ${historyTotal}` : '' }} <AppIcon name="chevron-right" :size="11"/></RouterLink></header>
        <div v-if="historyLoading" class="card-loading" role="status" aria-live="polite"><span class="spinner dark"></span>正在读取报告…</div><div v-else-if="!recentReports.length" class="card-empty compact" role="status"><strong>暂无已生成报告</strong><small>可从报表中心生成第一份报告</small></div><div v-else class="report-list"><div v-for="item in recentReports" :key="item.report_id" class="report-row"><span class="report-symbol"><AppIcon name="file" :size="12"/></span><strong>{{ item.report_title || reportTypeText[item.report_type] || '生产报表' }}</strong><span>{{ reportPeriod(item) }}</span><span>{{ item.task_count ?? '--' }} 项</span><em :data-status="item.status">{{ statusText[item.status] || item.status }}</em><time>{{ shortDateTime(item.generated_at || item.created_at) }}</time><a v-if="item.view_url" :href="item.view_url" target="_blank" rel="noopener">打开</a><span v-else>--</span></div></div>
      </article>
      <article class="dashboard-card quick-card" aria-labelledby="quick-card-title">
        <header><div><span class="section-icon"><AppIcon name="grid" :size="16"/></span><h2 id="quick-card-title">快速入口<small>QUICK ACCESS</small></h2></div></header><div class="quick-links"><RouterLink to="/admin/reports/leadership"><span><AppIcon name="briefcase" :size="17"/></span><strong>领导报表</strong><small>管理层数据总览</small><b><AppIcon name="chevron-right" :size="12"/></b></RouterLink><RouterLink to="/admin/reports/workshop"><span><AppIcon name="factory" :size="17"/></span><strong>车间报表</strong><small>车间生产明细</small><b><AppIcon name="chevron-right" :size="12"/></b></RouterLink><RouterLink to="/admin/reports/equipment"><span><AppIcon name="chart" :size="17"/></span><strong>设备报表</strong><small>设备能耗分析</small><b><AppIcon name="chevron-right" :size="12"/></b></RouterLink></div>
      </article>
      <article class="dashboard-card insights-card" aria-labelledby="insights-card-title">
        <header><div><span class="section-icon"><AppIcon name="sparkles" :size="16"/></span><h2 id="insights-card-title">智能洞察<small>AI INSIGHTS</small></h2></div><RouterLink to="/admin/reports/leadership" aria-label="查看更多智能洞察">查看更多 <AppIcon name="chevron-right" :size="11"/></RouterLink></header><div class="insight-grid"><div class="positive"><span><AppIcon name="chart" :size="18"/></span><p><strong>生产概况</strong><small>本月完成 {{ total.task_count || 0 }} 项任务</small></p></div><div :class="openAlarms.length ? 'danger' : 'positive'"><span><AppIcon :name="openAlarms.length ? 'alert' : 'check'" :size="18"/></span><p><strong>异常发现</strong><small>{{ openAlarms.length ? `发现 ${openAlarms.length} 项未解除告警` : '当前未发现异常告警' }}</small></p></div><div class="info"><span><AppIcon name="trend" :size="18"/></span><p><strong>趋势变化</strong><small>切割长度{{ changeRate(total.cut_length_m, previousTotal.cut_length_m).direction === 'down' ? '低于' : '高于' }}对比周期</small></p></div><div class="suggest"><span><AppIcon name="lightbulb" :size="18"/></span><p><strong>建议关注</strong><small>{{ openAlarms.length ? '优先处理未解除设备告警' : '生产运行平稳，持续关注能耗' }}</small></p></div></div>
      </article>
    </section>
  </div>
</template>

<style scoped>
.command-home{padding:12px 0 2px;color:#17364f}.command-hero{--hero-image:none;position:relative;min-height:222px;display:grid;grid-template-columns:minmax(390px,1.12fr) 300px minmax(310px,1fr);align-items:center;gap:28px;overflow:hidden;padding:26px 38px;border-radius:8px;background:linear-gradient(90deg,#f8fcff 0%,rgba(245,251,255,.97) 42%,rgba(231,244,251,.72) 59%,rgba(7,50,78,.09) 100%),var(--hero-image) right center/58% 100% no-repeat;box-shadow:0 12px 34px rgba(21,61,89,.08)}.command-hero::after{content:"";position:absolute;inset:0;pointer-events:none;background:linear-gradient(115deg,transparent 0 47%,rgba(255,255,255,.35) 47.1% 47.3%,transparent 47.4%)}.command-hero__copy,.system-summary,.command-hero__slogan{position:relative;z-index:1}.command-eyebrow{color:#356b95;font-size:9px;font-weight:800;letter-spacing:2px}.command-hero h1{margin:6px 0 1px;color:#103b5d;font-size:38px;line-height:1.18;letter-spacing:2px}.command-subtitle{margin:0;color:#53748e;font-size:15px;letter-spacing:2px}.command-intro{margin:12px 0 0;color:#607c91;font-size:10px;line-height:1.75}.command-date{display:flex;align-items:center;gap:8px;margin-top:17px;color:#4d6c83;font-size:10px}.command-date span{color:#1974a4}.command-date b{font-weight:600;font-variant-numeric:tabular-nums}.system-summary{padding:15px 16px;border:1px solid rgba(18,92,130,.08);border-radius:7px;background:rgba(255,255,255,.9);box-shadow:0 8px 28px rgba(29,75,102,.09);backdrop-filter:blur(8px)}.system-summary__head{display:flex;align-items:center;justify-content:space-between;padding-bottom:10px;border-bottom:1px solid #dce7ed}.system-summary__head small{display:block;color:#31536b;font-size:10px;font-weight:700}.system-summary__head strong{display:flex;align-items:center;gap:8px;margin-top:7px;color:#153b58;font-size:14px}.system-summary__head i{width:9px;height:9px;border-radius:50%;background:#18b879;box-shadow:0 0 0 4px rgba(24,184,121,.1)}.system-summary__head a{width:24px;height:24px;display:grid;place-items:center;color:#3277a1;border-radius:50%;background:#eef6fa;text-decoration:none;font-size:21px}.system-summary dl{margin:5px 0 0}.system-summary dl div{display:flex;justify-content:space-between;gap:16px;padding:5px 0;color:#698091;font-size:9px}.system-summary dd{margin:0;color:#284a62;font-weight:600}.command-hero__slogan{align-self:end;justify-self:end;display:flex;flex-direction:column;align-items:flex-end;margin:0 2px 3px 0;color:#fff;text-shadow:0 2px 8px rgba(3,27,45,.65)}.command-hero__slogan strong{font-size:17px;letter-spacing:3px}.command-hero__slogan span{margin-top:3px;font-size:10px;letter-spacing:1px}.home-error{margin:12px 0 0}.command-kpis{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px;margin:12px 0}.command-kpis article{min-width:0;min-height:112px;display:flex;gap:11px;padding:15px 13px;border:1px solid #e0e9ee;border-radius:7px;background:#fff;box-shadow:0 7px 22px rgba(22,62,89,.055)}.command-kpis article>div{min-width:0;flex:1}.metric-icon{width:32px;height:32px;display:grid;place-items:center;flex:0 0 auto;border-radius:50%;font-size:16px;font-weight:800}.metric-icon.cyan{color:#128fc9;background:#e7f6fc}.metric-icon.teal{color:#139f97;background:#e8f8f5}.metric-icon.violet{color:#7561eb;background:#f0edff}.metric-icon.green{color:#14a988;background:#e8f8f3}.metric-icon.red{color:#ef5555;background:#fff0ef}.metric-icon.blue{color:#2589e7;background:#ecf5ff}.command-kpis article>div>span{display:block;color:#254760;font-size:10px;font-weight:700;white-space:nowrap}.command-kpis article>div>span small{display:block;margin-top:3px;color:#7890a1;font-size:7px;font-weight:500;letter-spacing:.6px}.command-kpis p{margin:12px 0 7px;color:#173f64;font-size:9px;white-space:nowrap}.command-kpis p strong{font-size:21px;font-variant-numeric:tabular-nums}.command-kpis article:nth-child(3) p strong{font-size:16px}.command-kpis em{display:block;color:#08a567;font-style:normal;font-size:9px;white-space:nowrap}.command-kpis em.down{color:#ee4d54}.command-kpis em.flat{color:#8a9aa5}.command-kpis em small{margin-left:3px;color:#8999a4}.dashboard-grid{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:12px}.dashboard-card{min-width:0;overflow:hidden;border:1px solid #dfe8ed;border-radius:7px;background:#fff;box-shadow:0 7px 22px rgba(22,62,89,.055)}.dashboard-card>header{height:47px;display:flex;align-items:center;justify-content:space-between;gap:14px;padding:0 15px;border-bottom:1px solid #e6edf1}.dashboard-card>header>div{display:flex;align-items:center;gap:8px}.dashboard-card h2{margin:0;color:#193c57;font-size:13px}.dashboard-card h2 small{margin-left:8px;color:#7790a2;font-size:7px;font-weight:500;letter-spacing:.8px}.dashboard-card header>a{color:#2581b8;text-decoration:none;font-size:9px;white-space:nowrap}.section-icon{color:#12a8a5;font-size:15px;font-weight:800}.section-icon.alert{color:#f35c5c}.trend-card,.status-card,.alarm-card{grid-column:span 4}.reports-card{grid-column:span 5}.quick-card{grid-column:span 4}.insights-card{grid-column:span 3}.trend-chart{height:178px;margin:2px 8px 5px 3px}.card-loading,.card-empty{height:178px;display:flex;align-items:center;justify-content:center;gap:8px;color:#718695;font-size:10px}.card-empty{flex-direction:column}.card-empty>span{width:30px;height:30px;display:grid;place-items:center;color:#fff;border-radius:50%;background:#21af79;font-size:16px}.card-empty small{color:#91a0aa}.card-empty.compact{height:139px}.status-content{height:145px;display:grid;grid-template-columns:48% 52%;align-items:center;padding:2px 11px}.status-chart{height:130px}.status-content ul{display:grid;gap:12px;margin:0;padding:0;list-style:none}.status-content li{display:grid;grid-template-columns:8px 1fr 25px 35px;align-items:center;gap:6px;color:#587184;font-size:9px}.status-content li i{width:7px;height:7px;border-radius:50%;background:#18a8a5}.status-content li i.idle{background:#78baf0}.status-content li i.offline{background:#c9d5dc}.status-content li b{color:#173e5d;text-align:right;font-size:11px}.status-content li small{text-align:right}.status-note{height:31px;display:flex;align-items:center;margin:0;padding:0 15px;color:#607b8d;background:#f3f8fa;font-size:9px}.status-note span{width:16px;height:16px;display:grid;place-items:center;margin-right:8px;color:#fff;border-radius:50%;background:#16a9a2}.alarm-list{height:178px}.alarm-row{min-height:44px;display:grid;grid-template-columns:25px minmax(0,1fr) 85px 44px;align-items:center;gap:8px;padding:6px 12px;color:inherit;border-bottom:1px solid #edf1f3;text-decoration:none}.alarm-row:last-child{border-bottom:0}.alarm-row:hover{background:#f8fbfc}.alarm-symbol{width:23px;height:23px;display:grid;place-items:center;color:#e09922;border-radius:50%;background:#fff4dc;font-size:11px;font-weight:800}.alarm-symbol.critical{color:#ed5257;background:#ffebec}.alarm-symbol.high{color:#ec8e21;background:#fff1dd}.alarm-row div{min-width:0}.alarm-row strong,.alarm-row small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.alarm-row strong{color:#28465c;font-size:9px}.alarm-row small{margin-top:3px;color:#8295a3;font-size:8px}.alarm-row time{color:#71899a;font-size:8px;font-variant-numeric:tabular-nums}.alarm-row em{padding:4px 5px;color:#d74b4f;border-radius:4px;background:#ffeded;text-align:center;font-style:normal;font-size:8px}.alarm-row em.closed{color:#198d66;background:#e7f6f0}.report-list{height:139px}.report-row{height:46px;display:grid;grid-template-columns:24px minmax(90px,1fr) 72px 44px 50px 90px 28px;align-items:center;gap:6px;padding:0 12px;color:#62798a;border-bottom:1px solid #edf1f3;font-size:8px}.report-row:last-child{border-bottom:0}.report-symbol{width:22px;height:22px;display:grid;place-items:center;color:#1599ae;border-radius:50%;background:#e8f7f9}.report-row strong{overflow:hidden;color:#27475e;text-overflow:ellipsis;white-space:nowrap;font-size:9px}.report-row em{justify-self:start;padding:3px 5px;border-radius:9px;background:#edf5f7;font-style:normal}.report-row time{font-variant-numeric:tabular-nums}.report-row a{color:#1680b8;text-decoration:none}.quick-links{height:139px;display:grid;grid-template-columns:repeat(3,1fr);gap:7px;padding:8px}.quick-links a{position:relative;display:flex;align-items:center;flex-direction:column;justify-content:center;color:#294860;border-radius:5px;background:linear-gradient(150deg,#eff9fc,#f7fbfd);text-decoration:none}.quick-links a:nth-child(2){background:linear-gradient(150deg,#f1f4ff,#f9faff)}.quick-links a:nth-child(3){background:linear-gradient(150deg,#eef7fc,#f8fbfd)}.quick-links a>span{width:30px;height:30px;display:grid;place-items:center;margin-bottom:6px;color:#118ead;border-radius:7px;background:#dff4fa;font-size:15px}.quick-links a:nth-child(2)>span{color:#356fec;background:#e7ecff}.quick-links a:nth-child(3)>span{color:#167eaf;background:#e0f2fa}.quick-links strong{font-size:10px}.quick-links small{margin-top:4px;color:#8194a1;font-size:7px}.quick-links b{position:absolute;right:50%;bottom:5px;transform:translateX(50%);color:#2784b7;font-size:14px}.insight-grid{height:139px;display:grid;grid-template-columns:1fr 1fr;gap:6px;padding:8px}.insight-grid>div{min-width:0;display:flex;align-items:center;gap:9px;padding:8px;border-radius:5px;background:#eff8fb}.insight-grid>div.danger{background:#fff0f0}.insight-grid>div.suggest{background:#eef8f4}.insight-grid>div.info{background:#eff6fc}.insight-grid>div>span{color:#0aa3a0;font-size:17px;font-weight:800}.insight-grid .danger>span{color:#ee5659}.insight-grid .info>span{color:#3d8fe5}.insight-grid .suggest>span{color:#15a47a}.insight-grid p{min-width:0;margin:0}.insight-grid strong,.insight-grid small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.insight-grid strong{color:#29475c;font-size:9px}.insight-grid small{margin-top:4px;color:#718697;font-size:7px}
.command-home{--home-navy:#153f5d;--home-blue:#237fb7;--home-teal:#16a39d;--home-green:#2aa473;--home-amber:#d99132;--home-red:#df5961;--home-indigo:#6674cf;--home-sky:#438fc6;--home-line:#dbe6ec;color:var(--home-navy)}
.command-hero h1,.command-kpis p,.dashboard-card h2{color:var(--home-navy)}.command-eyebrow,.command-date,.dashboard-card header>a{color:var(--home-blue)}
.command-kpis article{--kpi-color:var(--home-blue);--kpi-soft:#eaf5fb;position:relative;border-color:var(--home-line);border-top:2px solid var(--kpi-color);transition:transform .18s ease,box-shadow .18s ease}.command-kpis article:hover{transform:translateY(-2px);box-shadow:0 11px 26px rgba(22,62,89,.09)}
.command-kpis .kpi-teal{--kpi-color:var(--home-teal);--kpi-soft:#e8f7f5}.command-kpis .kpi-indigo{--kpi-color:var(--home-indigo);--kpi-soft:#eef0fb}.command-kpis .kpi-green{--kpi-color:var(--home-green);--kpi-soft:#eaf7f1}.command-kpis .kpi-amber{--kpi-color:var(--home-amber);--kpi-soft:#fff5e8}.command-kpis .kpi-sky{--kpi-color:var(--home-sky);--kpi-soft:#eaf3fa}
.metric-icon{color:var(--kpi-color);background:var(--kpi-soft)}.command-kpis em{display:inline-flex;align-items:center;gap:3px;color:var(--home-green)}.command-kpis em.down{color:var(--home-red)}.command-kpis em.flat{color:#8293a0}
.dashboard-card{border-color:var(--home-line);box-shadow:0 7px 22px rgba(22,62,89,.05)}.dashboard-card>header{background:linear-gradient(90deg,#fff,#fbfdfe)}.dashboard-card header>a{display:inline-flex;align-items:center;gap:2px;font-weight:600}.section-icon{width:25px;height:25px;display:grid;place-items:center;color:var(--home-teal);border-radius:7px;background:#e8f7f6}.section-icon.alert{color:var(--home-red);background:#fff0f1}
.status-note span,.card-empty>span{display:grid;place-items:center;background:var(--home-green)}.alarm-symbol{color:var(--home-amber);background:#fff5e6}.alarm-symbol.critical{color:var(--home-red);background:#fff0f1}.alarm-symbol.high{color:#d9842b;background:#fff4e4}.report-symbol{color:var(--home-teal);background:#e8f7f6}
.quick-links a{border:1px solid transparent;transition:border-color .18s ease,transform .18s ease,box-shadow .18s ease}.quick-links a:hover{transform:translateY(-2px);border-color:#cfe1e9;box-shadow:0 7px 16px rgba(30,76,102,.08)}.quick-links a>span{color:var(--home-teal);background:#def3f2}.quick-links a:nth-child(2)>span{color:var(--home-indigo);background:#e9ecfb}.quick-links a:nth-child(3)>span{color:var(--home-blue);background:#e3f1f8}.quick-links b{display:grid;place-items:center}
.insight-grid>div>span{width:23px;height:23px;display:grid;place-items:center;flex:0 0 auto;color:var(--home-teal)}.insight-grid .danger>span{color:var(--home-red)}.insight-grid .info>span{color:var(--home-blue)}.insight-grid .suggest>span{color:var(--home-green)}
.command-eyebrow{font-size:10px}.command-hero h1{font-size:40px}.command-subtitle{font-size:16px}.command-intro{font-size:11px;line-height:1.8}.command-date{font-size:11px}.system-summary__head small{font-size:11px}.system-summary__head strong{font-size:15px}.system-summary dl div{font-size:10px}.command-hero__slogan strong{font-size:18px}.command-hero__slogan span{font-size:11px}
.command-kpis article{min-height:118px;padding:16px 14px}.command-kpis article>div>span{font-size:11px}.command-kpis article>div>span small{font-size:8px}.command-kpis p{font-size:10px}.command-kpis p strong{font-size:23px}.command-kpis article:nth-child(3) p strong{font-size:17px}.command-kpis em{font-size:10px}
.dashboard-card>header{height:50px}.dashboard-card h2{font-size:14px}.dashboard-card h2 small{font-size:8px}.dashboard-card header>a{font-size:10px}.card-loading,.card-empty{font-size:11px}.status-content li{font-size:10px}.status-content li b{font-size:12px}.status-note{font-size:10px}.alarm-row strong{font-size:10px}.alarm-row small,.alarm-row time,.alarm-row em{font-size:9px}.report-row{font-size:9px}.report-row strong{font-size:10px}.quick-links strong{font-size:11px}.quick-links small{font-size:8px}.insight-grid strong{font-size:10px}.insight-grid small{font-size:8px}
.command-hero{border:1px solid rgba(255,255,255,.7);border-radius:14px;animation:surface-in .46s cubic-bezier(.2,.75,.25,1) both}.command-hero::before{content:"";position:absolute;z-index:0;width:290px;height:290px;top:-190px;left:34%;pointer-events:none;border-radius:50%;background:radial-gradient(circle,rgba(66,190,213,.2),transparent 68%);animation:ambient-drift 7s ease-in-out infinite alternate}
.system-summary{border-radius:11px;transition:transform .2s ease,box-shadow .2s ease,border-color .2s ease}.system-summary:hover,.system-summary:focus-within{transform:translateY(-2px);border-color:rgba(40,145,181,.22);box-shadow:0 13px 32px rgba(24,70,98,.13)}.system-summary__head a{transition:transform .18s ease,background .18s ease}.system-summary__head a:hover{transform:translateX(2px);background:#e2f2f8}
.command-kpis article{overflow:hidden;border-radius:11px;animation:surface-in .42s cubic-bezier(.2,.75,.25,1) both}.command-kpis article::after{content:"";position:absolute;width:72px;height:72px;right:-28px;bottom:-38px;border-radius:50%;background:var(--kpi-soft);opacity:.75;transition:transform .24s ease}.command-kpis article:hover::after{transform:scale(1.24)}.command-kpis article:nth-child(2){animation-delay:.04s}.command-kpis article:nth-child(3){animation-delay:.08s}.command-kpis article:nth-child(4){animation-delay:.12s}.command-kpis article:nth-child(5){animation-delay:.16s}.command-kpis article:nth-child(6){animation-delay:.2s}.metric-icon{position:relative;z-index:1;transition:transform .2s ease}.command-kpis article:hover .metric-icon{transform:scale(1.08) rotate(-3deg)}
.dashboard-card{border-radius:12px;transition:transform .2s ease,box-shadow .2s ease,border-color .2s ease;animation:surface-in .48s cubic-bezier(.2,.75,.25,1) both}.dashboard-card:nth-child(2){animation-delay:.04s}.dashboard-card:nth-child(3){animation-delay:.08s}.dashboard-card:nth-child(4){animation-delay:.12s}.dashboard-card:nth-child(5){animation-delay:.16s}.dashboard-card:nth-child(6){animation-delay:.2s}.dashboard-card:hover,.dashboard-card:focus-within{transform:translateY(-2px);border-color:#cbdde6;box-shadow:0 13px 30px rgba(22,62,89,.09)}.dashboard-card header>a{min-height:30px;padding:0 4px;border-radius:6px;transition:color .18s ease,background .18s ease}.dashboard-card header>a:hover{color:#0e648f;background:#edf7fb}.dashboard-card header>a .app-icon{transition:transform .18s ease}.dashboard-card header>a:hover .app-icon{transform:translateX(2px)}
.dashboard-card a:focus-visible,.quick-links a:focus-visible,.report-row a:focus-visible,.system-summary a:focus-visible{outline:3px solid rgba(35,127,183,.28);outline-offset:2px}.alarm-row,.report-row{transition:background .16s ease,transform .16s ease}.alarm-row:hover{transform:translateX(2px);background:#f3f9fb}.report-row:hover{background:#f7fafc}.quick-links a:active{transform:translateY(0) scale(.98)}.quick-links a:hover b{transform:translateX(calc(50% + 2px))}.quick-links b{transition:transform .18s ease}
.insight-grid>div{border:1px solid transparent;transition:transform .18s ease,border-color .18s ease,box-shadow .18s ease}.insight-grid>div:hover{transform:translateY(-1px);border-color:#d8e7ed;box-shadow:0 6px 14px rgba(30,76,102,.06)}.system-summary__head i{animation:status-pulse 2.4s ease-out infinite}.card-loading{position:relative;overflow:hidden;background:linear-gradient(100deg,#fbfdfe 25%,#f0f6f9 37%,#fbfdfe 52%);background-size:220% 100%;animation:loading-shimmer 1.7s linear infinite}
@keyframes surface-in{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}@keyframes ambient-drift{to{transform:translate3d(70px,28px,0) scale(1.1)}}@keyframes status-pulse{0%,55%{box-shadow:0 0 0 4px rgba(24,184,121,.1)}85%,100%{box-shadow:0 0 0 9px rgba(24,184,121,0)}}@keyframes loading-shimmer{to{background-position:-220% 0}}
@media(prefers-reduced-motion:reduce){.command-home *,.command-home *::before,.command-home *::after{animation-duration:.01ms!important;animation-iteration-count:1!important;transition-duration:.01ms!important}}
@media(max-width:1120px){.command-hero{grid-template-columns:1fr 280px;padding-right:28px}.command-hero__slogan{display:none}.command-kpis{grid-template-columns:repeat(3,1fr)}.trend-card,.status-card{grid-column:span 6}.alarm-card{grid-column:span 12}.reports-card{grid-column:span 7}.quick-card{grid-column:span 5}.insights-card{grid-column:span 12}.insight-grid{grid-template-columns:repeat(4,1fr);height:auto}}
@media(max-width:760px){.command-home{padding-top:10px}.command-hero{grid-template-columns:1fr;gap:18px;padding:26px 22px;background:linear-gradient(90deg,rgba(245,251,255,.97),rgba(236,247,252,.88)),var(--hero-image) center/cover no-repeat}.command-hero h1{font-size:31px}.system-summary{width:100%}.command-kpis{grid-template-columns:repeat(2,1fr)}.trend-card,.status-card,.alarm-card,.reports-card,.quick-card,.insights-card{grid-column:span 12}.insight-grid{grid-template-columns:1fr 1fr}.dashboard-card h2 small{display:none}}
@media(max-width:480px){.command-kpis{grid-template-columns:1fr}.command-kpis article{min-height:100px}.status-content{grid-template-columns:45% 55%}.alarm-row{grid-template-columns:25px minmax(0,1fr) 44px}.alarm-row time{display:none}.report-row{grid-template-columns:24px minmax(90px,1fr) 55px 28px}.report-row>span:nth-of-type(3),.report-row em,.report-row time{display:none}.quick-links{height:auto;grid-template-columns:1fr}.quick-links a{min-height:105px}.insight-grid{grid-template-columns:1fr;height:auto}}
</style>
