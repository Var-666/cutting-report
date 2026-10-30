<script setup lang="ts">
import type { WeldingReport, WeldingJobRow } from '../types/welding'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { NButton, NCard, NEmpty, NModal } from 'naive-ui'
import AppIcon from '../components/AppIcon.vue'
import HomeSectionHeader from '../components/HomeSectionHeader.vue'
import WeldingKpiCards from '../components/WeldingKpiCards.vue'
import WeldingChart from '../components/WeldingChart.vue'
import WeldingTable from '../components/WeldingTable.vue'
import WeldingRecord from '../components/WeldingRecord.vue'
import ReportFeedback from '../components/ReportFeedback.vue'
import ReportPanelState from '../components/ReportPanelState.vue'
import { getWeldingReport } from '../api/welding'
import { reportToday } from '../api/reportDates'
import { deviceSelection } from '../state/devices'
import { selectedLineType } from '../utils/productionLines'
import { parseWeldingRouteQuery } from '../utils/weldingRouteState'
import { weldingDescriptions, weldingDisplay, weldingProcessRows, weldingTitles } from '../utils/weldingReport'
import { weldingTrendOption, weldingLengthOption, weldingProcessOption } from '../utils/weldingChartOptions'
import { useReportChartTheme } from '../composables/useReportChartTheme'
import { toError } from '../utils/errors'

const route = useRoute()
const router = useRouter()
const today = reportToday()
const displayDate = new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: 'long', day: 'numeric', weekday: 'long', timeZone: 'Asia/Shanghai' }).format(new Date())
const report = ref<WeldingReport<'jobs'> | null>(null)
const loading = ref(false)
const error = ref('')
const notice = ref('')
const detailJob = ref<WeldingJobRow | null>(null)
const detailOpen = ref(false)
const sourceEnabled = computed(() => deviceSelection.items.find(item => item.id === deviceSelection.id)?.enabled !== false)
const query = computed(() => parseWeldingRouteQuery(route.query, 'dashboard', today))
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
  { kind: 'daily', icon: 'chart' }, { kind: 'parts', icon: 'grid' },
  { kind: 'jobs', icon: 'activity' }, { kind: 'equipment', icon: 'factory' },
] as const
let controller: AbortController | undefined
let timer: ReturnType<typeof setTimeout> | undefined
let generation = 0
let disposed = false
let pollCount = 0

async function load(force = false, background = false) {
  clearTimeout(timer)
  controller?.abort()
  const run = ++generation
  const line = deviceSelection.id
  if (!background) pollCount = 0
  if (!sourceEnabled.value || selectedLineType(deviceSelection.items, line) !== 'welding' || disposed) { loading.value = false; return }
  controller = new AbortController()
  loading.value = !background
  error.value = ''
  try {
    const value = await getWeldingReport('dashboard', query.value, controller.signal, force)
    if (disposed || run !== generation || deviceSelection.id !== line) return
    report.value = value
    if (value.meta.report_data?.refreshing && pollCount < 75) {
      pollCount += 1
      timer = setTimeout(() => { void load(false, true) }, 5000)
    }
  } catch (caught) {
    const exception = toError(caught)
    if (run === generation && exception.name !== 'AbortError') error.value = exception.message
  } finally { if (run === generation) loading.value = false }
}
function openJob(row: object) { detailJob.value = row as WeldingJobRow; detailOpen.value = true }
async function copy(value: string) {
  try { await navigator.clipboard.writeText(value); notice.value = '流水号已复制' }
  catch { notice.value = '复制失败，请选择流水号后手动复制' }
}
function goToPart() {
  if (detailJob.value?.serial_number) router.push({ name: 'welding-parts', query: { serial_number: detailJob.value.serial_number, start: query.value.start, end: query.value.end } })
}
watch(() => [deviceSelection.id, sourceEnabled.value, selectedLineType(deviceSelection.items, deviceSelection.id), route.query], () => {
  report.value = null
  detailOpen.value = false
  void load()
}, { immediate: true })
onBeforeUnmount(() => { disposed = true; generation += 1; clearTimeout(timer); controller?.abort() })
</script>

