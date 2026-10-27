<script setup lang="ts">
import type { DataRow } from '../types/api'
import type { PropType } from 'vue'
import type { WeldingAnalysis, WeldingPart, WeldingQuery, WeldingReport, WeldingReportKind, WeldingJobRow, WeldingPartRow, WeldingMetrics } from '../types/welding'
import { toError } from '../utils/errors'
import { selectedLineType } from '../utils/productionLines.ts'
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NCard, NModal } from 'naive-ui'
import AppDatePicker from '../components/AppDatePicker.vue'
import AppSelect from '../components/AppSelect.vue'
import HomeKpiCard from '../components/HomeKpiCard.vue'
import ReportFeedback from '../components/ReportFeedback.vue'
import ReportPanelState from '../components/ReportPanelState.vue'
import ReportExportMenu from '../components/ReportExportMenu.vue'
import WeldingCharts from '../components/WeldingCharts.vue'
import WeldingTable from '../components/WeldingTable.vue'
import { analyzeWelding, downloadWelding, getWeldingPart, getWeldingReport, weldingQuery } from '../api/welding.ts'
import { reportToday } from '../api/reportDates.ts'
import { queryDateRange, sameRouteQuery } from '../utils/reportRouteState.ts'
import { parseWeldingRouteQuery, weldingRouteQuery } from '../utils/weldingRouteState.ts'
import { deviceSelection } from '../state/devices.ts'
import { weldingColumns, weldingDisplay, weldingKpis, weldingTitles } from '../utils/weldingReport.ts'

const props = defineProps({ kind: { type: String as PropType<WeldingReportKind>, required: true } })
const route = useRoute()
const router = useRouter()
const today = reportToday()
const draft = reactive(parseWeldingRouteQuery(route.query, props.kind, today))
const report = ref<WeldingReport | null>(null)
const activeQuery = ref<WeldingQuery | null>(null)
const loading = ref(false)
const error = ref('')
const actionError = ref('')
const actionNotice = ref('')
const exporting = ref(false)
const analyzing = ref(false)
const analysis = ref<WeldingAnalysis | null>(null)
const detailOpen = ref(false)
const details = ref<WeldingPart | null>(null)
const detailJob = ref<WeldingJobRow | null>(null)
const detailLoading = ref(false)
const detailError = ref('')
const seamPage = ref(1)
const lineName = computed(() => deviceSelection.items.find(item => item.id === deviceSelection.id)?.name || '焊接生产线')
const sourceEnabled = computed(() => deviceSelection.items?.find(item => item.id === deviceSelection.id)?.enabled !== false)
let controller: AbortController | undefined, detailController: AbortController | undefined, exportController: AbortController | undefined, analysisController: AbortController | undefined
let disposed = false
let appliedKind: WeldingReportKind | undefined
let appliedLine = ''
let generation = 0
let detailGeneration = 0
let pendingRead: { key: string; promise: Promise<void> } | null | undefined
const reportKind = computed(() => props.kind === 'dashboard' ? 'jobs' : props.kind)
const cards = computed(() => weldingKpis(props.kind, report.value))
const title = computed(() => weldingTitles[props.kind])
const sourceLabels: Record<string, string> = { weld_info_table: '作业', weld_part_info_table: '工件', welding_seam_info_table: '焊缝' }
const sourceUpdated = computed(() => Object.entries(report.value?.meta?.data_updated_at || {}).map(([key, value]) => `${sourceLabels[key]}：${value || '未记录'}`).join('；'))
const qualityLabels = { invalid_time_rows: '区间候选记录中结束时间无效', invalid_serial_rows: '流水号无效', invalid_length_rows: '长度为空或非正', invalid_duration_rows: '用时为空或非正', unverified_state_rows: '状态含义待确认' }
const analysisSections = [['overview', '生产概况'], ['features', '产出特点'], ['alerts', '数据关注点'], ['suggestions', '核查建议']] as const

