<script setup>
import { computed, nextTick, onActivated, onDeactivated, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  confirmWorkshopTasks,
  correctWorkshopTask,
  generateWorkshopDailyReport,
  getLatestWorkshopProductionDate,
  listUserAccounts,
  listWorkshopConfirmationAudits,
  listWorkshopRangeTasks,
  listWorkshopTasks,
  revokeWorkshopTask,
} from '../api/reports'
import AnalysisPanel from '../components/AnalysisPanel.vue'
import AppIcon from '../components/AppIcon.vue'
import MetricSparkline from '../components/MetricSparkline.vue'
import ReportFeedback from '../components/ReportFeedback.vue'
import ReportTrendPanel from '../components/ReportTrendPanel.vue'
import StandardReportQuery from '../components/StandardReportQuery.vue'
import WorkshopStructureCharts from '../components/WorkshopStructureCharts.vue'
import { reportToday, taskProductionDate } from '../api/reportDates.js'
import { formatChineseDate, formatChineseDateTime } from '../utils/dateDisplay.js'
import { useDialogFocus } from '../composables/useDialogFocus.js'
import { useDeviceSwitchQuery } from '../composables/useDeviceSwitchQuery.js'
import { mergeOwnedQuery, queryChoice, queryDateRange, queryPositiveInteger, queryText, sameRouteQuery } from '../utils/reportRouteState.js'

const props = defineProps({
  exporting: Boolean,
  user: { type: Object, required: true },
  mode: { type: String, default: 'manager', validator: (value) => ['manager', 'worker'].includes(value) },
})
const emit = defineEmits(['export'])
const route = useRoute()
const router = useRouter()
const today = reportToday()
const pageSize = 10
const filters = reactive({ reportDate: today, startDate: today, endDate: today, projectName: '', confirmationStatus: 'all' })
const appliedRouteState = reactive({ startDate: today, endDate: today, projectName: '', confirmationStatus: 'all', page: 1, defaultView: true })
const result = ref({ report: null, items: [], total: 0, page: 1, page_size: pageSize, project_names: [], confirmed_count: 0, worker_summaries: [], duration_distribution: [], status_distribution: [], top_cut_length_tasks: [] })
const loading = ref(false)
const error = ref('')
const pageActive = ref(true)
const defaultReportView = ref(true)
const chartType = ref(props.mode === 'manager' ? queryChoice(route.query.chart, ['bar_line', 'bar', 'line', 'area'], 'bar_line') : 'bar_line')
const selectedTasks = ref(new Map())
const batchConfirmation = reactive({ visible: false, saving: false, error: '', tasks: [] })
const batchDialogElement = ref(null)
const confirmationFeedback = reactive({ visible: false, type: 'pending', title: '', message: '' })
const correction = reactive({
  visible: false,
  row: null,
  action: 'revoke',
  workerId: '',
  reason: '',
  saving: false,
  loading: false,
  error: '',
  workers: [],
  audits: [],
})
const correctionDialogElement = ref(null)
const batchDialogOpen = computed(() => batchConfirmation.visible)
const correctionDialogOpen = computed(() => correction.visible)
useDialogFocus(batchDialogOpen, batchDialogElement, closeBatchConfirmation)
useDialogFocus(correctionDialogOpen, correctionDialogElement, closeCorrection)
const correctionReasonPresets = {
  workerRevoke: [
    '误选了其他工人的任务',
    '任务并非本人完成',
    '任务信息有误，需重新核对',
  ],
  adminRevoke: [
    '原确认人选择错误',
    '暂时无法确认实际完成人',
    '任务记录异常，待现场核查',
  ],
  adminReassign: [
    '根据现场记录更正实际完成人',
    '班组核对后调整任务归属',
    '原确认人误选，改派给正确人员',
  ],
}
const presetReasonValues = new Set(Object.values(correctionReasonPresets).flat())
const totalPages = computed(() => Math.max(1, Math.ceil(result.value.total / result.value.page_size)))
const isWorker = computed(() => props.mode === 'worker')
const dateQueryDirty = computed(() => !isWorker.value && (
  filters.startDate !== appliedRouteState.startDate || filters.endDate !== appliedRouteState.endDate
))
const visibleCorrectionReasonPresets = computed(() => {
  if (isWorker.value) return correctionReasonPresets.workerRevoke
  return correction.action === 'reassign'
    ? correctionReasonPresets.adminReassign
    : correctionReasonPresets.adminRevoke
})
const analysisReady = computed(() => Boolean(
  pageActive.value
  && !isWorker.value
  && result.value.report
  && result.value.report.period_start === filters.startDate
  && result.value.report.period_end === filters.endDate
  && !loading.value
))
const selectableTasks = computed(() => isWorker.value ? result.value.items.filter((row) => !row.confirmed_by) : [])
const selectedTaskCount = computed(() => selectedTasks.value.size)
const allVisibleTasksSelected = computed(() => (
  selectableTasks.value.length > 0
  && selectableTasks.value.every((row) => selectedTasks.value.has(row.task_id))
))
const someVisibleTasksSelected = computed(() => (
  !allVisibleTasksSelected.value
  && selectableTasks.value.some((row) => selectedTasks.value.has(row.task_id))
))
const availableWorkers = computed(() => correction.workers.filter((worker) => (
  worker.role === 'worker' && worker.active && worker.user_id !== correction.row?.confirmed_by_id
)))
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
let refreshTimer = null
let feedbackTimer = null
let requestController = null
let requestSequence = 0
let routeStateReady = false
let restoringRouteState = false

