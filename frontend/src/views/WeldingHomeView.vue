<script setup lang="ts">
import type { WeldingReport, WeldingJobRow } from '../types/welding'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { NButton, NCard, NEmpty, NDrawer, NDrawerContent } from 'naive-ui'
import AppIcon from '../components/AppIcon.vue'
import HomeSectionHeader from '../components/HomeSectionHeader.vue'
import WeldingKpiCards from '../components/WeldingKpiCards.vue'
import WeldingChart from '../components/WeldingChart.vue'
import WeldingTable from '../components/WeldingTable.vue'
import WeldingRecord from '../components/WeldingRecord.vue'
import ReportFeedback from '../components/ReportFeedback.vue'
import ReportDataStatus from '../components/ReportDataStatus.vue'
import ReportPanelState from '../components/ReportPanelState.vue'
import { getWeldingReport } from '../api/welding'
import { REPORT_POLL_INTERVAL, REPORT_POLL_LIMIT } from '../api/reportRequests'
import { reportToday } from '../api/reportDates'
import { formatChineseDate } from '../utils/dateDisplay'
import { deviceSelection } from '../state/devices'
import { selectedLineType } from '../utils/productionLines'
import { parseWeldingRouteQuery } from '../utils/weldingRouteState'
import { weldingDisplay, weldingProcessRows, weldingTitles } from '../utils/weldingReport'
import { weldingTrendOption, weldingLengthOption, weldingProcessOption } from '../utils/weldingChartOptions'
import { useReportChartTheme } from '../composables/useReportChartTheme'
import { toError } from '../utils/errors'

const route = useRoute()
const router = useRouter()
const today = ref(reportToday())
const displayDate = computed(() => formatChineseDate(today.value))
const report = ref<WeldingReport<'jobs'> | null>(null)
const loading = ref(false)
const preparing = ref(false)
const error = ref('')
const notice = ref('')
const detailJob = ref<WeldingJobRow | null>(null)
const detailOpen = ref(false)
const line = computed(() => deviceSelection.items.find(item => item.id === deviceSelection.id))
const sourceEnabled = computed(() => line.value?.enabled !== false)
const query = computed(() => parseWeldingRouteQuery(route.query, 'dashboard', today.value))
const applied = computed(() => report.value?.query ?? query.value)
const rangeLabel = computed(() => `${applied.value.start} 至 ${applied.value.end}${applied.value.device_no != null && applied.value.device_no !== '' ? ` · 设备 ${applied.value.device_no}` : ''}`)
const trendTitle = computed(() => {
  const week = parseWeldingRouteQuery({}, 'dashboard', today.value)
  return applied.value.start === week.start && applied.value.end === week.end ? '近七天生产趋势' : '区间生产趋势'
})
const statusMeta = computed(() => {
  const meta = report.value?.meta
  const state = meta?.report_data
  if (!state) return meta
  return { report_data: { ...state, stale: state.stale && !state.refreshing && !state.refresh_failed && !error.value, refreshing: state.refreshing && !error.value } }
})
const { colors, vars } = useReportChartTheme(() => {})
const daily = computed(() => [...(report.value?.daily || [])].sort((a, b) => a.production_date.localeCompare(b.production_date)))
const devices = computed(() => [...(report.value?.device_distribution || [])].sort((a, b) => b.weld_length_m - a.weld_length_m))
const processes = computed(() => weldingProcessRows(report.value?.summary || {}))
const trendOption = computed(() => weldingTrendOption(daily.value, colors.value.primary, colors.value.secondary))
const deviceOption = computed(() => weldingLengthOption(devices.value.map(row => `设备 ${row.device_no ?? '未记录'}`), devices.value.map(row => row.weld_length_m), colors.value.primary))
const processOption = computed(() => weldingProcessOption(processes.value, [colors.value.primary, colors.value.secondary, vars.value.errorColor, colors.value.accent, vars.value.textColor3]))
const quality = computed(() => [
  { label: '结束时间异常候选', value: report.value?.meta.quality.invalid_time_rows },
  { label: '焊接长度异常候选', value: report.value?.meta.quality.invalid_length_rows },
  { label: '作业用时异常候选', value: report.value?.meta.quality.invalid_duration_rows },
  { label: '流水号无效候选', value: report.value?.meta.quality.invalid_serial_rows },
  { label: '未关联工件的有效流水号', value: report.value?.meta.unlinked_part_count },
  { label: '存在多条工件归属的流水号', value: report.value?.meta.ambiguous_part_count },
])
const links = [
  { kind: 'daily', icon: 'chart' }, { kind: 'jobs', icon: 'activity' },
  { kind: 'parts', icon: 'grid' }, { kind: 'equipment', icon: 'factory' },
] as const
const reportQuery = computed(() => {
  const { start, end, device_no } = applied.value
  return { start, end, ...(device_no != null && device_no !== '' ? { device_no: String(device_no) } : {}) }
})
function reportLink(kind: typeof links[number]['kind']) {
  return router.resolve({ name: `welding-${kind}`, query: reportQuery.value }).fullPath
}
let controller: AbortController | undefined
let timer: ReturnType<typeof setTimeout> | undefined
let generation = 0
let disposed = false
let pollCount = 0