function queryKey(values: WeldingQuery) {
  return JSON.stringify([props.kind, deviceSelection.id, parseWeldingRouteQuery(weldingRouteQuery({}, values), props.kind, today)])
}
function read(values: WeldingQuery, force = false, syncRoute = true) {
  const key = queryKey(values)
  if (pendingRead?.key === key && !controller?.signal.aborted) return pendingRead.promise
  const promise = load(values, force, syncRoute)
  pendingRead = { key, promise }
  promise.finally(() => { if (pendingRead?.promise === promise) pendingRead = null })
  return promise
}
async function load(values: WeldingQuery, force: boolean, syncRoute: boolean) {
  if (disposed) return
  const kind = props.kind
  const line = deviceSelection.id
  controller?.abort()
  analysisController?.abort()
  const run = ++generation
  controller = new AbortController()
  loading.value = true
  error.value = ''
  analysis.value = null
  if (!sourceEnabled.value) {
    report.value = null
    activeQuery.value = null
    loading.value = false
    return
  }
  try {
    const value = await getWeldingReport(props.kind, values, controller.signal, force)
    if (disposed || run !== generation || line !== deviceSelection.id || kind !== props.kind || selectedLineType(deviceSelection.items, deviceSelection.id) !== 'welding') return
    report.value = value
    activeQuery.value = value.query
    appliedKind = kind
    appliedLine = line
    const query = weldingRouteQuery(route.query, parseWeldingRouteQuery(weldingRouteQuery({}, value.query), kind, today))
    if (!sameRouteQuery(query, route.query)) {
      if (syncRoute) await router.push({ query })
      else await router.replace({ query })
    }
  } catch (caught) {
    const exception = toError(caught)
    if (run === generation && exception.name !== 'AbortError') error.value = exception.message
  } finally {
    if (run === generation) loading.value = false
  }
}
function submit(force = false) {
  const range = queryDateRange({ start: draft.start, end: draft.end })
  if (!range) { actionError.value = '请选择有效日期区间，最长366天'; return }
  actionError.value = ''
  return read({ ...draft, ...range, page: 1, page_size: activeQuery.value?.page_size || (props.kind === 'dashboard' ? 10 : 20) }, force)
}
function changePage(page: number) { if (!activeQuery.value) return; return read({ ...activeQuery.value, page }) }
function changePageSize(page_size: number) { if (!activeQuery.value) return; return read({ ...activeQuery.value, page: 1, page_size }) }
function goToPart() {
  if (!detailJob.value || !activeQuery.value) return
  router.push({ name: 'welding-parts', query: { serial_number: detailJob.value.serial_number || undefined, start: activeQuery.value.start, end: activeQuery.value.end } })
  detailOpen.value = false
}
async function copy(value: string | null | undefined) {
  if (!value) return
  try { await navigator.clipboard.writeText(value); actionNotice.value = '流水号已复制' }
  catch { actionError.value = '复制失败，请选择流水号后手动复制' }
}
async function openDetail(row: WeldingJobRow | WeldingPartRow, page = 1) {
  detailController?.abort()
  const run = ++detailGeneration
  detailController = new AbortController()
  detailOpen.value = true
  detailError.value = ''
  seamPage.value = page
  if ('weld_type_label' in row) { detailJob.value = row; details.value = null; return }
  detailJob.value = null
  details.value = null
  detailLoading.value = true
  try {
    const value = await getWeldingPart(row.id, page, detailController.signal)
    if (run === detailGeneration) details.value = value
  } catch (caught) {
    const exception = toError(caught)
    if (run === detailGeneration && exception.name !== 'AbortError') detailError.value = exception.message
  } finally { if (run === detailGeneration) detailLoading.value = false }
}
async function exportReport(format: string) {
  if (!activeQuery.value || exporting.value) return
  exportController = new AbortController()
  exporting.value = true
  actionError.value = ''
  try {
    const query = new URLSearchParams(Object.entries({ ...weldingQuery(activeQuery.value), report_type: reportKind.value, format }).map(([key, value]) => [key, String(value)]))
    await downloadWelding(`/api/v1/welding/export?${query}`, `小组立焊接线-${title.value}-${activeQuery.value.start}-${activeQuery.value.end}.${format}`, exportController.signal)
  } catch (caught) {
    const exception = toError(caught)
    if (exception.name !== 'AbortError') actionError.value = exception.message }
  finally { exporting.value = false }
}
async function exportSeams() {
  if (!details.value || exporting.value) return
  exportController = new AbortController()
  exporting.value = true
  try { await downloadWelding(`/api/v1/welding/parts/${details.value.part.id}/seams/export`, `工件-${details.value.part.id}-焊缝.csv`, exportController.signal) }
  catch (caught) {
    const exception = toError(caught)
    if (exception.name !== 'AbortError') detailError.value = exception.message }
  finally { exporting.value = false }
}
async function runAnalysis() {
  if (!report.value || !activeQuery.value || analyzing.value) return
  analysisController = new AbortController()
  analyzing.value = true
  actionError.value = ''
  const version = report.value.data_version
  try {
    const result = await analyzeWelding(reportKind.value, activeQuery.value, version, analysisController.signal)
    if (report.value?.data_version === version) analysis.value = result
  } catch (caught) {
    const exception = toError(caught)
    if (exception.name !== 'AbortError') actionError.value = exception.message }
  finally { analyzing.value = false }
}
function cancel() {
  generation += 1
  detailGeneration += 1
  for (const request of [controller, detailController, exportController, analysisController]) request?.abort()
}
watch(detailOpen, open => { if (!open) { detailController?.abort(); detailGeneration += 1 } })
function restoreRouteState() {
  if (disposed) return
  if (!sourceEnabled.value) {
    cancel()
    loading.value = false
    report.value = null
    activeQuery.value = null
    return
  }
  const values = parseWeldingRouteQuery(route.query, props.kind, today)
  const key = queryKey(values)
  const sameScope = appliedKind === props.kind && appliedLine === deviceSelection.id
  if (sameScope && activeQuery.value && queryKey(activeQuery.value) === key) {
    error.value = ''
    if (pendingRead && pendingRead.key !== key) {
      controller?.abort()
      generation += 1
      loading.value = false
      Object.assign(draft, values)
    }
    return
  }
  if (pendingRead?.key === key && !controller?.signal.aborted) return pendingRead.promise
  if (!sameScope) {
    cancel()
    report.value = null
    activeQuery.value = null
    detailOpen.value = false
  }
  Object.assign(draft, values)
  if (selectedLineType(deviceSelection.items, deviceSelection.id) !== 'welding') return
  return read(values, false, false)
}
watch(() => [props.kind, sourceEnabled.value, deviceSelection.id, route.query], restoreRouteState, { immediate: true })
onBeforeUnmount(() => { disposed = true; cancel() })
</script>

