<script setup lang="ts">
import type { PropType } from 'vue'
import type { WeldingAnalysis, WeldingPart, WeldingQuery, WeldingReport, WeldingReportKind, WeldingJobRow, WeldingPartRow } from '../types/welding'
import { toError } from '../utils/errors'
import { selectedLineType } from '../utils/productionLines.ts'
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NModal } from 'naive-ui'
import ReportPeriodPicker from '../components/ReportPeriodPicker.vue'
import AppIcon from '../components/AppIcon.vue'
import WeldingKpiCards from '../components/WeldingKpiCards.vue'
import ReportAnalysis from '../components/ReportAnalysis.vue'
import AppSelect from '../components/AppSelect.vue'
import ReportFeedback from '../components/ReportFeedback.vue'
import ReportPanelState from '../components/ReportPanelState.vue'
import ReportExportMenu from '../components/ReportExportMenu.vue'
import WeldingCharts from '../components/WeldingCharts.vue'
import WeldingTable from '../components/WeldingTable.vue'
import WeldingRecord from '../components/WeldingRecord.vue'
import { analyzeWelding, downloadWelding, getWeldingPart, getWeldingReport, weldingQuery } from '../api/welding.ts'
import { reportToday } from '../api/reportDates.ts'
import { queryDateRange, sameRouteQuery } from '../utils/reportRouteState.ts'
import { parseWeldingRouteQuery, weldingRouteQuery } from '../utils/weldingRouteState.ts'
import { deviceSelection } from '../state/devices.ts'
import { weldingDescriptions, weldingTitles } from '../utils/weldingReport.ts'

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
const sourceEnabled = computed(() => deviceSelection.items?.find(item => item.id === deviceSelection.id)?.enabled !== false)
let controller: AbortController | undefined, detailController: AbortController | undefined, exportController: AbortController | undefined, analysisController: AbortController | undefined
let disposed = false
let appliedKind: WeldingReportKind | undefined
let appliedLine = ''
let generation = 0
let detailGeneration = 0
let pendingRead: { key: string; promise: Promise<void> } | null | undefined
let refreshTimer: ReturnType<typeof setTimeout> | undefined
let refreshPollCount = 0
const reportKind = computed(() => props.kind === 'dashboard' ? 'jobs' : props.kind)
const showAdvanced = ref(draft.anomalies_only || draft.sort_by !== 'time' || !draft.descending || [draft.robot_no, draft.weld_type, draft.state, draft.part_state, draft.serial_number, draft.project_no, draft.section_no, draft.part_no].some(value => value !== ''))
const analysisError = ref('')
const description = computed(() => weldingDescriptions[props.kind])
const title = computed(() => weldingTitles[props.kind])
const sourceLabels: Record<string, string> = { weld_info_table: '作业', weld_part_info_table: '工件', welding_seam_info_table: '焊缝' }
const sourceUpdated = computed(() => Object.entries(report.value?.meta?.data_updated_at || {}).map(([key, value]) => `${sourceLabels[key]}：${value || '未记录'}`).join('；'))
const qualityLabels = { invalid_time_rows: '区间候选记录中结束时间无效', invalid_serial_rows: '流水号无效', invalid_length_rows: '长度为空或非正', invalid_duration_rows: '用时为空或非正', unverified_state_rows: '状态含义待确认' }

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
async function load(values: WeldingQuery, force: boolean, syncRoute: boolean, background = false) {
  if (disposed) return
  const kind = props.kind
  const line = deviceSelection.id
  clearTimeout(refreshTimer)
  if (!background) refreshPollCount = 0
  controller?.abort()
  if (!background) analysisController?.abort()
  const run = ++generation
  controller = new AbortController()
  loading.value = !background
  error.value = ''
  if (!background) { analysis.value = null; analysisError.value = '' }
  if (!sourceEnabled.value) {
    report.value = null
    activeQuery.value = null
    loading.value = false
    return
  }
  try {
    const value = await getWeldingReport(props.kind, values, controller.signal, force)
    if (disposed || run !== generation || line !== deviceSelection.id || kind !== props.kind || selectedLineType(deviceSelection.items, deviceSelection.id) !== 'welding') return
    if (background && value.data_version !== report.value?.data_version) { analysisController?.abort(); analysis.value = null; analysisError.value = '' }
    report.value = value
    activeQuery.value = value.query
    appliedKind = kind
    appliedLine = line
    const query = weldingRouteQuery(route.query, parseWeldingRouteQuery(weldingRouteQuery({}, value.query), kind, today))
    if (!background && !sameRouteQuery(query, route.query)) {
      if (syncRoute) await router.push({ query })
      else await router.replace({ query })
    }
    if (run === generation && value.meta?.report_data?.refreshing && refreshPollCount < 75) {
      refreshPollCount += 1
      refreshTimer = setTimeout(() => { if (!disposed && run === generation) void load(values, false, false, true) }, 5000)
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
  return read({ ...draft, ...range, page: 1, page_size: 10 }, force)
}
function resetFilters() {
  Object.assign(draft, parseWeldingRouteQuery({}, props.kind, today))
  return submit(true)
}
function changePage(page: number) { if (!activeQuery.value) return; return read({ ...activeQuery.value, page }) }
function changePageSize() { if (!activeQuery.value) return; return read({ ...activeQuery.value, page: 1, page_size: 10 }) }
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
  detailLoading.value = false
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
  analysisError.value = ''
  const version = report.value.data_version
  try {
    const result = await analyzeWelding(reportKind.value, activeQuery.value, version, analysisController.signal)
    if (report.value?.data_version === version) analysis.value = result
  } catch (caught) {
    const exception = toError(caught)
    if (exception.name !== 'AbortError') analysisError.value = exception.message }
  finally { analyzing.value = false }
}
function cancel() {
  clearTimeout(refreshTimer)
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
watch(() => [props.kind, sourceEnabled.value, deviceSelection.id, selectedLineType(deviceSelection.items, deviceSelection.id), route.query], restoreRouteState, { immediate: true })
onBeforeUnmount(() => { disposed = true; cancel() })
</script>

<template>
  <div class="welding-page">
    <section class="page-hero report-hero welding-hero">
      <div class="report-hero-copy"><span class="report-hero-eyebrow">WELDING PRODUCTION</span><h1>{{ title }}</h1><p>{{ description }}</p></div>
      <div class="welding-hero-mark" aria-hidden="true"><AppIcon :name="kind === 'parts' ? 'grid' : kind === 'equipment' ? 'factory' : 'activity'" :size="64" tone="current" /></div>
    </section>
    <section v-if="sourceEnabled" class="panel welding-filter-panel" aria-label="焊接报表查询条件">
      <form @submit.prevent="submit(true)">
        <div class="welding-filters">
          <ReportPeriodPicker v-model:start-date="draft.start" v-model:end-date="draft.end" :anchor-date="today" :disabled="loading" @apply="submit(true)" @validation-error="actionError = $event" />
          <label><span>设备</span><AppSelect v-model="draft.device_no" aria-label="设备"><option value="">全部设备</option><option v-for="device in report?.meta?.devices || []" :key="device" :value="device">设备 {{ device }}</option></AppSelect></label>
          <NButton class="welding-query-submit" attr-type="submit" type="primary" :theme-overrides="{ iconSizeMedium: '15px', iconMarginMedium: '0 8px 0 0' }" :loading="loading" :disabled="loading"><template #icon><AppIcon name="search" :size="15" tone="current" /></template>{{ loading ? '查询中' : '查询报表' }}</NButton>
          <ReportExportMenu :formats="['csv', 'html', 'pdf']" :disabled="!report || loading" :loading="exporting" @select="exportReport" />
          <NButton attr-type="button" :aria-expanded="showAdvanced" aria-controls="welding-advanced" @click="showAdvanced = !showAdvanced">{{ showAdvanced ? '收起筛选' : '更多筛选' }}</NButton>
          <NButton attr-type="button" :disabled="loading" @click="resetFilters">重置</NButton>
        </div>
        <div v-show="showAdvanced" id="welding-advanced" class="welding-advanced">
          <label v-if="['jobs', 'equipment'].includes(kind)"><span>机器人编号</span><NInputNumber :value="draft.robot_no === '' ? null : Number(draft.robot_no)" @update:value="draft.robot_no = $event ?? ''" :min="0" :step="1" placeholder="全部" :precision="0" /></label>
          <label v-if="['jobs', 'equipment'].includes(kind)"><span>焊接类型</span><AppSelect v-model="draft.weld_type" aria-label="焊接类型"><option value="">全部工艺</option><option :value="0">平焊包角</option><option :value="1">平焊</option><option :value="2">立焊包角</option><option :value="3">立焊</option></AppSelect></label>
          <template v-if="kind === 'parts'"><label><span>项目编号</span><NInput v-model:value.trim="draft.project_no" :maxlength="255" placeholder="精确匹配" /></label><label><span>分段号</span><NInput v-model:value.trim="draft.section_no" :maxlength="255" /></label><label><span>工件编号</span><NInput v-model:value.trim="draft.part_no" :maxlength="255" /></label></template>
          <template v-if="['parts', 'jobs'].includes(kind)"><label><span>工件流水号</span><NInput v-model:value.trim="draft.serial_number" :maxlength="255" placeholder="完整流水号精确匹配" /></label><label><span>{{ kind === 'parts' ? '工件' : '作业' }}状态代码（待确认）</span><NInputNumber :value="draft[kind === 'parts' ? 'part_state' : 'state'] === '' ? null : Number(draft[kind === 'parts' ? 'part_state' : 'state'])" @update:value="draft[kind === 'parts' ? 'part_state' : 'state'] = $event ?? ''" :step="1" placeholder="全部" :precision="0" /></label></template>
          <label><span>排序方式</span><AppSelect v-model="draft.sort_by" aria-label="排序方式"><option value="time">时间／设备</option><option value="length">长度</option><option value="id">主键／设备</option></AppSelect></label>
          <NCheckbox v-model:checked="draft.descending" class="welding-check">降序</NCheckbox>
          <NCheckbox v-if="kind === 'jobs'" v-model:checked="draft.anomalies_only" class="welding-check">仅异常候选</NCheckbox>
        </div>
      </form>
      <p class="welding-query-note">{{ activeQuery ? `当前结果：${activeQuery.start} 至 ${activeQuery.end}` : '按作业结束时间统计，北京时间' }} · 筛选修改后点击查询，导出与 AI 使用已查询条件。</p>
    </section>
    <ReportFeedback v-if="!sourceEnabled" title="焊接数据库尚未启用" message="请先保存数据库连接参数并启用小组立焊接线。" action-label="配置数据库" @action="router.push('/admin/settings/database')" />
    <ReportFeedback v-else-if="error" type="error" title="焊接数据读取失败" :message="`${error}${report ? '；当前保留上次成功查询结果。' : ''}`" action-label="重试" @action="submit(true)" />
    <ReportPanelState v-else-if="loading" type="loading" title="正在读取焊接数据" message="首次统计可能需要较长时间，准备完成后会自动显示，请勿重复刷新。" />
    <ReportFeedback v-if="actionError" type="error" title="操作未完成" :message="actionError" dismissible @dismiss="actionError = ''" />
    <ReportFeedback v-if="actionNotice" title="操作完成" :message="actionNotice" dismissible @dismiss="actionNotice = ''" />
    <WeldingKpiCards v-if="sourceEnabled" :kind="kind" :report="report" :loading="loading" />
    <template v-if="report && activeQuery">
      <div class="welding-freshness" role="status"><span>数据更新：{{ report.meta.report_data?.data_as_of || report.meta.calculated_at }}</span><span v-if="loading">正在更新，当前展示上次结果</span><template v-if="report.meta.report_data"><span v-if="report.meta.report_data.refreshing">后台刷新排队中</span><span v-if="report.meta.report_data.refresh_failed">刷新失败，保留上次统计</span><span v-else-if="report.meta.report_data.stale">统计已过期，等待更新</span></template></div>
      <ReportFeedback v-if="!report.summary.job_count" title="当前区间暂无可归属的作业记录" message="可调整日期或设备后重新查询；无效时间候选记录见数据核查说明。" />
      <WeldingCharts v-else :kind="kind" :report="report" />
      <section class="panel welding-detail-table"><header><h2>{{ kind === 'parts' ? '工件生产明细' : kind === 'jobs' ? '焊接作业明细' : kind === 'equipment' ? '机器人工位明细' : '每日生产明细' }}</h2><span>{{ report.total }} 条记录 · 分页与排序使用已查询条件</span></header><WeldingTable :kind="reportKind" :items="report.items" :total="report.total" :page="activeQuery.page" :page-size="activeQuery.page_size" :loading="loading" @page="changePage" @page-size="changePageSize" @detail="openDetail" @copy="copy" /></section>
      <details v-if="kind === 'jobs' && report.speed_distribution?.length" class="panel welding-quality"><summary>源表速度分布（mm/min）</summary><dl><template v-for="band in report.speed_distribution" :key="band.speed_band"><dt>{{ band.speed_band }}</dt><dd>{{ band.job_count }} 次</dd></template></dl><p>只统计可归属生产日的作业；不将字段均值作为整线速度。</p></details>
      <ReportAnalysis :analysis="analysis" :loading="analyzing" :error="analysisError" :ready="!loading && Boolean(report.summary.job_count)" :action-label="analysis ? '重新生成分析' : '生成当前区间分析'" empty-message="点击“生成当前区间分析”，查看产出趋势、工艺结构和数据核查建议。" @reload="runAnalysis" @retry="runAnalysis" />
      <details class="panel welding-quality"><summary>统计口径与数据核查</summary><p v-for="notice in report.meta.notices" :key="notice">{{ notice }}</p><dl><template v-for="(label, key) in qualityLabels" :key="key"><dt>{{ label }}</dt><dd>{{ report.meta.quality[key] ?? '未提供' }}</dd></template><dt>未关联工件的有效流水号</dt><dd>{{ report.meta.unlinked_part_count ?? '未提供' }}</dd><dt>待核查关联流水号</dt><dd>{{ report.meta.ambiguous_part_count ?? '未提供' }}</dd></dl><p v-if="report.projects_truncated">项目图展示工件数最多的前100个项目，可按项目筛选查看。</p><p>源库最新更新时间：{{ sourceUpdated }}</p><p v-if="report.meta.source_info_as_of">源信息采样：{{ report.meta.source_info_as_of }}</p><p>本次计算：{{ report.meta.calculated_at }}<template v-if="report.meta.report_data">；统计截止：{{ report.meta.report_data.data_as_of }}</template></p></details>
    </template>
    <NModal v-model:show="detailOpen" preset="card" :title="detailJob ? '焊接作业详情' : '工件与焊缝详情'" class="welding-modal" :style="{ width: 'min(1200px, 94vw)' }">
      <ReportFeedback v-if="detailError" type="error" title="详情读取失败" :message="detailError" />
      <ReportPanelState v-if="detailLoading" type="loading" title="正在读取详情" message="正在查询，繁忙时由服务器短暂排队。" />
      <template v-if="detailJob"><WeldingRecord kind="jobs" :row="detailJob" /><NButton attr-type="button" :disabled="!detailJob.serial_number" @click="goToPart">查询该流水号的工件</NButton></template>
      <template v-if="details"><WeldingRecord kind="parts" :row="details.part" /><h3>关联焊缝记录</h3><ReportFeedback :type="details.association_status === 'pending_verification' ? 'warning' : 'info'" title="焊缝关联说明" :message="details.notice" /><NButton attr-type="button" :disabled="exporting || details.association_status === 'pending_verification'" @click="exportSeams">导出该工件全部焊缝 CSV</NButton><WeldingTable kind="seams" :items="details.items" :total="details.total" :page="seamPage" :loading="detailLoading" @page="openDetail(details.part, $event)" /></template>
    </NModal>
  </div>
</template>

<style scoped>
.welding-page{display:flex;flex-direction:column;gap:16px;min-width:0}.welding-page>.panel{margin-bottom:0}.welding-hero{padding:22px 24px;min-height:140px;border-radius:11px;background:rgb(var(--container-bg-color));border:1px solid var(--theme-border-color)}.welding-hero h1{font-size:24px;margin:8px 0}.welding-hero p{font-size:13px}.welding-hero-mark{color:rgb(var(--primary-color));padding:12px 24px;background:rgb(var(--primary-color) / .06);border-radius:16px}.welding-filter-panel{padding:16px}.welding-filters{display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end}.welding-filters>.report-period-picker{flex:1 1 250px}.welding-filters>label{flex:0 1 160px;min-width:140px}.welding-filters>.report-export-menu{width:138px}.welding-filters>.n-button{height:40px}.welding-filters>.welding-query-submit{padding:0 16px;gap:0;justify-content:center;flex:0 0 112px;width:112px;min-width:112px;height:40px;min-height:40px;max-height:40px}.welding-filters :deep(.report-period-picker__trigger){height:40px;min-height:40px}.welding-filters :deep(.n-base-selection){--n-height:40px!important}.welding-filters label,.welding-advanced label{display:flex;flex-direction:column;gap:8px}.welding-filters label>span,.welding-advanced label>span{font-size:12px;color:var(--text-color-2)}.welding-advanced{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;align-items:end;margin-top:16px;padding-top:16px;border-top:1px solid var(--theme-border-color)}.welding-check{min-height:36px}.welding-query-note{margin:12px 0 0;color:var(--text-color-2);font-size:12px}.welding-freshness{display:flex;gap:8px 16px;flex-wrap:wrap;font-size:12px;color:var(--text-color-2)}.welding-detail-table,.welding-quality{padding:16px;min-width:0}.welding-detail-table header{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.welding-page h2{display:flex;align-items:center;gap:8px;font-size:16px;margin:0;color:var(--text-color-1)}.welding-detail-table header{margin-bottom:16px}.welding-detail-table header span,.welding-quality p{font-size:12px;color:var(--text-color-2);line-height:1.8}.welding-quality summary{font-weight:600;cursor:pointer}.welding-quality dl{display:grid;grid-template-columns:minmax(170px,1fr) 80px;gap:8px;font-size:12px;max-width:600px}.welding-quality dd{margin:0;text-align:right;font-variant-numeric:tabular-nums}@media(max-width:600px){.welding-hero{padding:18px 16px}.welding-hero-mark{display:none}.welding-filters>label,.welding-filters>.report-period-picker{flex-basis:100%;flex-grow:1}.welding-filters>.n-button,.welding-filters>.report-export-menu{flex:1 1 calc(50% - 12px);width:auto}}
</style>