async function load(force = false, background = false) {
  if (force && loading.value) return
  const nextDay = force ? reportToday() : today.value
  if (today.value !== nextDay) {
    if (JSON.stringify(query.value) !== JSON.stringify(parseWeldingRouteQuery(route.query, 'dashboard', nextDay))) {
      today.value = nextDay
      return
    }
    today.value = nextDay
  }
  clearTimeout(timer)
  controller?.abort()
  const run = ++generation
  const lineId = deviceSelection.id
  const current = () => !disposed && run === generation && deviceSelection.id === lineId
  if (!background) pollCount = 0
  preparing.value = false
  if (!sourceEnabled.value || selectedLineType(deviceSelection.items, lineId) !== 'welding' || disposed) { loading.value = false; return }
  controller = new AbortController()
  loading.value = !background
  error.value = ''
  try {
    const value = await getWeldingReport('dashboard', query.value, controller.signal, force, {
      lineId, mode: background ? 'poll' : force ? 'refresh' : 'read',
      onPreparing: () => { if (current()) preparing.value = true },
    })
    if (!current()) return
    report.value = value
    if (value.meta.report_data?.refreshing) {
      if (pollCount >= REPORT_POLL_LIMIT) error.value = '等待生产数据更新超时。'
      else {
        pollCount += 1
        timer = setTimeout(() => { void load(false, true) }, REPORT_POLL_INTERVAL)
      }
    }
  } catch (caught) {
    const exception = toError(caught)
    if (current() && exception.name !== 'AbortError') error.value = exception.message
  } finally { if (current()) { loading.value = false; preparing.value = false } }
}
function openJob(row: object) { detailJob.value = row as WeldingJobRow; detailOpen.value = true }
async function copy(value: string) {
  try { await navigator.clipboard.writeText(value); notice.value = '流水号已复制' }
  catch { notice.value = '复制失败，请选择流水号后手动复制' }
}
function goToPart() {
  if (detailJob.value?.serial_number) router.push({ name: 'welding-parts', query: { ...reportQuery.value, serial_number: detailJob.value.serial_number } })
}
watch([() => deviceSelection.id, () => line.value?.source_key, () => sourceEnabled.value,
  () => line.value?.type, () => JSON.stringify(query.value)], () => {
  report.value = null
  detailOpen.value = false
  detailJob.value = null
  notice.value = ''
  error.value = ''
  void load()
}, { immediate: true })
onBeforeUnmount(() => { disposed = true; generation += 1; clearTimeout(timer); controller?.abort() })
</script>