<template>
  <div class="welding-page">
    <header class="welding-heading"><div><h1>{{ title }}</h1><p>{{ lineName }} · {{ kind === 'dashboard' ? '今日指标与最近7天动态' : '按作业结束时间统计，北京时间' }}</p></div><ReportExportMenu :formats="['csv', 'html', 'pdf']" :disabled="!report || loading" :loading="exporting" @select="exportReport" /></header>
    <section v-if="sourceEnabled" class="panel welding-filter-panel">
      <form class="welding-filters" @submit.prevent="submit(true)">
        <template v-if="kind !== 'dashboard'"><label><span>开始日期</span><AppDatePicker v-model="draft.start" required /></label><label><span>结束日期</span><AppDatePicker v-model="draft.end" :min="draft.start" required /></label></template>
        <label><span>设备</span><AppSelect v-model="draft.device_no"><option value="">全部设备</option><option v-for="device in report?.meta?.devices || []" :key="device" :value="device">设备 {{ device }}</option></AppSelect></label>
        <label v-if="['jobs', 'equipment'].includes(kind)"><span>机器人编号</span><input v-model="draft.robot_no" type="number" min="0" step="1" placeholder="全部" /></label>
        <label v-if="['jobs', 'equipment'].includes(kind)"><span>焊接类型</span><AppSelect v-model="draft.weld_type"><option value="">全部工艺</option><option :value="0">平焊包角</option><option :value="1">平焊</option><option :value="2">立焊包角</option><option :value="3">立焊</option></AppSelect></label>
        <template v-if="kind === 'parts'"><label><span>项目编号</span><input v-model.trim="draft.project_no" maxlength="255" placeholder="精确匹配" /></label><label><span>分段号</span><input v-model.trim="draft.section_no" maxlength="255" /></label><label><span>工件编号</span><input v-model.trim="draft.part_no" maxlength="255" /></label></template>
        <template v-if="['parts', 'jobs'].includes(kind)"><label><span>工件流水号</span><input v-model.trim="draft.serial_number" maxlength="255" placeholder="完整流水号精确匹配" /></label><label><span>状态代码（待确认）</span><input v-model="draft.state" type="number" step="1" placeholder="全部" /></label></template>
        <label v-if="kind !== 'dashboard'"><span>排序</span><AppSelect v-model="draft.sort_by"><option value="time">时间／设备</option><option value="length">长度</option><option value="id">主键／设备</option></AppSelect></label>
        <label v-if="kind !== 'dashboard'" class="welding-check"><input v-model="draft.descending" type="checkbox" />降序</label>
        <label v-if="kind === 'jobs'" class="welding-check"><input v-model="draft.anomalies_only" type="checkbox" />仅异常候选</label>
        <button class="primary" type="submit" :disabled="loading">{{ loading ? '查询中…' : '查询 / 刷新' }}</button>
      </form>
    </section>
    <ReportFeedback v-if="!sourceEnabled" title="焊接数据库尚未启用" message="请先保存数据库连接参数并启用小组立焊接线。" action-label="配置数据库" @action="router.push('/admin/settings/database')" />
    <ReportFeedback v-else-if="error" type="error" title="焊接数据读取失败" :message="`${error}${report ? '；当前保留上次成功查询结果。' : ''}`" action-label="重试" @action="submit(true)" />
    <ReportPanelState v-else-if="loading" type="loading" title="正在读取焊接数据" message="正在查询，繁忙时由服务器短暂排队。" />
    <ReportFeedback v-if="actionError" type="error" title="操作未完成" :message="actionError" dismissible @dismiss="actionError = ''" />
    <ReportFeedback v-if="actionNotice" title="操作完成" :message="actionNotice" dismissible @dismiss="actionNotice = ''" />
    <div v-if="sourceEnabled" class="welding-kpis"><NCard v-for="card in cards" :key="card.key" :bordered="false"><HomeKpiCard :title="card.title" :value="card.value" :unit="card.unit" :precision="card.unit === 'm' || card.unit === 'h' || card.unit === 'm/h' ? 2 : 0" :loading="loading" :status="card.pending ? '口径待核实' : ''" :series="report?.daily?.map(row => row[card.key as keyof WeldingMetrics] ?? 0) || []" /></NCard></div>
    <template v-if="report && activeQuery">
      <div class="welding-freshness"><strong>源库最新更新时间</strong><span>{{ sourceUpdated }}</span><span>本次计算：{{ report.meta.calculated_at }}</span><template v-if="report.meta.report_data"><span>统计来源：{{ { snapshot: '持久化快照', cache: '内存缓存', database: '源库查询' }[report.meta.report_data.source] }}</span><span>统计截止：{{ report.meta.report_data.data_as_of }}</span><strong v-if="report.meta.report_data.stale">统计已过期</strong><span v-if="report.meta.report_data.refreshing">后台刷新排队中</span><span v-if="report.meta.report_data.refresh_failed">刷新失败，保留上次统计</span></template></div>
      <ReportFeedback v-if="!report.summary.job_count" title="当前区间暂无可归属的作业记录" message="可调整日期或设备后重新查询；无效时间候选记录见数据核查说明。" />
      <WeldingCharts v-else :kind="kind" :report="report" />
      <section class="panel welding-detail-table"><header><h2>{{ kind === 'dashboard' ? '最近7天最新焊接动态' : title }}</h2><span>当前查询：{{ activeQuery.start }} 至 {{ activeQuery.end }}；服务器分页</span></header><WeldingTable :paginate="kind !== 'dashboard'" :kind="reportKind" :items="report.items" :total="report.total" :page="activeQuery.page" :page-size="kind === 'dashboard' ? 10 : activeQuery.page_size" :loading="loading" @page="changePage" @page-size="changePageSize" @detail="openDetail" @copy="copy" /></section>
      <section v-if="kind === 'jobs' && report.speed_distribution?.length" class="panel welding-quality"><h2>源表速度分布（mm/min）</h2><dl><template v-for="band in report.speed_distribution" :key="band.speed_band"><dt>{{ band.speed_band }}</dt><dd>{{ band.job_count }} 次</dd></template></dl><p>只统计可归属生产日的作业；不将字段均值作为整线速度。</p></section>
      <section class="panel welding-quality"><h2>统计口径与数据核查</h2><p v-for="notice in report.meta.notices" :key="notice">{{ notice }}</p><dl><template v-for="(label, key) in qualityLabels" :key="key"><dt>{{ label }}</dt><dd>{{ report.meta.quality[key] }}</dd></template><dt>未关联工件的有效流水号</dt><dd>{{ report.meta.unlinked_part_count }}</dd></dl><p v-if="report.projects_truncated">项目图展示工件数最多的前100个项目，可按项目筛选查看。</p></section>
      <section class="panel welding-analysis"><header><h2>AI 生产分析</h2><button type="button" :disabled="analyzing || loading || !report.summary.job_count" @click="runAnalysis">{{ analyzing ? '分析中…' : '生成当前区间分析' }}</button></header><p>基于已核验的汇总分析产出趋势、工艺结构和数据缺口。模型不可用时仍可查询和导出报表。</p><div v-if="analysis" class="welding-analysis-grid"><article v-for="[key, label] in analysisSections" :key="key"><h3>{{ label }}</h3><p>{{ analysis[key] }}</p></article></div></section>
    </template>
    <NModal v-model:show="detailOpen" preset="card" :title="detailJob ? '焊接作业详情' : '工件与焊缝详情'" class="welding-modal" :style="{ width: 'min(1200px, 94vw)' }">
      <ReportFeedback v-if="detailError" type="error" title="详情读取失败" :message="detailError" />
      <ReportPanelState v-if="detailLoading" type="loading" title="正在读取详情" message="正在查询，繁忙时由服务器短暂排队。" />
      <template v-if="detailJob"><dl class="welding-record"><template v-for="[key, label] in weldingColumns.jobs" :key="key"><dt>{{ label }}</dt><dd>{{ weldingDisplay((detailJob as DataRow)[key], key) }}</dd></template></dl><button type="button" :disabled="!detailJob.serial_number" @click="goToPart">查询该流水号的工件</button></template>
      <template v-if="details"><dl class="welding-record"><template v-for="[key, label] in weldingColumns.parts" :key="key"><dt>{{ label }}</dt><dd>{{ weldingDisplay((details.part as DataRow)[key], key) }}</dd></template></dl><ReportFeedback :type="details.association_status === 'pending_verification' ? 'warning' : 'info'" title="焊缝关联说明" :message="details.notice" /><button type="button" :disabled="exporting || details.association_status === 'pending_verification'" @click="exportSeams">导出该工件全部焊缝 CSV</button><WeldingTable kind="seams" :items="details.items" :total="details.total" :page="seamPage" :loading="detailLoading" @page="openDetail(details.part, $event)" /></template>
    </NModal>
  </div>
