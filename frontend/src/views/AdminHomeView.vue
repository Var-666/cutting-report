<script setup lang="ts">
import type { LeadershipReport, ReportJob } from '../types/reports'
import type { EnergyMonthReport } from '../types/energy'
import type { AlarmIncident, AlarmStatus } from '../types/alarms'

import type { EChartsType as ECharts } from 'echarts/core'
import type { EChartsOption } from 'echarts'

import { BarChart, LineChart, PieChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { init, use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { NAlert, NButton, NCard, NEmpty, NNumberAnimation, NSkeleton, NSpin, NStatistic, NTag, NText, useThemeVars } from 'naive-ui'
import { getAlarmStatus, listAlarmIncidents } from '../api/alarms.ts'
import { getEnergyMonth } from '../api/energy.ts'
import { getLeadershipReport, listReports } from '../api/cutting.ts'
import { reportToday } from '../api/reportDates.ts'
import AppIcon from '../components/AppIcon.vue'
import homeWelcomeIllustration from '../assets/home-welcome-illustration.png'
import HomeKpiCard from '../components/HomeKpiCard.vue'
import HomeSectionHeader from '../components/HomeSectionHeader.vue'
import { session } from '../state/session'
import { formatChineseDate, formatChineseDateTime, formatChineseMonth } from '../utils/dateDisplay.ts'
import { formatDeviceDisplay } from '../utils/deviceDisplay.ts'
import { useAppStore } from '../state/app.ts'

use([BarChart, LineChart, PieChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

const app = useAppStore()
const theme = useThemeVars()
const reportDate = reportToday()
const reportMonth = reportDate.slice(0, 7)
const overviewLoading = ref(true)
const historyLoading = ref(true)
const energyLoading = ref(true)
const alarmLoading = ref(true)
const overview = ref<LeadershipReport | null>(null)
const energy = ref<EnergyMonthReport | null>(null)
const alarmStatus = ref<AlarmStatus | null>(null)
const incidents = ref<AlarmIncident[]>([])
const recentReports = ref<ReportJob[]>([])
const historyTotal = ref(0)
const error = ref('')
const currentTime = ref(new Date())
const trendElement = ref<HTMLElement | null>(null)
const statusElement = ref<HTMLElement | null>(null)
let trendChart: ECharts | null = null
let statusChart: ECharts | null = null
let clockTimer: number | null = null

const total = computed(() => overview.value?.current_month_total || { task_count: 0, cut_length_m: 0, work_time_seconds: 0 })
const previousTotal = computed<Partial<LeadershipReport['current_month_total']>>(() => overview.value?.comparison_total || {})
const dailyRecords = computed(() => [...(overview.value?.daily_records || [])].sort((left, right) => left.period_start.localeCompare(right.period_start)))
const activeDays = computed(() => dailyRecords.value.filter((item) => Number(item.task_count) > 0).length)
const elapsedDays = computed(() => Math.max(activeDays.value, Number(energy.value?.elapsed_day_count || reportDate.slice(8))))
const energySummary = computed<Partial<EnergyMonthReport['summary']>>(() => energy.value?.summary || {})
const energyConnected = computed(() => energy.value?.energy_connected !== false)
const electricity = computed(() => energySummary.value.electricity?.value)
const gas = computed(() => energySummary.value.gas?.value)
const openAlarms = computed(() => incidents.value.filter((item) => item.status === 'open'))
const displayDate = computed(() => new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: 'long', day: 'numeric', weekday: 'short', timeZone: 'Asia/Shanghai' }).format(currentTime.value))
const displayTime = computed(() => new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false, timeZone: 'Asia/Shanghai' }).format(currentTime.value))
const metricSeries = computed(() => ({
  tasks: dailyRecords.value.map((item) => item.task_count),
  length: dailyRecords.value.map((item) => item.cut_length_m),
  hours: dailyRecords.value.map((item) => item.work_time_seconds == null ? null : Number(item.work_time_seconds) / 3600),
}))