const routeQueryKeys = ['start', 'end', 'project', 'confirmation', 'page', 'chart']

function routeStateQuery() {
  return mergeOwnedQuery(route.query, routeQueryKeys, {
    start: appliedRouteState.defaultView ? '' : appliedRouteState.startDate,
    end: appliedRouteState.defaultView ? '' : appliedRouteState.endDate,
    project: appliedRouteState.projectName,
    confirmation: appliedRouteState.confirmationStatus === 'all' ? '' : appliedRouteState.confirmationStatus,
    page: appliedRouteState.page === 1 ? '' : appliedRouteState.page,
    chart: chartType.value === 'bar_line' ? '' : chartType.value,
  })
}

useDeviceSwitchQuery(isWorker.value ? 'worker-tasks' : 'workshop-report', () => ({
  ...routeStateQuery(),
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
  result.value = { report: null, items: [], total: 0, page: 1, page_size: pageSize, project_names: [], confirmed_count: 0, worker_summaries: [], duration_distribution: [], status_distribution: [], top_cut_length_tasks: [] }
})
watch(() => [filters.projectName, filters.confirmationStatus], () => {
  if (restoringRouteState) return
  clearSelectedTasks()
  batchConfirmation.visible = false
})
watch(() => correction.action, () => {
  if (!isWorker.value && presetReasonValues.has(correction.reason)) correction.reason = ''
})

function display(value) { return value || '--' }
function number(value) { return value == null ? '--' : new Intl.NumberFormat('zh-CN', { minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(value) }
function duration(value) {
  if (value == null) return '--'
  const seconds = Math.max(0, Math.round(Number(value)))
  return [Math.floor(seconds / 3600), Math.floor(seconds % 3600 / 60), seconds % 60].map((item) => String(item).padStart(2, '0')).join(':')
}
function time(value) { return formatChineseDateTime(value) }
function rowDate(row) { return taskProductionDate(row, filters.reportDate) }

function clearSelectedTasks() {
  selectedTasks.value = new Map()
}

function setTaskSelected(row, selected) {
  if (!isWorker.value || row.confirmed_by || batchConfirmation.saving) return
  const next = new Map(selectedTasks.value)
  if (selected) next.set(row.task_id, row)
  else next.delete(row.task_id)
  selectedTasks.value = next
}

function toggleVisibleTasks(selected) {
  const next = new Map(selectedTasks.value)
  for (const row of selectableTasks.value) {
    if (selected) next.set(row.task_id, row)
    else next.delete(row.task_id)
  }
  selectedTasks.value = next
}

function reconcileVisibleTaskSelection(items) {
  if (!selectedTasks.value.size) return
  const next = new Map(selectedTasks.value)
  for (const row of items) {
    if (!next.has(row.task_id)) continue
    if (row.confirmed_by) next.delete(row.task_id)
    else next.set(row.task_id, row)
  }
  selectedTasks.value = next
}

function openBatchConfirmation(tasks = [...selectedTasks.value.values()]) {
  const pendingTasks = tasks.filter((row) => !row.confirmed_by)
  if (!isWorker.value || !pendingTasks.length || batchConfirmation.saving) return
  batchConfirmation.tasks = pendingTasks
  batchConfirmation.error = ''
  batchConfirmation.visible = true
}

function openSingleConfirmation(row) {
  if (row.confirmed_by) return
  selectedTasks.value = new Map([[row.task_id, row]])
  openBatchConfirmation([row])
}

function closeBatchConfirmation() {
  if (batchConfirmation.saving) return
  batchConfirmation.visible = false
  batchConfirmation.error = ''
}

function showConfirmationFeedback(type, title, message, timeout = 0) {
  if (feedbackTimer) window.clearTimeout(feedbackTimer)
  Object.assign(confirmationFeedback, { visible: true, type, title, message })
  if (timeout) {
    feedbackTimer = window.setTimeout(() => {
      confirmationFeedback.visible = false
      feedbackTimer = null
    }, timeout)
  }
}

function closeConfirmationFeedback() {
  if (feedbackTimer) window.clearTimeout(feedbackTimer)
  feedbackTimer = null
  confirmationFeedback.visible = false
}

function applyConfirmedTasks(confirmations = []) {
  const confirmationByTaskId = new Map(
    confirmations.map((confirmation) => [String(confirmation.task_id), confirmation]),
  )
  if (!confirmationByTaskId.size) return

  let newlyConfirmed = 0
  const items = result.value.items.map((row) => {
    const confirmation = confirmationByTaskId.get(String(row.task_id))
    if (!confirmation) return row
    if (!row.confirmed_by) newlyConfirmed += 1
    return {
      ...row,
      confirmed_by_id: confirmation.worker_id,
      confirmed_by: confirmation.worker_name,
      confirmed_by_company_no: confirmation.worker_company_no ?? row.confirmed_by_company_no ?? null,
      confirmed_at: confirmation.confirmed_at,
    }
  })
  result.value = {
    ...result.value,
    items,
    confirmed_count: Number(result.value.confirmed_count || 0) + newlyConfirmed,
  }
  reconcileVisibleTaskSelection(items)
}

async function load(page = 1, { silent = false, ensureReport = true, force = false, syncRoute = false } = {}) {
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
      : query.startDate === filters.startDate && query.endDate === filters.endDate
    if (pageActive.value && !signal.aborted && sequence === requestSequence && current) {
      result.value = data
      if (!isWorker.value) {
        Object.assign(appliedRouteState, {
          startDate: query.startDate,
          endDate: query.endDate,
          projectName: query.projectName,
          confirmationStatus: query.confirmationStatus,
          page: data.page,
          defaultView: defaultReportView.value,
        })
      }
      reconcileVisibleTaskSelection(data.items || [])
      if (syncRoute) await syncRouteState()
    }
  } catch (exception) {
    if (exception.name !== 'AbortError' && sequence === requestSequence && !silent) error.value = exception.message
  } finally {
    if (sequence === requestSequence) {
      if (!silent) loading.value = false
      requestController = null
    }
  }
}

function refreshAnalysisReport() {
  if (!pageActive.value || isWorker.value || loading.value) return
  return load(result.value.page, { ensureReport: false, force: true })
}

function applyTaskFilters() {
  defaultReportView.value = false
  clearSelectedTasks()
  load(1, { force: true, syncRoute: !isWorker.value })
}

function changePage(page) {
  load(page, { syncRoute: !isWorker.value })
}

async function submitBatchConfirmation() {
  if (!isWorker.value || batchConfirmation.saving || !batchConfirmation.tasks.length) return
  const tasks = [...batchConfirmation.tasks]
  batchConfirmation.saving = true
  batchConfirmation.error = ''
  error.value = ''
  showConfirmationFeedback('pending', '正在确认任务', `共 ${tasks.length} 项 · 请稍候`)
  try {
    const confirmationResult = await confirmWorkshopTasks(tasks.map((row) => row.task_id), filters.reportDate,
      Object.fromEntries(tasks.map((row) => [row.task_id, row.snapshot_revision || 1])))
    const confirmations = confirmationResult.items || []
    applyConfirmedTasks(confirmations)
    batchConfirmation.visible = false
    batchConfirmation.tasks = []
    clearSelectedTasks()
    showConfirmationFeedback(
      'success',
      '确认成功',
      `${tasks.length} 项任务已登记为由 ${props.user.display_name} 完成`,
      5000,
    )
    await load(result.value.page, { silent: true, ensureReport: false })
    // 即使后台刷新短暂返回旧快照，也不能让已确认任务重新显示待确认按钮。
    applyConfirmedTasks(confirmations)
  } catch (exception) {
    error.value = exception.message
    batchConfirmation.error = exception.message
    showConfirmationFeedback('error', '确认失败', exception.message, 7000)
  } finally {
    batchConfirmation.saving = false
  }
}

function isOwnConfirmation(row) {
  return isWorker.value && row.confirmed_by_id === props.user.user_id
}

async function openCorrection(row) {
  if (!row.confirmed_by || (isWorker.value && !isOwnConfirmation(row))) return
  Object.assign(correction, {
    visible: true,
    row,
    action: 'revoke',
    workerId: '',
    reason: '',
    saving: false,
    loading: !isWorker.value,
    error: '',
    workers: [],
    audits: [],
  })
  if (isWorker.value) return
  try {
    const [users, auditResult] = await Promise.all([
      listUserAccounts(),
      listWorkshopConfirmationAudits({ reportDate: rowDate(row), taskId: row.task_id }),
    ])
    correction.workers = users.items || []
    correction.audits = auditResult.items || []
  } catch (exception) {
    correction.error = exception.message
  } finally {
    correction.loading = false
  }
}

function closeCorrection() {
  if (correction.saving) return
  correction.visible = false
  correction.row = null
}

function selectCorrectionReason(reason) {
  correction.reason = reason
  correction.error = ''
}

async function submitCorrection() {
  const reason = correction.reason.trim()
  if (reason.length < 2) {
    correction.error = '请填写至少 2 个字的原因'
    return
  }
  if (!isWorker.value && correction.action === 'reassign' && !correction.workerId) {
    correction.error = '请选择改派后的工人'
    return
  }
  correction.saving = true
  correction.error = ''
  const row = correction.row
  try {
    if (isWorker.value) {
      await revokeWorkshopTask(row.task_id, filters.reportDate, reason)
      showConfirmationFeedback('success', '已撤销确认', `${display(row.program_no)} 已恢复为待确认`, 5000)
    } else {
      await correctWorkshopTask(row.task_id, {
        reportDate: rowDate(row),
        action: correction.action,
        workerId: correction.action === 'reassign' ? correction.workerId : null,
        reason,
      })
      const target = correction.workers.find((worker) => worker.user_id === correction.workerId)
      const message = correction.action === 'reassign'
        ? `${display(row.program_no)} 已改派给 ${target?.display_name || '所选工人'}`
        : `${display(row.program_no)} 已恢复为待确认`
      showConfirmationFeedback('success', '更正已保存', message, 5000)
    }
    correction.visible = false
    correction.row = null
    await load(result.value.page, { silent: true, ensureReport: false })
  } catch (exception) {
    correction.error = exception.message
    showConfirmationFeedback('error', '更正失败', exception.message, 7000)
  } finally {
    correction.saving = false
  }
}

function exportReport(format = 'html') {
  emit('export', {
    report_type: 'workshop',
    report_date: isWorker.value ? filters.reportDate : filters.startDate,
    period_start: isWorker.value ? filters.reportDate : filters.startDate,
    period_end: isWorker.value ? filters.reportDate : filters.endDate,
    project_code: isWorker.value ? null : filters.projectName || null,
    ship_no: null,
    parameters: { chart_type: chartType.value },
  }, format)
}

function requestedRouteState() {
  const range = queryDateRange(route.query)
  return {
    range,
    chartType: queryChoice(route.query.chart, ['bar_line', 'bar', 'line', 'area'], 'bar_line'),
    projectName: range ? queryText(route.query.project).trim() : '',
    confirmationStatus: range ? queryChoice(route.query.confirmation, ['all', 'unconfirmed', 'confirmed'], 'all') : 'all',
    page: range ? queryPositiveInteger(route.query.page) : 1,
  }
}

async function applyRequestedRouteState(state) {
  restoringRouteState = true
  chartType.value = state.chartType
  if (state.range) {
    if (isWorker.value) filters.reportDate = state.range.start
    filters.startDate = state.range.start
    filters.endDate = state.range.end
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
    const latest = await getLatestWorkshopProductionDate(today, { signal })
    if (!pageActive.value || signal.aborted || sequence !== requestSequence) return
    if (latest.report_date) {
      filters.reportDate = latest.report_date
      filters.startDate = latest.report_date
      filters.endDate = latest.report_date
    }
    if (!isWorker.value) {
      filters.projectName = ''
      filters.confirmationStatus = 'all'
    }
    defaultReportView.value = true
  } catch (exception) {
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
  if (feedbackTimer) window.clearTimeout(feedbackTimer)
  feedbackTimer = null
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
</script>

<template>
  <Transition name="action-toast">
    <aside v-if="confirmationFeedback.visible" class="confirmation-feedback" :class="confirmationFeedback.type" role="status" aria-live="polite">
      <span v-if="confirmationFeedback.type === 'pending'" class="spinner dark" aria-hidden="true"></span>
      <span v-else class="confirmation-feedback-icon" aria-hidden="true">{{ confirmationFeedback.type === 'success' ? '✓' : '!' }}</span>
      <div><strong>{{ confirmationFeedback.title }}</strong><p>{{ confirmationFeedback.message }}</p></div>
      <button type="button" aria-label="关闭确认提示" @click="closeConfirmationFeedback">×</button>
    </aside>
  </Transition>

  <Teleport to="body">
    <div v-if="batchConfirmation.visible" class="confirmation-dialog-backdrop" @click.self="closeBatchConfirmation">
      <section ref="batchDialogElement" class="confirmation-dialog batch-confirmation-dialog" role="dialog" aria-modal="true" aria-labelledby="batch-confirmation-dialog-title" tabindex="-1">
        <header><div><small>任务确认</small><h2 id="batch-confirmation-dialog-title">确认以下任务由我完成</h2></div><button type="button" aria-label="关闭" :disabled="batchConfirmation.saving" @click="closeBatchConfirmation">×</button></header>
        <p class="batch-confirmation-intro">请核对以下 {{ batchConfirmation.tasks.length }} 项任务。确认后，任务将登记到当前工人“{{ user.display_name }}”名下。</p>
        <div class="batch-confirmation-list">
          <table v-content-table="pageActive" data-content-columns="center left left left left">
            <thead><tr><th>序号</th><th>工程/船号</th><th>分段名</th><th>程序号</th><th>生产时间</th></tr></thead>
            <tbody><tr v-for="(row, index) in batchConfirmation.tasks" :key="row.task_id"><td>{{ index + 1 }}</td><td>{{ display(row.project_code || row.ship_no) }}</td><td>{{ display(row.segment_name) }}</td><td :title="display(row.program_no)">{{ display(row.program_no) }}</td><td>{{ time(row.production_time) }}</td></tr></tbody>
          </table>
        </div>
        <p v-if="batchConfirmation.error" class="confirmation-dialog-error" role="alert">{{ batchConfirmation.error }}</p>
        <footer><button type="button" :disabled="batchConfirmation.saving" @click="closeBatchConfirmation">返回检查</button><button class="primary" type="button" :disabled="batchConfirmation.saving" @click="submitBatchConfirmation"><span v-if="batchConfirmation.saving" class="spinner"></span>{{ batchConfirmation.saving ? '确认中…' : `确认这 ${batchConfirmation.tasks.length} 项任务` }}</button></footer>
      </section>
    </div>

    <div v-if="correction.visible" class="confirmation-dialog-backdrop" @click.self="closeCorrection">
      <section ref="correctionDialogElement" class="confirmation-dialog" role="dialog" aria-modal="true" aria-labelledby="confirmation-dialog-title" tabindex="-1">
        <header><div><small>{{ isWorker ? '本人撤销' : '管理员更正' }}</small><h2 id="confirmation-dialog-title">{{ isWorker ? '撤销任务确认' : '更正确认人' }}</h2></div><button type="button" aria-label="关闭" :disabled="correction.saving" @click="closeCorrection">×</button></header>
        <div class="confirmation-dialog-task"><strong>{{ display(correction.row?.program_no) }}</strong><span>当前确认人：{{ correction.row?.confirmed_by }} · {{ time(correction.row?.confirmed_at) }}</span></div>
        <div v-if="correction.loading" class="confirmation-dialog-loading"><span class="spinner dark"></span>正在读取工人和审计记录…</div>
        <template v-else>
          <label v-if="!isWorker" class="confirmation-dialog-field"><span>更正方式</span><AppSelect v-model="correction.action"><option value="revoke">撤销为待确认</option><option value="reassign">直接改派给其他工人</option></AppSelect></label>
          <label v-if="!isWorker && correction.action === 'reassign'" class="confirmation-dialog-field"><span>目标工人</span><AppSelect v-model="correction.workerId" required><option value="" disabled>请选择工人</option><option v-for="worker in availableWorkers" :key="worker.user_id" :value="worker.user_id">{{ worker.display_name }}{{ worker.company_no || worker.username ? ` · ${worker.company_no || worker.username}` : '' }}</option></AppSelect><small v-if="!availableWorkers.length">只显示已登录过系统的可用工人账号。</small></label>
          <div class="confirmation-reason-presets" role="group" aria-label="常用更正原因">
            <span>常用原因</span>
            <div><button v-for="reason in visibleCorrectionReasonPresets" :key="reason" type="button" :class="{ active: correction.reason === reason }" :disabled="correction.saving" @click="selectCorrectionReason(reason)">{{ reason }}</button></div>
          </div>
          <label class="confirmation-dialog-field"><span>更正原因</span><textarea v-model="correction.reason" name="correction_reason" autocomplete="off" maxlength="200" rows="3" placeholder="选择常用原因，或直接填写其他原因…" required></textarea><small>{{ correction.reason.trim().length }} / 200</small></label>
          <p v-if="correction.error" class="confirmation-dialog-error" role="alert">{{ correction.error }}</p>
          <section v-if="!isWorker" class="confirmation-audit-list"><h3>该任务的确认记录</h3><p v-if="!correction.audits.length">暂无历史更正记录</p><article v-for="audit in correction.audits" :key="audit.audit_id"><strong>{{ audit.action_label }}</strong><span>{{ audit.previous_worker_name || '待确认' }} → {{ audit.new_worker_name || '待确认' }}</span><small>{{ audit.operated_by_name }} · {{ time(audit.operated_at) }} · {{ audit.reason }}</small></article></section>
        </template>
        <footer><button type="button" :disabled="correction.saving" @click="closeCorrection">取消</button><button class="primary" type="button" :disabled="correction.loading || correction.saving || (!isWorker && correction.action === 'reassign' && !correction.workerId)" @click="submitCorrection"><span v-if="correction.saving" class="spinner"></span>{{ correction.saving ? '保存中…' : isWorker ? '确认撤销' : '保存更正' }}</button></footer>
      </section>
    </div>
  </Teleport>

  <div class="workshop-dashboard" :class="isWorker ? 'workshop-dashboard--worker' : 'workshop-dashboard--manager'">
    <section class="page-hero report-hero workshop-hero" :class="{ 'report-hero--query': !isWorker }">
      <div class="report-hero-copy workshop-hero__copy">
        <span class="report-hero-eyebrow eyebrow">{{ isWorker ? 'WORKER TASK CONFIRMATION' : 'WORKSHOP REPORT' }}</span>
        <h2>{{ isWorker ? '我的任务确认' : '车间报表' }}</h2>
        <p v-if="isWorker">当前工人：{{ user.display_name }}。请确认本人完成的任务，如果误点可撤销本人确认。</p>
      </div>
      <StandardReportQuery
        v-if="!isWorker"
        v-model:start-date="filters.startDate"
        v-model:end-date="filters.endDate"
        v-model:chart-type="chartType"
        class="report-hero-query workshop-query-shell"
        aria-label="车间报表查询条件"
        :anchor-date="filters.reportDate"
        :loading="loading"
        :has-data="Boolean(result.report)"
        :date-dirty="dateQueryDirty"
        :export-disabled="loading || !result.report"
        :exporting="exporting"
        @submit="applyTaskFilters"
        @export="exportReport"
        @validation-error="error = $event"
      />
    </section>

    <ReportFeedback
      v-if="error && result.report"
      type="error"
      :title="isWorker ? '任务操作未完成' : '车间报表读取失败'"
      :message="error"
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
      :loading="loading"
    />

    <ReportTrendPanel
    class="workshop-panel"
    number="01"
    :title="isWorker ? '待确认任务表' : '区间车间报表'"
    :note="isWorker ? '只能确认尚未被其他工人确认的任务' : '已确认任务可撤销或改派'"
    :show-chart="false"
    :loading="loading"
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
        <label><span>工程/船号</span><AppSelect v-model="filters.projectName" :disabled="!result.report"><option value="">全部</option><option v-for="name in result.project_names" :key="name" :value="name">{{ name }}</option></AppSelect></label>
        <label><span>确认状态</span><AppSelect v-model="filters.confirmationStatus" :disabled="!result.report"><option value="all">全部</option><option value="unconfirmed">未确认</option><option value="confirmed">已确认</option></AppSelect></label>
        <button type="submit" :disabled="loading || !result.report"><AppIcon name="sparkles" size="14" tone="current" />{{ loading ? '筛选中' : '筛选明细' }}</button>
      </form>
      <div v-if="result.report" class="workshop-report-state"><strong>{{ isWorker ? formatChineseDate(result.report.report_date) : `${formatChineseDate(result.report.period_start)} 至 ${formatChineseDate(result.report.period_end)}` }} 车间报表</strong><span>{{ isWorker ? `生成人：${result.report.generated_by_name || '--'} · 生成时间：${time(result.report.generated_at)} · ` : '' }}总任务 {{ result.report.item_count }} 项 · 已确认 {{ result.report.confirmed_count }} 项</span></div>
      <div v-if="result.report" class="workshop-summary">当前筛选返回 {{ result.total }} 项任务，所选工程/船号已确认 {{ result.confirmed_count }} 项</div>
      <div v-if="isWorker && result.report" class="workshop-batch-actions"><span>已选择 <strong>{{ selectedTaskCount }}</strong> 项待确认任务</span><div><button type="button" :disabled="!selectedTaskCount || batchConfirmation.saving" @click="clearSelectedTasks">清空选择</button><button class="primary" type="button" :disabled="!selectedTaskCount || loading || batchConfirmation.saving" @click="openBatchConfirmation()">确认选中任务</button></div></div>
    </template>
    <template #table>
      <table v-content-table="pageActive" class="workshop-table" :class="{ 'worker-workshop-table': isWorker }" :data-content-columns="isWorker ? 'center center left left left left right right right left center left' : 'center left left left left right right right left center left'">
        <thead><tr><th v-if="isWorker" class="workshop-select-cell"><input type="checkbox" :checked="allVisibleTasksSelected" :indeterminate="someVisibleTasksSelected" :disabled="!selectableTasks.length || loading || batchConfirmation.saving" aria-label="全选当前页待确认任务" @change="toggleVisibleTasks($event.target.checked)" /></th><th>序号</th><th>工程号</th><th>船号</th><th>分段名</th><th>程序号</th><th>切割长度</th><th>坡口长度</th><th>切割时长</th><th>时间</th><th>确认状态</th><th>备注</th></tr></thead>
        <tbody>
          <tr v-if="loading"><td :colspan="isWorker ? 12 : 11" class="table-empty">正在查询任务…</td></tr>
          <tr v-else-if="!result.items.length"><td :colspan="isWorker ? 12 : 11" class="table-empty">当前条件下暂无任务</td></tr>
          <tr v-for="(row, index) in result.items" v-else :key="row.task_id" :class="{ selected: isWorker && selectedTasks.has(row.task_id) }">
            <td v-if="isWorker" class="workshop-select-cell"><input type="checkbox" :checked="selectedTasks.has(row.task_id)" :disabled="Boolean(row.confirmed_by) || loading || batchConfirmation.saving" :aria-label="`选择任务 ${display(row.program_no)}`" @change="setTaskSelected(row, $event.target.checked)" /></td>
            <td>{{ (result.page - 1) * result.page_size + index + 1 }}</td><td>{{ display(row.project_code) }}</td><td>{{ display(row.ship_no) }}</td><td>{{ display(row.segment_name) }}</td><td class="program-cell" :title="display(row.program_no)">{{ display(row.program_no) }}</td><td>{{ number(row.cut_length) }}</td><td>{{ number(row.groove_length) }}</td><td class="duration-cell">{{ duration(row.work_time) }}</td><td class="time-cell">{{ time(row.production_time) }}</td>
            <td class="confirm-cell"><div v-if="row.confirmed_by" class="confirmation-person"><span class="confirmed-mark">✓ {{ row.confirmed_by }}<small>{{ time(row.confirmed_at) }}</small></span><button v-if="isOwnConfirmation(row)" type="button" class="correction-button" :disabled="correction.saving" @click="openCorrection(row)">撤销我的确认</button><button v-else-if="!isWorker" type="button" class="correction-button" :disabled="correction.saving" @click="openCorrection(row)">更正确认</button></div><button v-else-if="isWorker" type="button" :disabled="batchConfirmation.saving" @click="openSingleConfirmation(row)">确认是我完成</button><span v-else class="pending-confirmation">待确认</span></td>
            <td>{{ display(row.remark) }}</td>
          </tr>
        </tbody>
      </table>
    </template>
    <template #footer><div v-if="result.report && result.items.length" class="history-pagination app-pagination"><div class="history-page-buttons app-pagination__actions"><button :disabled="loading || result.page <= 1" @click="changePage(result.page - 1)">上一页</button><span class="app-pagination__summary">第 {{ result.page }} / {{ totalPages }} 页，共 {{ result.total }} 条</span><button :disabled="loading || result.page >= totalPages" @click="changePage(result.page + 1)">下一页</button></div></div></template>
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
      <table v-content-table="pageActive" class="worker-time-table" data-content-columns="center left left right right right right left">
        <thead><tr><th>序号</th><th>工号</th><th>姓名</th><th>已确认任务</th><th>切割长度</th><th>切割时长</th><th>工时占比</th><th>最后确认时间</th></tr></thead>
        <tbody>
          <tr v-for="(worker, index) in result.worker_summaries" :key="worker.worker_id">
            <td>{{ index + 1 }}</td><td>{{ worker.company_no || '--' }}</td><td>{{ worker.worker_name }}</td><td>{{ worker.confirmed_task_count }} 项</td><td>{{ number(worker.total_cut_length) }} 米</td><td class="duration-cell">{{ duration(worker.total_work_time_seconds) }}</td><td>{{ number(worker.work_time_share) }}%</td><td class="time-cell">{{ time(worker.last_confirmed_at) }}</td>
          </tr>
        </tbody>
      </table>
    </template>
  </ReportTrendPanel>

    <AnalysisPanel
      v-if="!isWorker"
      report-type="workshop"
      :report-date="filters.startDate"
      :period-start="filters.startDate"
      :period-end="filters.endDate"
      :ready="analysisReady"
      :data-version="result.analysis_source_version || ''"
      :version-check-enabled="defaultReportView"
      @refresh-report="refreshAnalysisReport"
    />
  </div>

</template>

<style scoped>
.workshop-dashboard>.page-hero{position:relative;min-height:174px;display:flex;align-items:center;overflow:hidden;padding:25px 30px;border:1px solid var(--theme-border-color);border-radius:14px;background:linear-gradient(90deg,rgb(var(--container-bg-color) / 0.99) 0%,rgb(var(--container-bg-color) / 0.92) 39%,rgb(var(--container-bg-color) / 0.08) 69%),url('../assets/home-laser-cutting.webp') center 51%/cover no-repeat;box-shadow:var(--theme-shadow);animation:surface-in .46s cubic-bezier(.2,.75,.25,1) both}.workshop-dashboard>.page-hero::after{content:"";position:absolute;inset:0;pointer-events:none;background:transparent}.workshop-hero__copy{position:relative;z-index:1;max-width:560px}.workshop-hero__copy .eyebrow{display:block;margin:0 0 5px;color:rgb(var(--info-color));font-size:var(--font-auxiliary);font-weight:700;letter-spacing:1.4px}.workshop-hero__copy h2{margin:0 0 7px;color:var(--text-color-1);font-size:var(--font-page-title);line-height:1.08;letter-spacing:1px}.workshop-hero__copy>p{max-width:530px;margin:0;color:var(--text-color-3);font-size:12px;line-height:1.7}.workshop-query-shell{margin:10px 0;border:1px solid var(--theme-border-color)}.workshop-query-shell .report-query{width:100%;grid-template-columns:repeat(2,minmax(170px,1fr)) minmax(160px,.7fr) 128px 128px;padding:0;border:0;background:transparent;box-shadow:none}.workshop-query-shell :deep(.report-period-picker__trigger),.workshop-query-shell :deep(.app-select__trigger),.workshop-query-shell .report-query>button{height:38px;min-height:38px;border-radius:6px;font-size:var(--font-auxiliary)}.workshop-query-shell .report-query>button.primary{display:flex;align-items:center;justify-content:center;gap:6px;border-color:var(--theme-border-color);background:rgb(var(--info-color));box-shadow:var(--theme-shadow)}.global-error{margin:10px 0 0;border-radius:9px}.workshop-kpis{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:16px;margin:16px 0}.workshop-kpi{--kpi-color:rgb(var(--info-color));--kpi-soft:rgb(var(--info-color) / .1);position:relative;min-width:0;min-height:108px;padding:12px;border:1px solid var(--theme-border-color);border-top:2px solid var(--kpi-color);border-radius:10px;background:rgb(var(--container-bg-color));box-shadow:var(--theme-shadow);transition:transform .18s ease,box-shadow .18s ease;animation:surface-in .42s cubic-bezier(.2,.75,.25,1) both;display:grid;grid-template-columns:35px minmax(0,1fr);align-items:center;gap:8px 12px}.workshop-kpi:hover{transform:translateY(-2px);box-shadow:var(--theme-shadow)}.workshop-kpi:nth-child(2){animation-delay:.04s}.workshop-kpi:nth-child(3){animation-delay:.08s}.workshop-kpi:nth-child(4){animation-delay:.12s}.workshop-kpi:nth-child(5){animation-delay:.16s}.workshop-kpi.kpi-amber{--kpi-color:rgb(var(--warning-color));--kpi-soft:rgb(var(--warning-color) / .1)}.workshop-kpi.kpi-green{--kpi-color:rgb(var(--success-color));--kpi-soft:rgb(var(--success-color) / .1)}.workshop-kpi.kpi-red{--kpi-color:var(--text-color-3);--kpi-soft:rgb(var(--info-color) / .1)}.workshop-kpi.kpi-violet{--kpi-color:rgb(var(--primary-color));--kpi-soft:rgb(var(--primary-color) / .1)}.workshop-kpi__icon{position:relative;z-index:1;width:35px;height:35px;display:grid;place-items:center;flex:0 0 auto;color:var(--kpi-color);border-radius:50%;background:var(--kpi-soft);transition:transform .2s ease}.workshop-kpi:hover .workshop-kpi__icon{transform:scale(1.08) rotate(-3deg)}.workshop-kpi>div{display:contents}.workshop-kpi>div>span{display:block;color:var(--text-color-1);font-size:var(--font-auxiliary);font-weight:700;white-space:normal;overflow-wrap:anywhere;grid-column:2}.workshop-kpi p{margin:0;color:var(--text-color-1);font-size:var(--font-auxiliary);grid-column:1/-1;min-width:0;overflow-wrap:anywhere}.workshop-kpi p strong{font-size:21px}.workshop-kpi p small{margin-left:2px;color:var(--muted);font-size:var(--font-auxiliary)}.workshop-kpi em{display:block;color:var(--muted);font-style:normal;font-size:var(--font-auxiliary);grid-column:1/-1;min-width:0;white-space:normal;overflow-wrap:anywhere;line-height:1.6}.workshop-sparkline{width:58px;height:25px;opacity:.72;grid-column:1/-1;justify-self:end}
:deep(.workshop-panel),:deep(.worker-time-summary){margin-bottom:24px;border:1px solid var(--theme-border-color);border-radius:var(--report-panel-radius);box-shadow:var(--theme-shadow)}:deep(.workshop-panel .report-trend-panel__heading),:deep(.worker-time-summary .report-trend-panel__heading){min-height:47px;padding:0 16px;border-bottom:1px solid var(--theme-border-color);background:rgb(var(--container-bg-color))}:deep(.workshop-panel .report-trend-panel__heading>div),:deep(.worker-time-summary .report-trend-panel__heading>div){gap:8px}:deep(.workshop-panel .report-trend-panel__heading>div>span),:deep(.worker-time-summary .report-trend-panel__heading>div>span){width:28px;height:24px;display:grid;place-items:center;padding:0;color:rgb(var(--info-color));border:0;border-radius:7px;background:rgb(var(--info-color) / .1);font-size:var(--font-auxiliary);font-weight:700}:deep(.workshop-panel .report-trend-panel__heading h2),:deep(.worker-time-summary .report-trend-panel__heading h2){color:var(--text-color-1);font-size:14px}:deep(.workshop-panel .report-trend-panel__heading p),:deep(.worker-time-summary .report-trend-panel__heading p){color:var(--muted);font-size:var(--font-auxiliary)}.workshop-filters{grid-template-columns:1.2fr 1fr auto!important;gap:16px;padding:16px;border-bottom:1px solid var(--theme-border-color)}.workshop-dashboard--worker .workshop-filters{grid-template-columns:1fr 1fr 1fr auto!important}.workshop-filters label span{margin-bottom:5px;font-size:var(--font-auxiliary)}.workshop-filters>button{min-height:38px;display:flex;align-items:center;justify-content:center;gap:6px;padding:0 15px;border-color:var(--theme-border-color);border-radius:6px;background:rgb(var(--info-color) / .1);color:rgb(var(--info-color));font-size:var(--font-auxiliary)}.workshop-report-state,.workshop-empty-report{margin:0 16px 8px;padding:8px 12px;border:0;border-radius:7px;background:rgb(var(--container-bg-color));font-size:var(--font-auxiliary)}.workshop-report-state strong,.workshop-empty-report strong{font-size:var(--font-auxiliary)}.workshop-summary{padding:0 16px 16px;font-size:var(--font-auxiliary)}:deep(.workshop-panel .report-trend-panel__table),:deep(.worker-time-summary .report-trend-panel__table){overflow-x:auto;border-color:var(--theme-border-color);border-radius:7px}.workshop-table th,.workshop-table td{height:36px;padding:6px 8px;font-size:var(--font-auxiliary)}.workshop-table thead th{color:var(--text-color-1)!important;background:rgb(var(--info-color))!important;font-size:var(--font-auxiliary)!important}.workshop-table tbody tr:hover{background:rgb(var(--info-color) / .1)}.pending-confirmation{display:inline-flex;align-items:center;justify-content:center;min-height:22px;padding:0 9px;border-radius:999px;background:rgb(var(--warning-color) / .1);color:rgb(var(--warning-color));font-weight:700}.confirmed-mark{color:rgb(var(--success-color))}.history-pagination{min-height:45px;padding:7px 14px;border-top:1px solid var(--theme-border-color);background:rgb(var(--container-bg-color))}.history-page-buttons button{min-height:30px;border-radius:5px}.worker-time-table th,.worker-time-table td{height:36px;padding:6px 8px;font-size:var(--font-auxiliary)}.worker-time-table thead th{background:rgb(var(--info-color))}.workshop-batch-actions{margin:0 14px 9px;border:1px solid var(--theme-border-color);border-radius:7px}
@media(max-width:1120px){.workshop-kpis{grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;margin:16px 0}.workshop-kpi:last-child{grid-column:1/-1}}
@media(max-width:900px){.workshop-query-shell .report-query{grid-template-columns:repeat(2,minmax(180px,1fr))}.workshop-query-shell :deep(.report-period-picker){grid-column:1/-1}.workshop-filters,.workshop-dashboard--worker .workshop-filters{grid-template-columns:1fr 1fr!important}}
@media(max-width:560px){.workshop-dashboard>.page-hero{min-height:0;padding:22px 18px;background:linear-gradient(90deg,rgb(var(--container-bg-color) / 0.97),rgb(var(--container-bg-color) / 0.89)),url('../assets/home-laser-cutting.webp') center/cover}.workshop-hero__copy h2{font-size:var(--font-page-title-mobile)}.workshop-query-shell .report-query,.workshop-kpis,.workshop-filters,.workshop-dashboard--worker .workshop-filters{grid-template-columns:1fr!important}.workshop-query-shell :deep(.report-period-picker),.workshop-kpi:last-child{grid-column:auto}.workshop-report-state,.workshop-empty-report{align-items:flex-start;flex-direction:column;margin:0 16px 8px;padding:8px 12px}}
@media(prefers-reduced-motion:reduce){.workshop-dashboard>.page-hero,.workshop-kpi{animation:none}.workshop-kpi{transition:none;display:grid;grid-template-columns:35px minmax(0,1fr);align-items:center;gap:8px 12px;padding:12px}}


/* Match the table and panel density used by the other management reports. */
.workshop-dashboard--worker > .page-hero { margin-bottom:10px; }
.workshop-query-shell { position:relative; padding:12px 14px 13px; border-color:var(--theme-border-color); border-radius:12px; background:rgb(var(--container-bg-color)); box-shadow:var(--theme-shadow); }
.workshop-query-shell::before { content:""; position:absolute; top:-1px; left:18px; width:34px; height:3px; border-radius:0 0 999px 999px; background:rgb(var(--info-color)); }
.workshop-query-shell .standard-report-query { gap:10px; }
.workshop-table th,
.workshop-table td,
.worker-time-table th,
.worker-time-table td { height:35px; }
.workshop-table thead th,
.worker-time-table thead th {
  color:var(--report-table-head-ink)!important;
  background:var(--report-table-head-bg)!important;
  font-size:var(--font-auxiliary)!important;
}
.workshop-table tbody td,
.workshop-table tbody th,
.worker-time-table tbody td,
.worker-time-table tbody th { font-size:var(--font-auxiliary)!important; }
</style>