<template>
  <div class="welding-home">
    <NCard :bordered="false" size="small" class="card-wrapper home-welcome">
      <div class="welding-welcome">
        <div><h1>小组立焊接生产指挥中心</h1><p>今日生产概览</p><div class="welding-date"><AppIcon name="calendar" :size="16" tone="current" /><span>{{ displayDate }}</span><NButton size="small" :disabled="loading || !sourceEnabled" :loading="loading" @click="load(true)">刷新概览</NButton></div></div>
        <div class="welding-welcome-art" aria-hidden="true"><AppIcon name="factory" :size="92" tone="current" /><AppIcon name="sparkles" :size="34" tone="current" /></div>
      </div>
    </NCard>
    <ReportFeedback v-if="!sourceEnabled" title="焊接数据库尚未启用" message="保存连接并启用小组立焊接线后，即可查看生产概览。" action-label="配置数据库" @action="router.push('/admin/settings/database')" />
    <ReportFeedback v-else-if="error" type="error" title="生产概览读取失败" :message="`${error}${report ? '；当前保留上次成功结果。' : ''}`" action-label="重试" @action="load(true)" />
    <ReportPanelState v-else-if="loading && !report" type="loading" title="正在读取焊接生产概览" message="正在准备今日指标与近七天生产数据，完成后将自动显示。" />
    <ReportFeedback v-if="notice" title="操作提示" :message="notice" dismissible @dismiss="notice = ''" />
    <WeldingKpiCards v-if="sourceEnabled" kind="dashboard" :report="report" :loading="loading" />
    <template v-if="report">
      <div class="welding-home-meta" role="status"><span>数据更新：{{ report.meta.report_data?.data_as_of || report.meta.calculated_at }}</span><span v-if="report.meta.report_data?.refresh_failed">刷新失败，保留上次结果</span><span v-else-if="report.meta.report_data?.refreshing">后台更新中</span><span v-else-if="report.meta.report_data?.stale">数据待更新</span><span>趋势与结构：{{ query.start }} 至 {{ query.end }}{{ query.device_no !== '' ? ` · 设备 ${query.device_no}` : '' }}</span></div>
      <section class="welding-home-grid" aria-label="生产趋势与工艺结构">
        <NCard :bordered="false" size="small" class="card-wrapper">
          <template #header><HomeSectionHeader title="近七天生产趋势" icon="trend" to="/admin/welding/reports/daily" action="生产统计" /></template>
          <div class="welding-home-chart"><WeldingChart v-if="daily.length" :option="trendOption" label="每日焊接长度与作业次数" /><NEmpty v-else description="当前范围暂无生产趋势" /></div>
          <details v-if="daily.length" class="welding-chart-data"><summary>查看生产趋势数据</summary><div class="welding-chart-data-scroll" role="region" aria-label="生产趋势数据" tabindex="0"><table><caption>每日焊接长度与作业次数（{{ query.start }} 至 {{ query.end }}）</caption><thead><tr><th scope="col">日期</th><th scope="col">焊接长度(m)</th><th scope="col">作业次数</th></tr></thead><tbody><tr v-for="row in daily" :key="row.production_date"><th scope="row">{{ row.production_date }}</th><td>{{ weldingDisplay(row.weld_length_m, 'weld_length_m') }}</td><td>{{ weldingDisplay(row.job_count, 'job_count') }}</td></tr></tbody></table></div></details>
        </NCard>
        <NCard :bordered="false" size="small" class="card-wrapper">
          <template #header><HomeSectionHeader title="焊接工艺结构" icon="activity" to="/admin/welding/reports/jobs" action="查看作业" /></template>
          <div class="welding-home-chart"><WeldingChart v-if="processes.some(row => row.length !== null && row.length > 0)" :option="processOption" label="按焊接长度统计的工艺结构，包含未知工艺" /><NEmpty v-else description="暂无有效工艺长度数据" /></div>
          <p class="welding-home-note">按当前范围有效焊接长度统计，未知工艺独立保留。</p>
          <details v-if="processes.some(row => row.length !== null && row.length > 0)" class="welding-chart-data"><summary>查看工艺结构数据</summary><div class="welding-chart-data-scroll" role="region" aria-label="工艺结构数据" tabindex="0"><table><caption>各工艺焊接长度（{{ query.start }} 至 {{ query.end }}）</caption><thead><tr><th scope="col">焊接工艺</th><th scope="col">焊接长度(m)</th></tr></thead><tbody><tr v-for="row in processes" :key="row.key"><th scope="row">{{ row.name }}</th><td>{{ weldingDisplay(row.length, 'weld_length_m') }}</td></tr></tbody></table></div></details>
        </NCard>
      </section>
      <section class="welding-home-grid" aria-label="设备贡献与数据核查">
        <NCard :bordered="false" size="small" class="card-wrapper">
          <template #header><HomeSectionHeader title="设备产出对比" icon="factory" to="/admin/welding/reports/equipment" action="查看设备效能报表" /></template>
          <div class="welding-home-chart"><WeldingChart v-if="devices.length" :option="deviceOption" label="按累计焊接长度排序的设备产出" /><NEmpty v-else description="暂无设备产出数据" /></div>
          <details v-if="devices.length" class="welding-chart-data"><summary>查看设备产出数据</summary><div class="welding-chart-data-scroll" role="region" aria-label="设备产出数据" tabindex="0"><table><caption>各设备累计焊接长度（{{ query.start }} 至 {{ query.end }}）</caption><thead><tr><th scope="col">设备</th><th scope="col">焊接长度(m)</th></tr></thead><tbody><tr v-for="row in devices" :key="String(row.device_no)"><th scope="row">设备 {{ row.device_no ?? '未记录' }}</th><td>{{ weldingDisplay(row.weld_length_m, 'weld_length_m') }}</td></tr></tbody></table></div></details>
        </NCard>
        <NCard :bordered="false" size="small" class="card-wrapper">
          <template #header><HomeSectionHeader title="数据核查概况" icon="search" to="/admin/welding/reports/jobs" action="核查作业" /></template>
          <dl class="welding-home-quality"><div v-for="item in quality" :key="item.label"><dt>{{ item.label }}</dt><dd :class="{ 'has-candidates': item.value !== undefined && item.value > 0 }">{{ item.value ?? '未提供' }}</dd></div></dl>
          <p class="welding-home-note">各项候选可重叠，数量不可相加；数据核查不代表设备故障。</p>
        </NCard>
      </section>
      <NCard :bordered="false" size="small" class="card-wrapper">
        <template #header><HomeSectionHeader title="最近焊接作业" icon="tasks" to="/admin/welding/reports/jobs" action="查看全部作业" /></template>
        <WeldingTable kind="jobs" :items="report.items.slice(0, 10)" :paginate="false" @detail="openJob" @copy="copy" />
        <p class="welding-home-note">当前范围内最新 10 条真实记录，原始状态含义待确认。</p>
      </NCard>
    </template>
    <section aria-label="焊接报表快捷入口">
      <HomeSectionHeader title="生产报表" icon="report" />
      <div class="welding-home-links"><RouterLink v-for="link in links" :key="link.kind" :to="{ name: `welding-${link.kind}` }" class="panel"><AppIcon :name="link.icon" :size="22" tone="current" /><div><h3>{{ weldingTitles[link.kind] }}</h3><p>{{ weldingDescriptions[link.kind] }}</p></div><AppIcon name="chevron-right" :size="18" tone="current" /></RouterLink></div>
    </section>
    <NModal v-model:show="detailOpen" preset="card" title="焊接作业详情" :style="{ width: 'min(760px, 94vw)' }">
      <template v-if="detailJob"><WeldingRecord kind="jobs" :row="detailJob" /><NButton :disabled="!detailJob.serial_number" @click="goToPart">查询该流水号的工件</NButton></template>
    </NModal>
  </div>