<template>
  <div class="welding-home">
    <header class="page-heading">
      <h1 class="page-heading__title">产线概览</h1>
      <div class="page-heading__actions welding-home-actions">
        <time :datetime="today">{{ displayDate }}</time>
        <NButton size="small" :disabled="loading || !sourceEnabled" :loading="loading" @click="load(true)"><template #icon><AppIcon name="activity" :size="16" tone="current" /></template>刷新概览</NButton>
      </div>
    </header>
    <ReportFeedback v-if="!sourceEnabled" title="焊接产线未启用" message="请检查是否已配置数据源并启用此产线。" action-label="配置数据库" @action="router.push('/admin/settings/database')" />
    <ReportFeedback v-else-if="error" type="error" title="生产概览读取失败" :message="report ? '当前保留上次成功结果，可以重试。' : '暂时无法读取当前范围，可以重试。'" action-label="重试" @action="load(true)" />
    <ReportPanelState v-else-if="loading && !report" type="loading" class="card-wrapper" :title="preparing ? '焊接生产数据准备中' : '正在读取焊接生产概览'" message="当日指标和所选区间统计就绪后将自动显示。" />
    <ReportFeedback v-if="notice" title="操作提示" :message="notice" dismissible @dismiss="notice = ''" />
    <WeldingKpiCards v-if="sourceEnabled" kind="dashboard" :report="report" :loading="loading" :title="report ? `当日指标按焊接生产日统计（${report.meta.timezone || '时区未提供'}），不随趋势区间变化` : undefined" />
    <template v-if="report">
      <div class="welding-home-meta"><ReportDataStatus v-if="report.meta.report_data" :meta="statusMeta" :busy="loading" @refresh="load(true)" /><span :title="`焊接生产时区：${report.meta.timezone || '未知'}`">区间统计：{{ rangeLabel }}</span></div>
      <section class="welding-home-grid" aria-label="生产趋势与工艺结构">
        <NCard :bordered="false" size="small" class="card-wrapper">
          <template #header><HomeSectionHeader :title="trendTitle" icon="trend" :to="reportLink('daily')" action="生产统计" /></template>
          <div class="welding-home-chart"><WeldingChart v-if="daily.length" :option="trendOption" label="每日焊接长度与作业次数" /><NEmpty v-else description="当前范围暂无生产趋势" /></div>
          <details v-if="daily.length" class="welding-chart-data"><summary>查看生产趋势数据</summary><div class="welding-chart-data-scroll" role="region" aria-label="生产趋势数据" tabindex="0"><table><caption>每日焊接长度与作业次数（{{ applied.start }} 至 {{ applied.end }}）</caption><thead><tr><th scope="col">日期</th><th scope="col">焊接长度(m)</th><th scope="col">作业次数</th></tr></thead><tbody><tr v-for="row in daily" :key="row.production_date"><th scope="row" :title="row.production_date"><span>{{ row.production_date }}</span></th><td :title="weldingDisplay(row.weld_length_m, 'weld_length_m')"><span>{{ weldingDisplay(row.weld_length_m, 'weld_length_m') }}</span></td><td :title="weldingDisplay(row.job_count, 'job_count')"><span>{{ weldingDisplay(row.job_count, 'job_count') }}</span></td></tr></tbody></table></div></details>
        </NCard>
        <NCard :bordered="false" size="small" class="card-wrapper">
          <template #header><HomeSectionHeader title="焊接工艺结构" icon="activity" :to="reportLink('jobs')" action="查看作业" /></template>
          <div class="welding-home-chart"><WeldingChart v-if="processes.some(row => row.length !== null && row.length > 0)" :option="processOption" label="按焊接长度统计的工艺结构，包含未知工艺" /><NEmpty v-else description="暂无有效工艺长度数据" /></div>
          <details v-if="processes.some(row => row.length !== null && row.length > 0)" class="welding-chart-data"><summary>查看工艺结构数据</summary><div class="welding-chart-data-scroll" role="region" aria-label="工艺结构数据" tabindex="0"><table><caption>各工艺焊接长度（{{ applied.start }} 至 {{ applied.end }}）</caption><thead><tr><th scope="col">焊接工艺</th><th scope="col">焊接长度(m)</th></tr></thead><tbody><tr v-for="row in processes" :key="row.key"><th scope="row" :title="row.name"><span>{{ row.name }}</span></th><td :title="weldingDisplay(row.length, 'weld_length_m')"><span>{{ weldingDisplay(row.length, 'weld_length_m') }}</span></td></tr></tbody></table></div></details>
        </NCard>
      </section>
      <section class="welding-home-grid" aria-label="设备贡献与数据核查">
        <NCard :bordered="false" size="small" class="card-wrapper">
          <template #header><HomeSectionHeader title="设备产出对比" icon="factory" :to="reportLink('equipment')" action="查看设备效能报表" /></template>
          <p v-if="report?.meta?.pending_metrics?.device_distribution" class="welding-home-note">{{ report.meta.pending_metrics.device_distribution }}</p>
          <div class="welding-home-chart"><WeldingChart v-if="devices.length" :option="deviceOption" label="按累计焊接长度排序的设备产出" /><NEmpty v-else description="暂无设备产出数据" /></div>
          <details v-if="devices.length" class="welding-chart-data"><summary>查看设备产出数据</summary><div class="welding-chart-data-scroll" role="region" aria-label="设备产出数据" tabindex="0"><table><caption>各设备累计焊接长度（{{ applied.start }} 至 {{ applied.end }}）</caption><thead><tr><th scope="col">设备</th><th scope="col">焊接长度(m)</th></tr></thead><tbody><tr v-for="row in devices" :key="String(row.device_no)"><th scope="row" :title="`设备 ${row.device_no ?? '未记录'}`"><span>设备 {{ row.device_no ?? '未记录' }}</span></th><td :title="weldingDisplay(row.weld_length_m, 'weld_length_m')"><span>{{ weldingDisplay(row.weld_length_m, 'weld_length_m') }}</span></td></tr></tbody></table></div></details>
        </NCard>
        <NCard :bordered="false" size="small" class="card-wrapper">
          <template #header><HomeSectionHeader title="数据核查概况" icon="search" :to="reportLink('jobs')" action="核查作业" /></template>
          <dl class="welding-home-quality"><div v-for="item in quality" :key="item.label"><dt>{{ item.label }}</dt><dd :class="{ 'has-candidates': item.value !== undefined && item.value > 0 }">{{ item.value ?? '未提供' }}</dd></div></dl>
          <p class="welding-home-note">各项候选可重叠，数量不可相加；数据核查不代表设备故障。</p>
        </NCard>
      </section>
    </template>
    <div class="welding-home-bottom" :class="{ 'welding-home-bottom--single': !report }">
      <NCard v-if="report" :bordered="false" size="small" class="card-wrapper welding-recent">
        <template #header><HomeSectionHeader title="最近焊接作业" icon="tasks" :to="reportLink('jobs')" action="查看全部作业" /></template>
        <WeldingTable kind="jobs" :items="report.items.slice(0, 10)" :paginate="false" @detail="openJob" @copy="copy" />
        <p class="welding-home-note">原始状态含义待确认。</p>
      </NCard>
      <NCard :bordered="false" size="small" class="card-wrapper welding-shortcuts">
        <template #header><HomeSectionHeader title="生产报表" icon="report" /></template>
        <nav class="welding-home-links" aria-label="焊接报表快捷入口">
          <RouterLink v-for="link in links" :key="link.kind" :to="{ name: `welding-${link.kind}`, query: reportQuery }">
            <span class="welding-link-icon"><AppIcon :name="link.icon" :size="20" tone="current" /></span>
            <strong>{{ weldingTitles[link.kind] }}</strong>
            <AppIcon name="chevron-right" :size="16" tone="current" />
          </RouterLink>
        </nav>
      </NCard>
    </div>
    <NDrawer v-model:show="detailOpen" width="min(760px, 100vw)" aria-label="焊接作业详情">
      <NDrawerContent title="焊接作业详情" closable>
      <template v-if="detailJob"><WeldingRecord kind="jobs" :row="detailJob" /><NButton :disabled="!detailJob.serial_number" @click="goToPart">查询该流水号的工件</NButton></template>
      </NDrawerContent>
    </NDrawer>
  </div>