</template>

<style scoped>
.welding-page{display:flex;flex-direction:column;gap:16px;min-width:0}.welding-heading{display:flex;justify-content:space-between;align-items:center;gap:16px}.welding-heading h1{margin:0;font-size:24px;font-weight:600;color:var(--text-color-1)}.welding-heading p{margin:6px 0 0;color:var(--text-color-3);font-size:13px}.welding-heading :deep(.report-export-menu){width:190px;flex-shrink:0}.welding-filter-panel{padding:16px}.welding-filters{display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end}.welding-filters>label{display:flex;flex-direction:column;gap:5px;min-width:140px;flex:1 1 145px}.welding-filters label>span{font-size:12px;color:var(--text-color-3)}.welding-filters input:not([type=checkbox]){width:100%;min-height:36px;padding:7px 10px;border:1px solid var(--theme-border-color);border-radius:5px;background:rgb(var(--container-bg-color));color:var(--text-color-1)}.welding-filters .welding-check{flex:0 0 auto;min-width:auto;flex-direction:row;align-items:center;min-height:36px}.welding-kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px}.welding-kpis :deep(.n-card__content){padding:0}.welding-freshness{display:flex;gap:8px 16px;flex-wrap:wrap;font-size:12px;color:var(--text-color-3)}.welding-detail-table,.welding-quality,.welding-analysis{padding:16px;min-width:0}.welding-detail-table header,.welding-analysis header{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.welding-page h2{font-size:15px;margin:0 0 12px;color:var(--text-color-1)}.welding-detail-table header span,.welding-quality p,.welding-analysis>p{font-size:12px;color:var(--text-color-3);line-height:1.7}.welding-quality dl{display:grid;grid-template-columns:minmax(170px,1fr) 80px;gap:8px;font-size:12px;max-width:600px}.welding-quality dd{margin:0;text-align:right;font-variant-numeric:tabular-nums}.welding-analysis-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.welding-analysis-grid h3{font-size:14px}.welding-analysis-grid p{white-space:pre-line;overflow-wrap:anywhere;font-size:13px;line-height:1.8}.welding-record{display:grid;grid-template-columns:170px minmax(0,1fr);gap:8px;font-size:13px}.welding-record dt{color:var(--text-color-3)}.welding-record dd{margin:0;overflow-wrap:anywhere}.welding-page button{min-height:36px}button:focus-visible,input:focus-visible{outline:2px solid rgb(var(--primary-color));outline-offset:2px}@media(max-width:1000px){.welding-kpis{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:600px){.welding-heading{align-items:flex-start;flex-direction:column}.welding-kpis,.welding-analysis-grid{grid-template-columns:1fr}.welding-filters>label{flex-basis:100%}.welding-record{grid-template-columns:110px minmax(0,1fr)}}
</style>