</template>

<style scoped>
.welding-home{display:flex;flex-direction:column;gap:16px;min-width:0}.welding-welcome{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:8px 12px}.welding-welcome h1{font-size:22px;font-weight:600;margin:0 0 10px}.welding-welcome p{margin:0 0 12px;color:var(--text-color-2)}.welding-date{display:flex;align-items:center;flex-wrap:wrap;gap:8px;color:var(--text-color-2);font-size:12px}.welding-welcome-art{display:flex;align-items:center;justify-content:center;gap:8px;width:200px;min-height:118px;flex-shrink:0;color:rgb(var(--primary-color));border-radius:18px;background:rgb(var(--primary-color) / .06)}.welding-home-grid{display:grid;grid-template-columns:minmax(0,1.7fr) minmax(0,1fr);gap:16px}.welding-home-grid>*{min-width:0}.welding-home-chart{height:260px;display:flex;align-items:center;justify-content:center;min-width:0}.welding-home-note{font-size:12px;line-height:1.7;color:var(--text-color-2);margin:10px 0 0}.welding-home-meta{display:flex;flex-wrap:wrap;gap:8px 16px;font-size:12px;color:var(--text-color-2)}.welding-home-quality{margin:0}.welding-home-quality>div{display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:38px;border-bottom:1px solid var(--theme-border-color)}.welding-home-quality dt{font-size:12px;color:var(--text-color-2)}.welding-home-quality dd{margin:0;font-size:18px;font-weight:600;font-variant-numeric:tabular-nums}.has-candidates{color:rgb(var(--warning-color))}.welding-home-links{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px;margin-top:12px}.welding-home-links>a{display:flex;align-items:center;gap:12px;margin:0;padding:18px 16px;text-decoration:none;color:var(--text-color-1)}.welding-home-links>a>div{flex:1;min-width:0}.welding-home-links>a>.app-icon:first-child{color:rgb(var(--primary-color))}.welding-home-links h3{font-size:14px;margin:0 0 6px}.welding-home-links p{font-size:12px;line-height:1.6;margin:0;color:var(--text-color-2)}@media(max-width:1100px){.welding-home-links{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:900px){.welding-home-grid{grid-template-columns:1fr}}@media(max-width:600px){.welding-welcome{padding:4px}.welding-welcome h1{font-size:20px}.welding-welcome-art{display:none}.welding-home-links{grid-template-columns:1fr}}
.welding-chart-data{margin-top:12px;color:var(--text-color-2);font-size:12px}.welding-chart-data summary{cursor:pointer;min-height:28px;display:list-item;line-height:28px}.welding-chart-data-scroll{max-height:280px;overflow:auto;margin-top:8px}.welding-chart-data table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}.welding-chart-data caption{text-align:left;padding:8px 0}.welding-chart-data th,.welding-chart-data td{padding:8px;border-bottom:1px solid var(--theme-border-color);text-align:right;white-space:nowrap}.welding-chart-data th:first-child{text-align:left}.welding-chart-data-scroll:focus-visible{outline:2px solid rgb(var(--primary-color));outline-offset:2px}
</style>
