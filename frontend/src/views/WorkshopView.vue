<script setup lang="ts">
import { NTag } from 'naive-ui'
import AppReportTable from '../components/AppReportTable.vue'
import WorkshopCorrectionDialog from '../components/workshop/WorkshopCorrectionDialog.vue'
import WorkshopBatchConfirmationDialog from '../components/workshop/WorkshopBatchConfirmationDialog.vue'
import { useWorkshopConfirmation } from '../composables/useWorkshopConfirmation'
import { useWorkshopCorrection } from '../composables/useWorkshopCorrection'
import type { ReportCreateRequest } from '../types/reports'

import type { PropType } from 'vue'
import type { ExportFormat } from '../types/api'
import type { User } from '../types/auth'
import type { WorkshopResult, WorkshopTask } from '../types/reports'

import { toError } from '../utils/errors'
import { computed, nextTick, onActivated, onDeactivated, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { generateWorkshopDailyReport, getLatestWorkshopProductionDate, listWorkshopRangeTasks, listWorkshopTasks } from '../api/cutting.ts'
import AnalysisPanel from '../components/AnalysisPanel.vue'
import AppIcon from '../components/AppIcon.vue'
import MetricSparkline from '../components/MetricSparkline.vue'
import ReportFeedback from '../components/ReportFeedback.vue'
import ReportTrendPanel from '../components/ReportTrendPanel.vue'
import StandardReportQuery from '../components/StandardReportQuery.vue'
import WorkshopStructureCharts from '../components/WorkshopStructureCharts.vue'
import { reportToday, taskProductionDate } from '../api/reportDates.ts'
import { formatChineseDate, formatChineseDateTime } from '../utils/dateDisplay.ts'
import { getDeviceId } from '../api/deviceContext.ts'
import { useDeviceSwitchQuery } from '../composables/useDeviceSwitchQuery.ts'
import { mergeOwnedQuery, queryChoice, queryDateRange, queryPositiveInteger, queryText, sameRouteQuery } from '../utils/reportRouteState.ts'

const props = defineProps({
  exporting: Boolean,
  user: { type: Object as PropType<User>, required: true },
  mode: { type: String, default: 'manager', validator: (value: unknown) => ['manager', 'worker'].includes(String(value)) },
})
const emit = defineEmits<{ export: [payload: ReportCreateRequest, format: ExportFormat]; refreshed: [] }>()
const route = useRoute()
const router = useRouter()
const today = reportToday()
const pageSize = 10
const filters = reactive({ reportDate: today, startDate: today, endDate: today, projectName: '', taskId: '', confirmationStatus: 'all' })
const appliedRouteState = reactive({ startDate: today, endDate: today, projectName: '', taskId: '', confirmationStatus: 'all', page: 1, defaultView: true })
const result = ref<WorkshopResult>({ analysis_source_version: '', report: null, items: [], total: 0, page: 1, page_size: pageSize, project_names: [], confirmed_count: 0, worker_summaries: [], duration_distribution: [], status_distribution: [], top_cut_length_tasks: [] })
const loading = ref(false)
const error = ref('')
const pageActive = ref(true)
const defaultReportView = ref(true)
const chartType = ref(props.mode === 'manager' ? queryChoice(route.query.chart, ['bar_line', 'bar', 'line', 'area'], 'bar_line') : 'bar_line')
const isWorker = computed(() => props.mode === 'worker')
const dateQueryDirty = computed(() => !isWorker.value && (
  filters.startDate !== appliedRouteState.startDate || filters.endDate !== appliedRouteState.endDate
))
const {
  selectableTasks, selectedTasks, batchConfirmation, confirmationFeedback, batchDialogOpen, selectedTaskCount,
  allVisibleTasksSelected, someVisibleTasksSelected, clearSelectedTasks, setTaskSelected, toggleVisibleTasks,
  reconcileVisibleTaskSelection, openBatchConfirmation, openSingleConfirmation, closeBatchConfirmation, showConfirmationFeedback,
  closeConfirmationFeedback, submitBatchConfirmation, pauseFeedback,
} = useWorkshopConfirmation({ isWorker, user: () => props.user, result, filters, error, load })
const {
  correction, correctionDialogOpen, visibleCorrectionReasonPresets, availableWorkers, isOwnConfirmation,
  openCorrection, closeCorrection, selectCorrectionReason, submitCorrection,
} = useWorkshopCorrection({
  isWorker, user: () => props.user, filters, rowDate, showConfirmationFeedback,
  refresh: () => load(result.value.page, { silent: true, ensureReport: false, useAppliedFilters: !isWorker.value }),
})

const analysisReady = computed(() => Boolean(
  pageActive.value
  && !isWorker.value
  && result.value.report
  && result.value.report.period_start === appliedRouteState.startDate
  && result.value.report.period_end === appliedRouteState.endDate
  && !loading.value
))
const workshopMetrics = computed(() => {
  const taskCount = Math.max(0, Number(result.value.report?.item_count || 0))
  const confirmedCount = Math.max(0, Number(result.value.report?.confirmed_count || 0))
  return {
    taskCount,
    confirmedCount,
    pendingCount: Math.max(0, taskCount - confirmedCount),
    projectCount: result.value.project_names?.length || 0,
    confirmationRate: taskCount ? Math.round((confirmedCount / taskCount) * 1000) / 10 : 0,
  }
})
const workshopMetricSeries = computed(() => {
  const rows = result.value.trend || []
  return {
    tasks: rows.map((row) => row.task_count),
    pending: rows.map((row) => row.pending_count),
    confirmed: rows.map((row) => row.confirmed_count),
    projects: rows.map((row) => row.project_count),
    confirmationRate: rows.map((row) => row.confirmation_rate),
  }
})
let refreshTimer: number | null = null
let requestController: AbortController | null = null
let requestSequence = 0
let routeStateReady = false
let restoringRouteState = false

const routeQueryKeys = ['start', 'end', 'project', 'confirmation', 'page', 'chart', 'task', 'task_line']

function routeStateQuery() {
  return mergeOwnedQuery(route.query, routeQueryKeys, {
    start: appliedRouteState.defaultView ? '' : appliedRouteState.startDate,
    end: appliedRouteState.defaultView ? '' : appliedRouteState.endDate,
    project: appliedRouteState.projectName,
    task: appliedRouteState.taskId,
    task_line: appliedRouteState.taskId ? getDeviceId() : '',
    confirmation: appliedRouteState.confirmationStatus === 'all' ? '' : appliedRouteState.confirmationStatus,
    page: appliedRouteState.page === 1 ? '' : appliedRouteState.page,
    chart: chartType.value === 'bar_line' ? '' : chartType.value,
  })
}

useDeviceSwitchQuery(isWorker.value ? 'worker-tasks' : 'workshop-report', () => ({
  ...routeStateQuery(),
  task: '',
  task_line: '',
  start: isWorker.value ? filters.reportDate : appliedRouteState.startDate,
  end: isWorker.value ? filters.reportDate : appliedRouteState.endDate,
}))

async function syncRouteState() {
  if (isWorker.value || route.name !== 'workshop-report') return
  const query = routeStateQuery()
  if (sameRouteQuery(query, route.query)) return
  await router.push({ query })
}

function beginRequest() {
  requestController?.abort()
  requestController = new AbortController()
  return { sequence: ++requestSequence, signal: requestController.signal }
}

function stopRefreshTimer() {
  if (!refreshTimer) return
  window.clearTimeout(refreshTimer)
  refreshTimer = null
}

function startRefreshTimer() {
  if (!pageActive.value || !isWorker.value || refreshTimer) return
  refreshTimer = window.setTimeout(async () => {
    refreshTimer = null
    if (
      pageActive.value
      && document.visibilityState === 'visible'
      && result.value.report
      && !loading.value
      && !batchConfirmation.visible
      && !batchConfirmation.saving
      && !correction.visible
    ) {
      await load(result.value.page, { silent: true, ensureReport: false, force: true })
    }
    startRefreshTimer()
  }, 5000)
}

watch(() => [filters.reportDate, filters.startDate, filters.endDate], () => {
  if (restoringRouteState) return
  filters.projectName = ''
  filters.confirmationStatus = 'all'
  clearSelectedTasks()
  batchConfirmation.visible = false
  if (isWorker.value) clearReport()
})
function clearReport() {
  result.value = { analysis_source_version: '', report: null, items: [], total: 0, page: 1, page_size: pageSize, project_names: [], confirmed_count: 0, worker_summaries: [], duration_distribution: [], status_distribution: [], top_cut_length_tasks: [] }
}
watch(() => [filters.projectName, filters.confirmationStatus], () => {
  if (restoringRouteState) return
  clearSelectedTasks()
  batchConfirmation.visible = false
})

function display(value: string | null | undefined) { return value || '--' }
function number(value: number | null | undefined) { return value == null ? '--' : new Intl.NumberFormat('zh-CN', { minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(value) }
function duration(value: unknown) {
  if (value == null) return '--'
  const seconds = Math.max(0, Math.round(Number(value)))
  return [Math.floor(seconds / 3600), Math.floor(seconds % 3600 / 60), seconds % 60].map((item) => String(item).padStart(2, '0')).join(':')
}
function time(value: unknown) { return formatChineseDateTime(value) }
function rowDate(row: WorkshopTask) { return taskProductionDate(row, filters.reportDate) }

async function load(page = 1, { silent = false, ensureReport = true, force = false, syncRoute = false, useAppliedFilters = false } = {}) {
  if (!isWorker.value && filters.endDate < filters.startDate) {
    error.value = '结束日期不能早于开始日期'
    return
  }
  const { sequence, signal } = beginRequest()
  if (!silent) {
    loading.value = true
    error.value = ''
  }
  try {
    const query = {
      ...filters,
      ...(!isWorker.value && useAppliedFilters ? {
        startDate: appliedRouteState.startDate, endDate: appliedRouteState.endDate,
        taskId: appliedRouteState.taskId,
        projectName: appliedRouteState.projectName, confirmationStatus: appliedRouteState.confirmationStatus,
      } : {}),
      sortMode: props.mode,
      page,
      pageSize: result.value.page_size,
    }
    let data = isWorker.value
      ? await listWorkshopTasks(query, { force, signal })
      : await listWorkshopRangeTasks(query, { force, signal })
    if (isWorker.value && ensureReport) {
      await generateWorkshopDailyReport(query.reportDate, { signal })
      if (!pageActive.value || signal.aborted || sequence !== requestSequence) return
      data = await listWorkshopTasks(query, { force: true, signal })
    }
    const current = isWorker.value
      ? query.reportDate === filters.reportDate
      : useAppliedFilters || (query.startDate === filters.startDate && query.endDate === filters.endDate)
    if (pageActive.value && !signal.aborted && sequence === requestSequence && current) {
      result.value = data
      if (!isWorker.value) {
        Object.assign(appliedRouteState, {
          startDate: query.startDate,
          endDate: query.endDate,
          taskId: query.taskId,
          projectName: query.projectName,
          confirmationStatus: query.confirmationStatus,
          page: data.page,
          defaultView: useAppliedFilters ? appliedRouteState.defaultView : defaultReportView.value,
        })
      }
      reconcileVisibleTaskSelection(data.items || [])
      if (syncRoute) await syncRouteState()
    }
  } catch (caught) {
    const exception = toError(caught)
    if (exception.name !== 'AbortError' && sequence === requestSequence && !silent) error.value = exception.message
  } finally {
    if (sequence === requestSequence) {
      if (!silent) loading.value = false
      requestController = null
      if (isWorker.value && pageActive.value && !signal.aborted) emit('refreshed')
    }
  }
}

function refreshAnalysisReport() {
  if (!pageActive.value || isWorker.value || loading.value) return
  return load(result.value.page, { ensureReport: false, force: true, useAppliedFilters: true })
}

function applyTaskFilters() {
  filters.taskId = ''
  defaultReportView.value = false
  clearSelectedTasks()
  load(1, { force: true, syncRoute: !isWorker.value })
}

function changePage(page: number) {
  return load(page, { syncRoute: !isWorker.value, useAppliedFilters: !isWorker.value })
}

function exportReport(format: ExportFormat = 'html') {
  emit('export', {
    report_type: 'workshop',
    report_date: isWorker.value ? filters.reportDate : appliedRouteState.startDate,
    period_start: isWorker.value ? filters.reportDate : appliedRouteState.startDate,
    period_end: isWorker.value ? filters.reportDate : appliedRouteState.endDate,
    project_code: isWorker.value ? null : appliedRouteState.projectName || null,
    ship_no: null,
    parameters: { chart_type: chartType.value },
  }, format)
}

function requestedRouteState() {
  const range = queryDateRange(route.query)
  return {
    range,
    chartType: queryChoice(route.query.chart, ['bar_line', 'bar', 'line', 'area'], 'bar_line'),
    taskId: range && queryText(route.query.task_line) === getDeviceId() ? queryText(route.query.task).trim() : '',
    projectName: range ? queryText(route.query.project).trim() : '',
    confirmationStatus: range ? queryChoice(route.query.confirmation, ['all', 'unconfirmed', 'confirmed'], 'all') : 'all',
    page: range ? queryPositiveInteger(route.query.page) : 1,
  }
}

async function applyRequestedRouteState(state: ReturnType<typeof requestedRouteState>) {
  restoringRouteState = true
  chartType.value = state.chartType
  if (state.range) {
    if (isWorker.value) filters.reportDate = state.range.start
    filters.startDate = state.range.start
    filters.endDate = state.range.end
    filters.taskId = state.taskId
    filters.projectName = state.projectName
    filters.confirmationStatus = state.confirmationStatus
  }
  await nextTick()
  restoringRouteState = false
}

async function initializeReport({ restoreQuery = false } = {}) {
  const requested = restoreQuery ? requestedRouteState() : null
  const { sequence, signal } = beginRequest()
  loading.value = true
  error.value = ''
  try {
    const selectedDate = reportToday()
    const reportDate = isWorker.value
      ? (await getLatestWorkshopProductionDate(selectedDate, { signal })).report_date
      : selectedDate
    if (!pageActive.value || signal.aborted || sequence !== requestSequence) return
    if (reportDate) {
      if (!isWorker.value && (filters.startDate !== reportDate || filters.endDate !== reportDate)) clearReport()
      filters.reportDate = reportDate
      filters.startDate = reportDate
      filters.endDate = reportDate
    }
    if (!isWorker.value) {
      filters.taskId = ''
      filters.projectName = ''
      filters.confirmationStatus = 'all'
    }
    defaultReportView.value = true
  } catch (caught) {
    const exception = toError(caught)
    if (exception.name !== 'AbortError' && sequence === requestSequence) error.value = exception.message
  }
  if (!pageActive.value || signal.aborted || sequence !== requestSequence) return
  if (requested?.range) {
    await applyRequestedRouteState(requested)
    defaultReportView.value = false
  }
  await load(requested?.page || 1, { force: false })
}

async function restoreRouteState() {
  if (isWorker.value || route.name !== 'workshop-report') return false
  if (result.value.report && sameRouteQuery(route.query, routeStateQuery())) return false
  const requested = requestedRouteState()
  await applyRequestedRouteState(requested)

  if (!requested.range) {
    if (!result.value.report || !defaultReportView.value) {
      await initializeReport()
      return true
    }
    return false
  }

  defaultReportView.value = false
  if (
    !result.value.report
    || appliedRouteState.startDate !== requested.range.start
    || appliedRouteState.endDate !== requested.range.end
    || appliedRouteState.taskId !== requested.taskId
    || appliedRouteState.projectName !== requested.projectName
    || appliedRouteState.confirmationStatus !== requested.confirmationStatus
    || appliedRouteState.page !== requested.page
  ) {
    await load(requested.page, { force: false })
    return true
  }
  return false
}

function pausePage() {
  if (!pageActive.value) return
  pageActive.value = false
  requestSequence += 1
  requestController?.abort()
  requestController = null
  loading.value = false
  stopRefreshTimer()
  pauseFeedback()
}

onMounted(async () => {
  await initializeReport({ restoreQuery: true })
  routeStateReady = true
  startRefreshTimer()
})
onActivated(async () => {
  pageActive.value = true
  startRefreshTimer()
  if (routeStateReady && await restoreRouteState()) return
  if (!result.value.report && !loading.value) {
    await initializeReport({ restoreQuery: true })
    return
  }
  if (isWorker.value && result.value.report && !loading.value && !batchConfirmation.visible && !batchConfirmation.saving) {
    load(result.value.page, { silent: true, ensureReport: false, force: true })
  } else if (!isWorker.value && routeStateReady && result.value.report && !loading.value) {
    if (defaultReportView.value && !dateQueryDirty.value
        && filters.projectName === appliedRouteState.projectName
        && filters.confirmationStatus === appliedRouteState.confirmationStatus) initializeReport()
    else load(result.value.page, { ensureReport: false, useAppliedFilters: true })
  }
})
onDeactivated(pausePage)
onUnmounted(() => {
  pausePage()
})
watch(chartType, async () => {
  if (routeStateReady && !restoringRouteState && !isWorker.value) await syncRouteState()
})
watch(() => route.query, async () => {
  if (routeStateReady && pageActive.value && !isWorker.value && route.name === 'workshop-report') await restoreRouteState()
}, { deep: true })

const taskTableColumns = computed(() => [
  ...(isWorker.value ? [{ key: 'c0', title: '', width: 48, align: 'center' as const, fixed: 'left' as const, ellipsis: false }] : []),
  { key: 'c1', title: "序号", width: 56, align: 'center' },
  { key: 'c2', title: "工程号", width: 76, align: 'left' },
  { key: 'c3', title: "船名", width: 160, align: 'left' },
  { key: 'c4', title: "分段名", width: 76, align: 'left' },
  { key: 'c5', title: "程序号", width: 160, align: 'left' },
  { key: 'c6', title: "切割长度", width: 104, align: 'right' },
  { key: 'c7', title: "坡口长度", width: 104, align: 'right' },
  { key: 'c8', title: "切割时长", width: 104, align: 'center' },
  { key: 'c9', title: "时间", width: 184, align: 'left' },
  { key: 'c10', title: "确认状态", width: result.value.items.some(row => row.confirmed_by) ? 400 : 116, align: 'center', fixed: 'right', ellipsis: false },
] as const)

const workerTableColumns = [
  { key: 'c0', title: "序号", width: 56, align: 'center', fixed: 'left' },
  { key: 'c1', title: "工号", width: 90, align: 'left' },
  { key: 'c2', title: "姓名", width: 96, align: 'left' },
  { key: 'c3', title: "已确认任务", width: 104, align: 'right' },
  { key: 'c4', title: "切割长度", width: 112, align: 'right' },
  { key: 'c5', title: "切割时长", width: 104, align: 'center' },
  { key: 'c6', title: "工时占比", width: 96, align: 'right' },
  { key: 'c7', title: "最后确认时间", width: 184, align: 'left' }
] as const
</script>

<template>
  <Transition name="action-toast">
    <aside v-if="confirmationFeedback.visible" class="confirmation-feedback" :class="confirmationFeedback.type" role="status" aria-live="polite">
      <span v-if="confirmationFeedback.type === 'pending'" class="spinner dark" aria-hidden="true"></span>
      <span v-else class="confirmation-feedback-icon" aria-hidden="true">{{ confirmationFeedback.type === 'success' ? '✓' : '!' }}</span>
      <div><strong>{{ confirmationFeedback.title }}</strong><p>{{ confirmationFeedback.message }}</p></div>
      <NButton attr-type="button" aria-label="关闭确认提示" @click="closeConfirmationFeedback">×</NButton>
    </aside>
  </Transition>

    <WorkshopBatchConfirmationDialog :batch-confirmation="batchConfirmation" :user="user" @close="closeBatchConfirmation" @submit="submitBatchConfirmation" />

    <WorkshopCorrectionDialog :correction="correction" v-model:action="correction.action" v-model:worker-id="correction.workerId" v-model:reason="correction.reason" :is-worker="isWorker" :available-workers="availableWorkers" :visible-correction-reason-presets="visibleCorrectionReasonPresets" @close="closeCorrection" @submit="submitCorrection" @reason="selectCorrectionReason" />

  <div class="workshop-dashboard" :class="isWorker ? 'workshop-dashboard--worker' : 'workshop-dashboard--manager'">
    <section class="workshop-query-panel" :class="{ 'page-hero report-hero report-hero--query': !isWorker }">
      <header class="workshop-heading" :class="{ 'report-hero-copy': !isWorker }">
        <span v-if="!isWorker" class="report-hero-eyebrow">WORKSHOP PRODUCTION</span>
        <h2>{{ isWorker ? '我的任务确认' : '车间报表' }}</h2>
        <span v-if="isWorker">{{ user.display_name }}</span>
      </header>
      <StandardReportQuery
        v-if="!isWorker"
        v-model:start-date="filters.startDate"
        v-model:end-date="filters.endDate"
        v-model:chart-type="chartType"
        class="workshop-query-shell report-hero-query"
        aria-label="车间报表查询条件"
        :anchor-date="filters.reportDate"
        :loading="loading"
        :show-loading="!result.report"
        :has-data="Boolean(result.report)"
        :date-dirty="dateQueryDirty"
        :export-disabled="loading || !result.report"
        :exporting="exporting"
        @submit="applyTaskFilters"
        @export="exportReport"
        @validation-error="error = $event"
      />
    </section>

    <ReportFeedback v-if="!isWorker && loading && result.report" type="info" title="车间报表正在后台更新" message="当前显示上次加载的数据，更新完成后自动替换。" />
    <ReportFeedback
      v-if="error && result.report"
      type="error"
      :title="isWorker ? '任务操作未完成' : '车间报表读取失败'"
      :message="`${error}；当前仍显示上次成功查询结果。`"
      action-label="重新查询"
      dismissible
      @action="applyTaskFilters"
      @dismiss="error = ''"
    />

    <section v-if="!isWorker" class="workshop-kpis" aria-label="车间任务概况" :aria-busy="loading">
      <article class="workshop-kpi kpi-blue">
        <span class="workshop-kpi__icon"><AppIcon name="tasks" size="21" tone="current" /></span>
        <div><span>全部任务</span><p><strong>{{ result.report ? workshopMetrics.taskCount : '--' }}</strong> <small>项</small></p><em>当前查询区间</em></div>
        <MetricSparkline class="workshop-sparkline" :values="workshopMetricSeries.tasks" label="每日任务数量趋势" />
      </article>
      <article class="workshop-kpi kpi-amber">
        <span class="workshop-kpi__icon"><AppIcon name="clock" size="21" tone="current" /></span>
        <div><span>待确认</span><p><strong>{{ result.report ? workshopMetrics.pendingCount : '--' }}</strong> <small>项</small></p><em>等待工人确认</em></div>
        <MetricSparkline class="workshop-sparkline" :values="workshopMetricSeries.pending" label="每日待确认任务趋势" />
      </article>
      <article class="workshop-kpi kpi-green">
        <span class="workshop-kpi__icon"><AppIcon name="check" size="21" tone="current" /></span>
        <div><span>已确认</span><p><strong>{{ result.report ? workshopMetrics.confirmedCount : '--' }}</strong> <small>项</small></p><em>已登记完成人</em></div>
        <MetricSparkline class="workshop-sparkline" :values="workshopMetricSeries.confirmed" label="每日已确认任务趋势" />
      </article>
      <article class="workshop-kpi kpi-red">
        <span class="workshop-kpi__icon"><AppIcon name="briefcase" size="21" tone="current" /></span>
        <div><span>涉及工程</span><p><strong>{{ result.report ? workshopMetrics.projectCount : '--' }}</strong> <small>个</small></p><em>当前区间范围</em></div>
        <MetricSparkline class="workshop-sparkline" :values="workshopMetricSeries.projects" label="每日涉及工程趋势" />
      </article>
      <article class="workshop-kpi kpi-violet">
        <span class="workshop-kpi__icon"><AppIcon name="activity" size="21" tone="current" /></span>
        <div><span>确认率</span><p><strong>{{ result.report ? number(workshopMetrics.confirmationRate) : '--' }}</strong> <small>%</small></p><em>按区间任务计算</em></div>
        <MetricSparkline class="workshop-sparkline" :values="workshopMetricSeries.confirmationRate" label="每日任务确认率趋势" />
      </article>
    </section>

    <WorkshopStructureCharts
      v-if="!isWorker && result.report"
      :duration-distribution="result.duration_distribution || []"
      :status-distribution="result.status_distribution || []"
      :top-cut-length-tasks="result.top_cut_length_tasks || []"
      :loading="loading && (isWorker || !result.report)"
    />

    <ReportTrendPanel
    class="workshop-panel"
    number="01"
    :title="isWorker ? '待确认任务表' : '区间车间报表'"
    :note="isWorker ? '只能确认尚未被其他工人确认的任务' : '已确认任务可撤销或改派'"
    :show-chart="false"
    :loading="loading && (isWorker || !result.report)"
    :has-data="result.items.length > 0"
    :empty="Boolean(result.report) && !result.items.length"
    :error="!result.report ? error : ''"
    :loading-title="isWorker ? '正在读取待确认任务' : '正在读取车间报表'"
    :empty-title="isWorker ? '当前没有待确认任务' : '当前条件下暂无任务'"
    :empty-message="isWorker ? '所选日期或筛选条件下没有需要展示的任务。' : '可以调整统计区间或筛选条件后重新查询。'"
    fit-table-content
    @retry="applyTaskFilters"
  >
    <template #summary>
      <form class="workshop-filters" :class="{ manager: !isWorker }" @submit.prevent="applyTaskFilters">
        <label v-if="isWorker"><span>生产日期</span><AppDatePicker v-model="filters.reportDate" required /></label>
        <label><span>工程号</span><AppSelect v-model="filters.projectName" :disabled="!result.report || (!isWorker && loading)"><option value="">全部</option><option v-for="name in result.project_names" :key="name" :value="name">{{ name }}</option></AppSelect></label>
        <label><span>确认状态</span><AppSelect v-model="filters.confirmationStatus" :disabled="!result.report || (!isWorker && loading)"><option value="all">全部</option><option value="unconfirmed">未确认</option><option value="confirmed">已确认</option></AppSelect></label>
        <NButton attr-type="submit" :disabled="loading || !result.report"><AppIcon name="sparkles" size="14" tone="current" />{{ loading ? '筛选中' : '筛选明细' }}</NButton>
      </form>
      <div v-if="result.report" class="workshop-report-state"><strong>{{ isWorker ? formatChineseDate(result.report.report_date) : `${formatChineseDate(result.report.period_start)} 至 ${formatChineseDate(result.report.period_end)}` }} 车间报表</strong><span>{{ isWorker ? `生成人：${result.report.generated_by_name || '--'} · 生成时间：${time(result.report.generated_at)} · ` : '' }}总任务 {{ result.report.item_count }} 项 · 已确认 {{ result.report.confirmed_count }} 项</span></div>
      <div v-if="!isWorker && appliedRouteState.taskId" class="workshop-summary" role="status">正在定位任务 {{ appliedRouteState.taskId }}；范围内没有该任务时显示空结果。<NButton text @click="applyTaskFilters">清除任务定位</NButton></div>
      <div v-if="result.report" class="workshop-summary">当前筛选返回 {{ result.total }} 项任务，所选工程号已确认 {{ result.confirmed_count }} 项</div>
      <div v-if="isWorker && result.report" class="workshop-batch-actions"><span>已选择 <strong>{{ selectedTaskCount }}</strong> 项待确认任务</span><div><NButton attr-type="button" :disabled="!selectedTaskCount || batchConfirmation.saving" @click="clearSelectedTasks">清空选择</NButton><NButton class="primary" attr-type="button" :disabled="!selectedTaskCount || loading || batchConfirmation.saving" @click="openBatchConfirmation()" type="primary">确认选中任务</NButton></div></div>
    </template>
    <template #table>
      <AppReportTable :columns="taskTableColumns" :items="result.items" :total="result.total" :page="result.page" :page-size="result.page_size" :page-sizes="[]" @page="changePage" :row-key="row => row.task_id" :row-class-name="row => (isWorker && selectedTasks.has(row.task_id)) ? 'selected' : ''" :loading="loading && (isWorker || !result.report)">
        <template #header-c0><NCheckbox :checked="allVisibleTasksSelected" :indeterminate="someVisibleTasksSelected" :disabled="!selectableTasks.length || loading || batchConfirmation.saving" aria-label="全选当前页待确认任务" @update:checked="toggleVisibleTasks($event)" /></template>
        <template #c0="{ row }"><div class="workshop-select-cell"><NCheckbox :checked="selectedTasks.has(row.task_id)" :disabled="Boolean(row.confirmed_by) || loading || batchConfirmation.saving" :aria-label="`选择任务 ${display(row.program_no)}`" @update:checked="setTaskSelected(row, $event)" /></div></template>
        <template #c1="{ row, index: rowIndex }">{{ (result.page - 1) * result.page_size + rowIndex + 1 }}</template>
        <template #c2="{ row }">{{ row.project_code?.trim() || '--' }}</template>
        <template #c3="{ row }">{{ row.ship_no?.trim() || '--' }}</template>
        <template #c4="{ row }">{{ row.segment_name?.trim() || '--' }}</template>
        <template #c5="{ row }"><div class="program-cell" :title="display(row.program_no)">{{ display(row.program_no) }}</div></template>
        <template #c6="{ row }">{{ number(row.cut_length) }}</template>
        <template #c7="{ row }">{{ number(row.groove_length) }}</template>
        <template #c8="{ row }"><div class="duration-cell">{{ duration(row.work_time) }}</div></template>
        <template #c9="{ row }"><div class="time-cell">{{ time(row.production_time) }}</div></template>
        <template #c10="{ row }"><div class="confirm-cell"><div v-if="row.confirmed_by" class="confirmation-person"><NTag type="success" size="small" class="confirmed-mark">✓ {{ row.confirmed_by }}<small>{{ time(row.confirmed_at) }}</small></NTag><NButton v-if="isOwnConfirmation(row)" attr-type="button" class="correction-button" :disabled="loading || correction.saving" @click="openCorrection(row)">撤销我的确认</NButton><NButton v-else-if="!isWorker" attr-type="button" class="correction-button" :disabled="loading || correction.saving" @click="openCorrection(row)">更正确认</NButton></div><NButton v-else-if="isWorker" attr-type="button" :disabled="loading || batchConfirmation.saving" @click="openSingleConfirmation(row)">确认是我完成</NButton><NTag v-else type="warning" size="small" class="pending-confirmation">待确认</NTag></div></template>
        <template #empty>当前条件下暂无任务</template>
      </AppReportTable>
    </template>
  </ReportTrendPanel>

    <ReportTrendPanel
    v-if="!isWorker && result.worker_summaries?.length"
    class="worker-time-summary"
    number="02"
    title="工人工时汇总"
    note="按当前确认人统计，只包含已确认任务"
    :show-chart="false"
    fit-table-content
  >
    <template #table>
      <AppReportTable :columns="workerTableColumns" :items="result.worker_summaries" :row-key="worker => worker.worker_id" :paginate="false">
        <template #c0="{ row: worker, index: rowIndex }">{{ rowIndex + 1 }}</template>
        <template #c1="{ row: worker }">{{ worker.company_no || '--' }}</template>
        <template #c2="{ row: worker }">{{ worker.worker_name }}</template>
        <template #c3="{ row: worker }">{{ worker.confirmed_task_count }} 项</template>
        <template #c4="{ row: worker }">{{ number(worker.total_cut_length) }} 米</template>
        <template #c5="{ row: worker }"><div class="duration-cell">{{ duration(worker.total_work_time_seconds) }}</div></template>
        <template #c6="{ row: worker }">{{ number(worker.work_time_share) }}%</template>
        <template #c7="{ row: worker }"><div class="time-cell">{{ time(worker.last_confirmed_at) }}</div></template>
      </AppReportTable>
    </template>
  </ReportTrendPanel>

    <AnalysisPanel
      v-if="!isWorker"
      report-type="workshop"
      :report-date="appliedRouteState.startDate"
      :period-start="appliedRouteState.startDate"
      :period-end="appliedRouteState.endDate"
      :ready="analysisReady"
      :data-version="result.analysis_source_version || ''"
      :version-check-enabled="appliedRouteState.defaultView"
      @refresh-report="refreshAnalysisReport"
    />
  </div>

</template>

<style scoped>
.workshop-query-panel { display:grid; grid-template-columns:140px minmax(0,1fr); align-items:center; gap:16px; padding:16px; border:1px solid var(--theme-border-color); border-radius:var(--report-panel-radius); background:rgb(var(--container-bg-color)); }
.workshop-dashboard--manager .workshop-query-panel { position:relative; overflow:hidden; border-radius:14px; background:linear-gradient(90deg,rgb(var(--container-bg-color) / .98) 0%,rgb(var(--container-bg-color) / .91) 39%,rgb(var(--container-bg-color) / .04) 72%),url('../assets/home-laser-cutting.webp') center 48%/cover no-repeat; box-shadow:var(--theme-shadow); }
.workshop-dashboard--manager .workshop-heading { position:relative; z-index:1; }
.workshop-heading { display:flex; align-items:center; justify-content:space-between; gap:16px; min-height:40px; }
.workshop-heading h2 { margin:0; font-size:20px; font-weight:600; }
.workshop-heading span { color:var(--text-color-3); font-size:var(--font-auxiliary); }
.workshop-dashboard--worker .workshop-query-panel { display:block; padding:0; border:0; background:transparent; }
@media (max-width:1000px) { .workshop-query-panel { grid-template-columns:minmax(0,1fr); gap:12px; } }.workshop-kpis{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:16px;margin:16px 0}.workshop-kpi{--kpi-color:rgb(var(--info-color));--kpi-soft:rgb(var(--info-color) / .1);position:relative;min-width:0;min-height:108px;padding:12px;border:1px solid var(--theme-border-color);border-top:2px solid var(--kpi-color);border-radius:10px;background:rgb(var(--container-bg-color));box-shadow:var(--theme-shadow);transition:transform .18s ease,box-shadow .18s ease;animation:surface-in .42s cubic-bezier(.2,.75,.25,1) both;display:grid;grid-template-columns:35px minmax(0,1fr);align-items:center;gap:8px 12px}.workshop-kpi:hover{transform:translateY(-2px);box-shadow:var(--theme-shadow)}.workshop-kpi:nth-child(2){animation-delay:.04s}.workshop-kpi:nth-child(3){animation-delay:.08s}.workshop-kpi:nth-child(4){animation-delay:.12s}.workshop-kpi:nth-child(5){animation-delay:.16s}.workshop-kpi.kpi-amber{--kpi-color:rgb(var(--warning-color));--kpi-soft:rgb(var(--warning-color) / .1)}.workshop-kpi.kpi-green{--kpi-color:rgb(var(--success-color));--kpi-soft:rgb(var(--success-color) / .1)}.workshop-kpi.kpi-red{--kpi-color:var(--text-color-3);--kpi-soft:rgb(var(--info-color) / .1)}.workshop-kpi.kpi-violet{--kpi-color:rgb(var(--primary-color));--kpi-soft:rgb(var(--primary-color) / .1)}.workshop-kpi__icon{position:relative;z-index:1;width:35px;height:35px;display:grid;place-items:center;flex:0 0 auto;color:var(--kpi-color);border-radius:50%;background:var(--kpi-soft);transition:transform .2s ease}.workshop-kpi:hover .workshop-kpi__icon{transform:scale(1.08) rotate(-3deg)}.workshop-kpi>div{display:contents}.workshop-kpi>div>span{display:block;color:var(--text-color-1);font-size:var(--font-auxiliary);font-weight:700;white-space:normal;overflow-wrap:anywhere;grid-column:2}.workshop-kpi p{margin:0;color:var(--text-color-1);font-size:var(--font-auxiliary);grid-column:1/-1;min-width:0;overflow-wrap:anywhere}.workshop-kpi p strong{font-size:21px}.workshop-kpi p small{margin-left:2px;color:var(--muted);font-size:var(--font-auxiliary)}.workshop-kpi em{display:block;color:var(--muted);font-style:normal;font-size:var(--font-auxiliary);grid-column:1/-1;min-width:0;white-space:normal;overflow-wrap:anywhere;line-height:1.6}.workshop-sparkline{width:58px;height:25px;opacity:.72;grid-column:1/-1;justify-self:end}
:deep(.workshop-panel),:deep(.worker-time-summary){margin-bottom:24px;border:1px solid var(--theme-border-color);border-radius:var(--report-panel-radius);box-shadow:var(--theme-shadow)}:deep(.workshop-panel .report-trend-panel__heading),:deep(.worker-time-summary .report-trend-panel__heading){min-height:47px;padding:0 16px;border-bottom:1px solid var(--theme-border-color);background:rgb(var(--container-bg-color))}:deep(.workshop-panel .report-trend-panel__heading>div),:deep(.worker-time-summary .report-trend-panel__heading>div){gap:8px}:deep(.workshop-panel .report-trend-panel__heading>div>span),:deep(.worker-time-summary .report-trend-panel__heading>div>span){width:28px;height:24px;display:grid;place-items:center;padding:0;color:rgb(var(--info-color));border:0;border-radius:7px;background:rgb(var(--info-color) / .1);font-size:var(--font-auxiliary);font-weight:700}:deep(.workshop-panel .report-trend-panel__heading h2),:deep(.worker-time-summary .report-trend-panel__heading h2){color:var(--text-color-1);font-size:14px}:deep(.workshop-panel .report-trend-panel__heading p),:deep(.worker-time-summary .report-trend-panel__heading p){color:var(--muted);font-size:var(--font-auxiliary)}.workshop-filters{grid-template-columns:1.2fr 1fr auto!important;gap:16px;padding:16px;border-bottom:1px solid var(--theme-border-color)}.workshop-dashboard--worker .workshop-filters{grid-template-columns:1fr 1fr 1fr auto!important}.workshop-filters label span{margin-bottom:5px;font-size:var(--font-auxiliary)}.workshop-filters>button{min-height:38px;display:flex;align-items:center;justify-content:center;gap:6px;padding:0 15px}.workshop-report-state,.workshop-empty-report{margin:0 16px 8px;padding:8px 12px;border:0;border-radius:7px;background:rgb(var(--container-bg-color));font-size:var(--font-auxiliary)}.workshop-report-state strong,.workshop-empty-report strong{font-size:var(--font-auxiliary)}.workshop-summary{padding:0 16px 16px;font-size:var(--font-auxiliary)}:deep(.workshop-panel .report-trend-panel__table),:deep(.worker-time-summary .report-trend-panel__table){overflow-x:auto;border-color:var(--theme-border-color);border-radius:7px}.pending-confirmation{display:inline-flex;align-items:center;justify-content:center;min-height:22px;padding:0 9px;border-radius:999px;background:rgb(var(--warning-color) / .1);color:rgb(var(--warning-color));font-weight:700}.confirm-cell{white-space:nowrap}.confirmation-person{display:inline-flex;align-items:center;gap:8px;width:auto}.confirmed-mark{display:inline-flex;flex-direction:row;white-space:nowrap;color:rgb(var(--success-color))}.confirmed-mark small{margin:0 0 0 8px}.workshop-batch-actions{margin:0 14px 9px;border:1px solid var(--theme-border-color);border-radius:7px}
@media(max-width:1120px){.workshop-kpis{grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;margin:16px 0}.workshop-kpi:last-child{grid-column:1/-1}}
@media(max-width:900px){.workshop-filters,.workshop-dashboard--worker .workshop-filters{grid-template-columns:1fr 1fr!important}}
@media(max-width:560px){.workshop-kpis,.workshop-filters,.workshop-dashboard--worker .workshop-filters{grid-template-columns:1fr!important}.workshop-kpi:last-child{grid-column:auto}.workshop-report-state,.workshop-empty-report{align-items:flex-start;flex-direction:column;margin:0 16px 8px;padding:8px 12px}}
@media(prefers-reduced-motion:reduce){.workshop-kpi{animation:none}.workshop-kpi{transition:none;display:grid;grid-template-columns:35px minmax(0,1fr);align-items:center;gap:8px 12px;padding:12px}}


/* Match the table and panel density used by the other management reports. */
</style>