const quickLinks = [
  { to: '/admin/reports/leadership', title: '领导报表', icon: 'briefcase' },
  { to: '/admin/reports/workshop', title: '车间报表', icon: 'factory' },
  { to: '/admin/reports/equipment', title: '设备报表', icon: 'chart' },
]
const reportTypeText = { leadership: '领导报表', workshop: '车间报表', equipment: '设备报表', custom: '自定义报表' }
const statusText = { pending: '等待中', querying: '查询中', calculating: '统计中', analyzing: '分析中', rendering: '渲染中', completed: '已完成', failed: '失败' }
const severityText: Record<string, string> = { critical: '严重', high: '高', medium: '一般', low: '提示' }

const workSeconds = computed(() => Math.max(0, Math.round(Number(total.value.work_time_seconds || 0))))
const workHours = computed(() => Math.floor(workSeconds.value / 3600))
const workUnit = computed(() => `小时 ${String(Math.floor((workSeconds.value % 3600) / 60)).padStart(2, '0')}分`)

function comparisonStatus(current: unknown, previous: unknown) {
  if ([current, previous].some((value) => value == null || value === '' || !Number.isFinite(Number(value))) || Number(previous) <= 0) return { text: '暂无上月对比', direction: 'flat' }
  const change = (Number(current) - Number(previous)) / Number(previous) * 100
  if (change === 0) return { text: '与上月持平', direction: 'flat' }
  const direction = change > 0 ? 'up' : 'down'
  const percent = Math.abs(change) < 0.1 ? '<0.1' : Number(Math.abs(change).toFixed(1))
  return { text: `${direction === 'up' ? '↑' : '↓'} ${percent}% 较上月`, direction }
}

const taskComparison = computed(() => comparisonStatus(total.value.task_count, previousTotal.value.task_count))
const lengthComparison = computed(() => comparisonStatus(total.value.cut_length_m, previousTotal.value.cut_length_m))
const timeComparison = computed(() => comparisonStatus(total.value.work_time_seconds, previousTotal.value.work_time_seconds))
const insightTrend = computed(() => {
  if (lengthComparison.value.direction === 'flat') return lengthComparison.value.text === '暂无上月对比' ? '暂无可比较的切割长度数据' : '切割长度与上月持平'
  return `切割长度${lengthComparison.value.direction === 'up' ? '高于' : '低于'}上月 ${lengthComparison.value.text.match(/<?\d+(?:\.\d+)?%/)?.[0] || ''}`
})
const insightAdvice = computed(() => openAlarms.value.length ? '优先处理未解除设备告警' : !energyConnected.value ? '当前设备尚未接入能耗仪表' : electricity.value == null || gas.value == null ? '先核对能耗数据是否完整' : '持续关注生产与能耗变化')

function reportPeriod(item: ReportJob | null) {
  if (!item?.report_date) return '--'
  return item.report_type === 'leadership' ? formatChineseMonth(item.report_date) : formatChineseDate(item.report_date)
}

function shortDateTime(value: unknown) {
  if (!value) return '--'
  return formatChineseDateTime(value).replace(/年|月/g, '-').replace('日', '')
}

function resizeCharts() { trendChart?.resize(); statusChart?.resize() }
const chartResizeObserver = new ResizeObserver(resizeCharts)
watch([trendElement, statusElement], (elements) => {
  chartResizeObserver.disconnect()
  elements.filter((element): element is HTMLElement => Boolean(element)).forEach((element) => chartResizeObserver.observe(element))
}, { flush: 'post' })