</template>

<style scoped>
.welding-home { display:flex; flex-direction:column; gap:var(--space-4); min-width:0; container:welding-home / inline-size; }
.welding-home>.report-feedback { margin:0; }
.welding-home-actions time { color:var(--text-color-3); font-size:var(--font-auxiliary); }
.welding-home-grid { display:grid; grid-template-columns:minmax(0,2fr) minmax(0,1fr); gap:var(--space-4); }
.welding-home-grid>* { min-width:0; }
.welding-home-chart { height:240px; display:flex; align-items:center; justify-content:center; min-width:0; }
.welding-home-note { font-size:var(--font-auxiliary); line-height:1.7; color:var(--text-color-3); margin:var(--space-3) 0 0; }
.welding-home-meta { display:flex; align-items:center; flex-wrap:wrap; gap:var(--space-2) var(--space-4); color:var(--text-color-3); font-size:var(--font-auxiliary); }
.welding-home-quality { margin:0; }
.welding-home-quality>div { display:flex; align-items:center; justify-content:space-between; gap:var(--space-3); min-height:36px; border-bottom:1px solid var(--theme-divider-color); }
.welding-home-quality>div:last-child { border-bottom:0; }
.welding-home-quality dt { font-size:var(--font-auxiliary); color:var(--text-color-2); }
.welding-home-quality dd { margin:0; font-size:18px; font-weight:600; font-variant-numeric:tabular-nums; }
.has-candidates { color:rgb(var(--warning-color)); }
.welding-home-bottom { display:grid; grid-template-columns:minmax(0,1fr) 300px; gap:var(--space-4); align-items:start; }
.welding-home-bottom>* { min-width:0; }
.welding-home-bottom--single { grid-template-columns:minmax(0,1fr); }
.welding-home-links { display:grid; gap:var(--space-2); }
.welding-home-links>a { display:flex; align-items:center; gap:var(--space-3); min-width:0; padding:var(--space-3); border-radius:var(--card-radius); text-decoration:none; color:var(--text-color-1); transition:background .18s ease; }
.welding-home-links>a:hover { background:rgb(var(--primary-color) / .08); }
.welding-home-links>a:focus-visible { outline:2px solid rgb(var(--primary-color)); outline-offset:2px; }
.welding-home-links>a>div { flex:1; min-width:0; }
.welding-link-icon { width:36px; height:36px; flex-shrink:0; display:grid; place-items:center; border-radius:var(--card-radius); color:rgb(var(--primary-color)); background:rgb(var(--primary-color) / .1); }
.welding-home-links strong { font-weight:600; overflow-wrap:anywhere; }
@container welding-home (max-width:1200px) { .welding-home-bottom { grid-template-columns:minmax(0,1fr); } .welding-home-links { grid-template-columns:repeat(2,minmax(0,1fr)); } }
@container welding-home (max-width:760px) { .welding-home-grid { grid-template-columns:minmax(0,1fr); } }
@container welding-home (max-width:560px) {
  .welding-home-actions { width:100%; justify-content:space-between; }
  .welding-home-links { grid-template-columns:minmax(0,1fr); }
  .welding-home-meta { align-items:flex-start; }
}
.welding-chart-data{margin-top:12px;color:var(--text-color-2);font-size:12px}.welding-chart-data summary{cursor:pointer;min-height:28px;display:list-item;line-height:28px}.welding-chart-data-scroll{max-height:280px;overflow:auto;margin-top:8px}.welding-chart-data table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}.welding-chart-data caption{text-align:left;padding:8px 0}.welding-chart-data th,.welding-chart-data td{padding:8px;border-bottom:1px solid var(--theme-border-color);text-align:right;white-space:nowrap}.welding-chart-data td>span,.welding-chart-data th>span{display:inline-block;max-width:344px;overflow:hidden;text-overflow:ellipsis;vertical-align:middle}.welding-chart-data th:first-child{text-align:left}.welding-chart-data-scroll:focus-visible{outline:2px solid rgb(var(--primary-color));outline-offset:2px}
</style>