function renderCharts() {
  if (trendElement.value && overview.value) {
    trendChart ||= init(trendElement.value)
    const rows = dailyRecords.value.slice(-7)
    trendChart.setOption<EChartsOption>({
      animationDuration: app.reducedMotion ? 0 : 300,
      color: [theme.value.primaryColor, theme.value.successColor],
      tooltip: { trigger: 'axis', borderWidth: 0, backgroundColor: theme.value.popoverColor, textStyle: { color: theme.value.textColor1, fontSize: 12 } },
      legend: { top: 4, right: 8, itemWidth: 11, itemHeight: 7, textStyle: { color: theme.value.textColor3, fontSize: 12 } },
      grid: { left: 48, right: 44, top: 38, bottom: 31 },
      xAxis: { type: 'category', data: rows.map((item) => item.period_start.slice(5)), axisTick: { show: false }, axisLine: { lineStyle: { color: theme.value.borderColor } }, axisLabel: { color: theme.value.textColor3, fontSize: 12 } },
      yAxis: [{ type: 'value', splitNumber: 3, splitLine: { lineStyle: { color: theme.value.dividerColor } }, axisLabel: { color: theme.value.textColor3, fontSize: 12 } }, { type: 'value', splitNumber: 3, splitLine: { show: false }, axisLabel: { show: false } }],
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
    statusChart.setOption<EChartsOption>({
      animationDuration: app.reducedMotion ? 0 : 300,
      color: [theme.value.successColor, theme.value.dividerColor],
      tooltip: { backgroundColor: theme.value.popoverColor, borderColor: theme.value.borderColor, textStyle: { color: theme.value.textColor1 }, trigger: 'item', formatter: '{b}：{c} 天' },
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

watch([theme, () => app.reducedMotion, trendElement, statusElement], () => renderCharts(), { flush: 'post' })

onMounted(() => {
  loadDashboard()
  clockTimer = window.setInterval(() => { currentTime.value = new Date() }, 1000)
})
onBeforeUnmount(() => {
  window.clearInterval(clockTimer ?? undefined)
  chartResizeObserver.disconnect()
  trendChart?.dispose()
  statusChart?.dispose()
})
</script>

<template>
  <div class="min-h-500px flex-col-stretch gap-16px overflow-hidden bg-layout lt-sm:overflow-auto">
    <NCard :bordered="false" size="small" class="card-wrapper bg-container home-welcome">
      <div class="flex-y-center justify-between gap-16px">
        <div class="min-w-0 flex-col-stretch gap-12px">
          <h1 class="m-0 text-22px font-semibold">生产指挥中心</h1>
          <NText depth="3">{{ session.user?.display_name }}，欢迎回来</NText>
          <div class="flex-y-center flex-wrap gap-8px text-12px text-gray-500 dark:text-gray-400"><AppIcon name="calendar" :size="16" tone="current" />{{ displayDate }}<time>{{ displayTime }}</time></div>
        </div>
        <img :src="homeWelcomeIllustration" alt="" width="800" height="600" class="h-auto w-120px shrink-0 object-contain sm:w-200px">
      </div>
    </NCard>

    <NAlert v-if="error" title="部分概览数据读取失败" type="error" role="alert">
      {{ error }}
    </NAlert>

    <NCard :bordered="false" class="card-wrapper bg-container" content-style="padding: 0">
      <section class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4" aria-label="本月生产概况">
        <HomeKpiCard class="border-0 border-b border-solid border-[var(--theme-divider-color)] sm:border-r xl:border-b-0" title="本月任务" :value="overview ? total.task_count : null" unit="项" :status="taskComparison.text" :direction="taskComparison.direction" :series="metricSeries.tasks" :loading="overviewLoading" />
        <HomeKpiCard class="border-0 border-b border-solid border-[var(--theme-divider-color)] xl:border-r xl:border-b-0" title="切割长度" color="rgb(var(--success-color))" :value="overview ? total.cut_length_m : null" :precision="1" unit="米" :status="lengthComparison.text" :direction="lengthComparison.direction" :series="metricSeries.length" :loading="overviewLoading" />
        <HomeKpiCard class="border-0 border-b border-solid border-[var(--theme-divider-color)] sm:border-b-0 sm:border-r" title="设备工时" color="rgb(var(--info-color))" :value="overview ? workHours : null" :unit="overview ? workUnit : ''" :status="timeComparison.text" :direction="timeComparison.direction" :series="metricSeries.hours" :loading="overviewLoading" />
        <div class="min-w-0 flex-col-stretch justify-center gap-8px p-16px" aria-label="生产天数与能耗">
          <div class="flex-y-center justify-between gap-8px border border-solid border-[var(--theme-divider-color)] rd-12px px-12px py-8px">
            <div class="flex-col-stretch gap-4px"><NText depth="3" class="text-12px">生产天数</NText><NSkeleton v-if="overviewLoading" height="24px" width="48px" /><NText v-else class="text-18px font-medium">{{ overview ? activeDays : '--' }} <NText depth="3" class="text-12px">天</NText></NText></div>
            <NTag v-if="!overviewLoading && overview" round size="small" :bordered="false" :type="activeDays ? 'success' : 'default'">{{ activeDays ? '本月有记录' : '本月暂无记录' }}</NTag>
          </div>
          <div class="grid grid-cols-2 gap-8px">
            <div v-for="item in [{ label: '电耗', icon: 'zap', value: electricity, unit: energy?.units?.electricity || 'kWh' }, { label: '气耗', icon: 'wind', value: gas, unit: energy?.units?.gas || 'Nm³' }]" :key="item.label" class="min-w-0 border border-dashed border-[var(--theme-divider-color)] rd-12px px-12px py-8px" :aria-label="item.label">
              <div class="mb-4px flex-y-center gap-4px"><NText depth="3" class="text-12px">{{ item.label }}</NText><AppIcon :name="item.icon" :size="14" tone="muted" /></div>
              <NSkeleton v-if="energyLoading" height="22px" width="80%" aria-label="正在获取能耗数据" role="status" />
              <NStatistic v-else tabular-nums :value="!energyConnected ? '暂未接入' : item.value == null ? '暂无数据' : item.value.toLocaleString('zh-CN', { minimumFractionDigits: 1, maximumFractionDigits: 1 })" :theme-overrides="{ valueFontSize: '14px' }" class="break-words"><template #suffix><NText depth="3" class="text-12px">{{ item.unit }}</NText></template></NStatistic>
            </div>
          </div>
        </div>
      </section>
    </NCard>

    <section class="grid grid-cols-1 gap-16px xl:grid-cols-3" aria-label="生产看板">
      <NCard :bordered="false" size="small" class="card-wrapper bg-container xl:col-span-2" aria-labelledby="trend-card-title">
        <template #header><HomeSectionHeader heading-id="trend-card-title" title="生产趋势" icon="trend" to="/admin/reports/leadership" action="近7天" /></template>
        <Transition name="fade-slide" mode="out-in">
          <div v-if="overviewLoading" key="loading" class="h-240px flex-center" role="status"><NSpin size="small" /><NText depth="3" class="ml-12px">正在获取生产数据…</NText></div>
          <NEmpty v-else-if="!overview || !dailyRecords.length" key="empty" class="h-240px flex-center" description="暂无趋势数据" />
          <div v-else key="chart" ref="trendElement" class="h-240px w-full" role="img" aria-label="近七天切割长度与设备工时趋势图"></div>
        </Transition>
      </NCard>
      <NCard :bordered="false" size="small" class="card-wrapper bg-container" aria-labelledby="status-card-title">
        <template #header><HomeSectionHeader heading-id="status-card-title" title="生产状态" icon="activity" to="/admin/reports/workshop" action="查看全部" /></template>
        <Transition name="fade-slide" mode="out-in">
          <div v-if="overviewLoading" key="loading" class="h-240px flex-center" role="status"><NSpin size="small" /></div>
          <NEmpty v-else-if="!overview" key="empty" class="h-240px flex-center" description="暂无生产状态" />
          <div v-else key="status" class="flex-col-stretch gap-12px">
            <div class="relative mx-auto h-150px w-180px">
              <div ref="statusElement" class="h-full w-full" role="img" aria-label="本月生产日状态图"></div>
              <div class="pointer-events-none absolute inset-0 flex-center flex-col" aria-hidden="true"><NNumberAnimation v-if="!app.reducedMotion" :to="activeDays" class="text-26px font-semibold" show-separator /><span v-else class="text-26px font-semibold">{{ activeDays }}</span><NText depth="3" class="text-12px">生产天数</NText></div>
            </div>
            <div class="flex-y-center justify-between gap-8px text-12px"><span class="text-success">生产日</span><NText>{{ activeDays }} 天</NText><NText depth="3">{{ elapsedDays ? Math.round(activeDays / elapsedDays * 100) : 0 }}%</NText></div>
            <div class="flex-y-center justify-between gap-8px text-12px"><NText depth="3">无生产记录</NText><NText>{{ Math.max(0, elapsedDays - activeDays) }} 天</NText><NText depth="3">统计周期 {{ elapsedDays }} 天</NText></div>
          </div>
        </Transition>
      </NCard>
      <NCard :bordered="false" size="small" class="card-wrapper bg-container xl:max-h-300px" content-class="min-h-0 overflow-hidden" aria-labelledby="alarm-card-title">
        <template #header><HomeSectionHeader heading-id="alarm-card-title" title="最近告警" icon="bell" to="/admin/alarms" action="查看全部" /></template>
        <Transition name="fade" mode="out-in">
          <div v-if="alarmLoading" key="loading" class="h-full min-h-180px flex-center" role="status"><NSpin size="small" /></div>
          <NEmpty v-else-if="!incidents.length" key="empty" class="h-full min-h-120px flex-center" :description="alarmStatus?.enabled ? '当前暂无告警' : '告警监控未启用'" />
          <div v-else key="alarms" class="flex-col-stretch gap-8px">
            <RouterLink v-for="item in incidents.slice(0, 3)" :key="item.incident_id" to="/admin/alarms" class="h-72px min-w-0 flex-col-stretch gap-4px rd-8px p-4px no-underline transition-all-300 hover:bg-primary/10">
              <div class="flex-y-center justify-between gap-8px"><NText class="min-w-0 flex-1 truncate">{{ item.alarm_desc || item.alarm_code || '设备告警' }}</NText><NTag size="small" :bordered="false" :type="item.status === 'open' ? 'error' : 'success'">{{ item.status === 'open' ? '未解除' : '已解除' }}</NTag></div>
              <NText depth="3" class="truncate text-12px leading-16px">{{ severityText[item.severity] || '告警' }} · {{ formatDeviceDisplay(item.machine_no) || '切割设备' }} · {{ item.alarm_class || '运行监控' }}</NText>
              <NText depth="3" class="truncate text-12px leading-16px">{{ shortDateTime(item.last_seen_at) }}</NText>
            </RouterLink>
          </div>
        </Transition>
      </NCard>
      <NCard :bordered="false" size="small" class="card-wrapper bg-container xl:max-h-300px xl:col-span-2" content-class="min-h-0 overflow-hidden" aria-labelledby="reports-card-title">
        <template #header><HomeSectionHeader heading-id="reports-card-title" title="最近生成报告" icon="file" to="/admin/reports/center" :action="`查看全部${historyTotal ? ` ${historyTotal}` : ''}`" /></template>
        <Transition name="fade" mode="out-in">
          <div v-if="historyLoading" key="loading" class="h-full min-h-180px flex-center" role="status"><NSpin size="small" /></div>
          <NEmpty v-else-if="!recentReports.length" key="empty" class="h-full min-h-120px flex-center" description="暂无已生成报告" />
          <div v-else key="reports" class="flex-col-stretch gap-8px">
            <div v-for="item in recentReports" :key="item.report_id" class="h-72px min-w-0 flex-y-center justify-between gap-12px">
              <div class="min-w-0 flex-1 flex-col-stretch gap-4px"><NText class="line-clamp-2 break-words leading-20px">{{ item.report_title || reportTypeText[item.report_type] || '生产报表' }}</NText><NText depth="3" class="truncate text-12px leading-16px">{{ reportPeriod(item) }} · {{ item.task_count ?? '--' }} 项 · {{ shortDateTime(item.generated_at || item.created_at) }}</NText></div>
              <NTag size="small" :bordered="false" :type="item.status === 'completed' ? 'success' : item.status === 'failed' ? 'error' : 'info'">{{ statusText[item.status] || item.status }}</NTag>
              <NButton v-if="item.view_url" tag="a" :href="item.view_url" target="_blank" rel="noopener" text type="primary">打开</NButton><NText v-else depth="3">—</NText>
            </div>
          </div>
        </Transition>
      </NCard>
      <NCard :bordered="false" size="small" class="card-wrapper bg-container" aria-labelledby="quick-card-title">
        <template #header><HomeSectionHeader heading-id="quick-card-title" title="快捷入口" icon="grid" /></template>
        <nav class="grid grid-cols-1 gap-16px" aria-label="核心报表快速入口">
          <RouterLink v-for="item in quickLinks" :key="item.to" :to="item.to" class="flex-y-center gap-12px rd-8px p-12px text-base-text no-underline transition-all-300 hover:bg-primary/10 focus-visible:outline-primary">
            <span class="h-36px w-36px flex-center shrink-0 rd-8px bg-primary/10 text-primary"><AppIcon :name="item.icon" :size="20" tone="current" /></span><NText class="flex-1">{{ item.title }}</NText><AppIcon name="chevron-right" :size="16" tone="current" class="text-primary" />
          </RouterLink>
        </nav>
      </NCard>
      <NCard :bordered="false" size="small" class="card-wrapper bg-container xl:col-span-2" header-style="padding-bottom: 0" content-style="padding-top: var(--n-padding-bottom)" content-class="flex flex-col justify-center" aria-labelledby="insights-card-title">
        <template #header><HomeSectionHeader heading-id="insights-card-title" title="智能洞察" icon="sparkles" to="/admin/reports/leadership" action="查看更多" /></template>
        <Transition name="fade" mode="out-in">
          <div v-if="overviewLoading || alarmLoading" key="loading" class="min-h-180px flex-center" role="status"><NSpin size="small" /></div>
          <NEmpty v-else-if="!overview" key="empty" class="py-24px" description="暂无生产洞察" />
          <div v-else key="insights" class="grid grid-cols-1 gap-16px sm:grid-cols-2">
            <div class="flex-y-center gap-12px rd-8px bg-primary/10 p-16px"><AppIcon name="chart" :size="22" tone="current" class="text-primary" /><div class="min-w-0 flex-col-stretch gap-8px"><NText>生产概况</NText><NText depth="3" class="text-12px">本月完成 {{ total.task_count }} 项任务</NText></div></div>
            <div class="flex-y-center gap-12px rd-8px p-16px" :class="openAlarms.length ? 'bg-error/10 text-error' : 'bg-success/10 text-success'"><AppIcon :name="openAlarms.length ? 'alert' : 'check'" :size="22" tone="current" /><div class="min-w-0 flex-col-stretch gap-8px"><NText>异常发现</NText><NText depth="3" class="text-12px">{{ openAlarms.length ? `发现 ${openAlarms.length} 项未解除告警` : '当前未发现异常告警' }}</NText></div></div>
            <div class="flex-y-center gap-12px rd-8px bg-primary/10 p-16px"><AppIcon name="trend" :size="22" tone="current" class="text-primary" /><div class="min-w-0 flex-col-stretch gap-8px"><NText>趋势变化</NText><NText depth="3" class="text-12px">{{ insightTrend }}</NText></div></div>
            <div class="flex-y-center gap-12px rd-8px bg-warning/10 p-16px"><AppIcon name="lightbulb" :size="22" tone="current" class="text-warning" /><div class="min-w-0 flex-col-stretch gap-8px"><NText>建议关注</NText><NText depth="3" class="text-12px">{{ insightAdvice }}</NText></div></div>
          </div>
        </Transition>
      </NCard>
    </section>
  </div>
</template>
