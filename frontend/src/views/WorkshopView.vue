<script setup>
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
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
import ReportExportMenu from '../components/ReportExportMenu.vue'
import ReportPeriodPicker from '../components/ReportPeriodPicker.vue'
import ReportTrendPanel from '../components/ReportTrendPanel.vue'
import { reportToday } from '../api/reportDates.js'
import { formatChineseDate, formatChineseDateTime } from '../utils/dateDisplay.js'

const props = defineProps({
  exporting: Boolean,
  user: { type: Object, required: true },
  mode: { type: String, default: 'manager', validator: (value) => ['manager', 'worker'].includes(value) },
})
const emit = defineEmits(['export'])
const today = reportToday()
const pageSize = 10
const filters = reactive({ reportDate: today, startDate: today, endDate: today, projectName: '', confirmationStatus: 'all' })
const result = ref({ report: null, items: [], total: 0, page: 1, page_size: pageSize, project_names: [], confirmed_count: 0, worker_summaries: [] })
const loading = ref(false)
const error = ref('')
const chartType = ref('bar_line')
const selectedTasks = ref(new Map())
const batchConfirmation = reactive({ visible: false, saving: false, error: '', tasks: [] })
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
const visibleCorrectionReasonPresets = computed(() => {
  if (isWorker.value) return correctionReasonPresets.workerRevoke
  return correction.action === 'reassign'
    ? correctionReasonPresets.adminReassign
    : correctionReasonPresets.adminRevoke
})
const analysisReady = computed(() => Boolean(
  !isWorker.value
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
let refreshTimer = null
let feedbackTimer = null

function stopRefreshTimer() {
  if (!refreshTimer) return
  window.clearInterval(refreshTimer)
  refreshTimer = null
}

function startRefreshTimer() {
  if (!isWorker.value || refreshTimer) return
  refreshTimer = window.setInterval(() => {
    if (
      document.visibilityState === 'visible'
      && result.value.report
      && !loading.value
      && !batchConfirmation.visible
      && !batchConfirmation.saving
      && !correction.visible
    ) {
      load(result.value.page, { silent: true, ensureReport: false, force: true })
    }
  }, 5000)
}

watch(() => [filters.reportDate, filters.startDate, filters.endDate], () => {
  filters.projectName = ''
  filters.confirmationStatus = 'all'
  clearSelectedTasks()
  batchConfirmation.visible = false
  result.value = { report: null, items: [], total: 0, page: 1, page_size: pageSize, project_names: [], confirmed_count: 0, worker_summaries: [] }
})
watch(() => [filters.projectName, filters.confirmationStatus], () => {
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
function rowDate(row) { return row?.production_time ? String(row.production_time).slice(0, 10) : filters.reportDate }

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

async function load(page = 1, { silent = false, ensureReport = true, force = false } = {}) {
  if (!isWorker.value && filters.endDate < filters.startDate) {
    error.value = '结束日期不能早于开始日期'
    return
  }
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
      ? await listWorkshopTasks(query, { force })
      : await listWorkshopRangeTasks(query, { force })
    if (isWorker.value && ensureReport && (!data.report || !data.report.item_count)) {
      await generateWorkshopDailyReport(query.reportDate)
      data = await listWorkshopTasks(query, { force: true })
    }
    const current = isWorker.value
      ? query.reportDate === filters.reportDate
      : query.startDate === filters.startDate && query.endDate === filters.endDate
    if (current) {
      result.value = data
      reconcileVisibleTaskSelection(data.items || [])
    }
  } catch (exception) {
    if (!silent) error.value = exception.message
  } finally {
    if (!silent) loading.value = false
  }
}

function applyTaskFilters() {
  clearSelectedTasks()
  load(1, { force: true })
}

async function submitBatchConfirmation() {
  if (!isWorker.value || batchConfirmation.saving || !batchConfirmation.tasks.length) return
  const tasks = [...batchConfirmation.tasks]
  batchConfirmation.saving = true
  batchConfirmation.error = ''
  error.value = ''
  showConfirmationFeedback('pending', '正在确认任务', `共 ${tasks.length} 项 · 请稍候`)
  try {
    const confirmationResult = await confirmWorkshopTasks(tasks.map((row) => row.task_id), filters.reportDate)
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

async function initializeReport() {
  loading.value = true
  error.value = ''
  try {
    const latest = await getLatestWorkshopProductionDate(today)
    if (latest.report_date) {
      filters.reportDate = latest.report_date
      filters.startDate = latest.report_date
      filters.endDate = latest.report_date
    }
  } catch (exception) {
    error.value = exception.message
  }
  await load(1, { force: false })
}

onMounted(() => {
  initializeReport()
})
onActivated(() => {
  startRefreshTimer()
  if (isWorker.value && result.value.report && !loading.value && !batchConfirmation.visible && !batchConfirmation.saving) {
    load(result.value.page, { silent: true, ensureReport: false, force: true })
  }
})
onDeactivated(stopRefreshTimer)
onUnmounted(() => {
  stopRefreshTimer()
  if (feedbackTimer) window.clearTimeout(feedbackTimer)
})
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
      <section class="confirmation-dialog batch-confirmation-dialog" role="dialog" aria-modal="true" aria-labelledby="batch-confirmation-dialog-title">
        <header><div><small>任务确认</small><h2 id="batch-confirmation-dialog-title">确认以下任务由我完成</h2></div><button type="button" aria-label="关闭" :disabled="batchConfirmation.saving" @click="closeBatchConfirmation">×</button></header>
        <p class="batch-confirmation-intro">请核对以下 {{ batchConfirmation.tasks.length }} 项任务。确认后，任务将登记到当前工人“{{ user.display_name }}”名下。</p>
        <div class="batch-confirmation-list">
          <table v-content-table>
            <thead><tr><th>序号</th><th>工程/船号</th><th>分段名</th><th>程序号</th><th>生产时间</th></tr></thead>
            <tbody><tr v-for="(row, index) in batchConfirmation.tasks" :key="row.task_id"><td>{{ index + 1 }}</td><td>{{ display(row.project_code || row.ship_no) }}</td><td>{{ display(row.segment_name) }}</td><td :title="display(row.program_no)">{{ display(row.program_no) }}</td><td>{{ time(row.production_time) }}</td></tr></tbody>
          </table>
        </div>
        <p v-if="batchConfirmation.error" class="confirmation-dialog-error">{{ batchConfirmation.error }}</p>
        <footer><button type="button" :disabled="batchConfirmation.saving" @click="closeBatchConfirmation">返回检查</button><button class="primary" type="button" :disabled="batchConfirmation.saving" @click="submitBatchConfirmation"><span v-if="batchConfirmation.saving" class="spinner"></span>{{ batchConfirmation.saving ? '确认中…' : `确认这 ${batchConfirmation.tasks.length} 项任务` }}</button></footer>
      </section>
    </div>

    <div v-if="correction.visible" class="confirmation-dialog-backdrop" @click.self="closeCorrection">
      <section class="confirmation-dialog" role="dialog" aria-modal="true" aria-labelledby="confirmation-dialog-title">
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
          <label class="confirmation-dialog-field"><span>更正原因</span><textarea v-model="correction.reason" maxlength="200" rows="3" placeholder="选择常用原因，或直接填写其他原因" required></textarea><small>{{ correction.reason.trim().length }} / 200</small></label>
          <p v-if="correction.error" class="confirmation-dialog-error">{{ correction.error }}</p>
          <section v-if="!isWorker" class="confirmation-audit-list"><h3>该任务的确认记录</h3><p v-if="!correction.audits.length">暂无历史更正记录</p><article v-for="audit in correction.audits" :key="audit.audit_id"><strong>{{ audit.action_label }}</strong><span>{{ audit.previous_worker_name || '待确认' }} → {{ audit.new_worker_name || '待确认' }}</span><small>{{ audit.operated_by_name }} · {{ time(audit.operated_at) }} · {{ audit.reason }}</small></article></section>
        </template>
        <footer><button type="button" :disabled="correction.saving" @click="closeCorrection">取消</button><button class="primary" type="button" :disabled="correction.loading || correction.saving || (!isWorker && correction.action === 'reassign' && !correction.workerId)" @click="submitCorrection"><span v-if="correction.saving" class="spinner"></span>{{ correction.saving ? '保存中…' : isWorker ? '确认撤销' : '保存更正' }}</button></footer>
      </section>
    </div>
  </Teleport>

  <section class="page-hero report-hero workshop-hero">
    <div><span class="eyebrow">{{ isWorker ? 'WORKER TASK CONFIRMATION' : 'WORKSHOP REPORT' }}</span><h2>{{ isWorker ? '我的任务确认' : '车间报表' }}</h2><p>{{ isWorker ? `当前工人：${user.display_name}。请确认本人完成的任务，如果误点可撤销本人确认。` : '查看全部任务和工人确认结果，必要时可撤销或改派并保留审计记录。' }}</p></div>
    <form v-if="!isWorker" class="report-query workshop-query" @submit.prevent="applyTaskFilters">
      <ReportPeriodPicker
        v-model:start-date="filters.startDate"
        v-model:end-date="filters.endDate"
        :anchor-date="filters.reportDate"
        :disabled="loading"
        @validation-error="error = $event"
      />
      <label><span>图表类型</span><AppSelect v-model="chartType"><option value="bar_line">柱线组合</option><option value="bar">柱状图</option><option value="line">折线图</option><option value="area">面积图</option></AppSelect></label>
      <button class="primary" type="submit" :disabled="loading"><span v-if="loading" class="spinner"></span>{{ loading ? '查询中' : '查询报表' }}</button>
      <ReportExportMenu :disabled="loading || !result.report" :loading="exporting" @select="exportReport" />
    </form>
  </section>

  <div v-if="error" class="global-error"><strong>操作未完成</strong><span>{{ error }}</span><button @click="error = ''">关闭</button></div>
  <ReportTrendPanel
    class="workshop-panel"
    number="01"
    :title="isWorker ? '待确认任务表' : '区间车间报表'"
    :note="isWorker ? '只能确认尚未被其他工人确认的任务' : '已确认任务可撤销或改派'"
    :show-chart="false"
    fit-table-content
  >
    <template #summary>
      <form class="workshop-filters" :class="{ manager: !isWorker }" @submit.prevent="applyTaskFilters">
        <label v-if="isWorker"><span>生产日期</span><AppDatePicker v-model="filters.reportDate" required /></label>
        <label><span>工程/船号</span><AppSelect v-model="filters.projectName" :disabled="!result.report"><option value="">全部</option><option v-for="name in result.project_names" :key="name" :value="name">{{ name }}</option></AppSelect></label>
        <label><span>确认状态</span><AppSelect v-model="filters.confirmationStatus" :disabled="!result.report"><option value="all">全部</option><option value="unconfirmed">未确认</option><option value="confirmed">已确认</option></AppSelect></label>
        <button type="submit" :disabled="loading || !result.report">{{ loading ? '筛选中' : '筛选明细' }}</button>
      </form>
      <div v-if="result.report" class="workshop-report-state"><strong>{{ isWorker ? formatChineseDate(result.report.report_date) : `${formatChineseDate(result.report.period_start)} 至 ${formatChineseDate(result.report.period_end)}` }} 车间报表</strong><span>{{ isWorker ? `生成人：${result.report.generated_by_name || '--'} · 生成时间：${time(result.report.generated_at)} · ` : '' }}总任务 {{ result.report.item_count }} 项 · 已确认 {{ result.report.confirmed_count }} 项</span></div>
      <div v-else class="workshop-empty-report"><strong>{{ loading ? '正在加载最近车间报表' : '该日期暂无车间报表' }}</strong><span>{{ loading ? '系统正在查找最近有数据的生产日并加载全部明细。' : '当前源数据在该日期没有已完成任务。' }}</span></div>
      <div v-if="result.report" class="workshop-summary">当前筛选返回 {{ result.total }} 项任务，所选工程/船号已确认 {{ result.confirmed_count }} 项</div>
      <div v-if="isWorker && result.report" class="workshop-batch-actions"><span>已选择 <strong>{{ selectedTaskCount }}</strong> 项待确认任务</span><div><button type="button" :disabled="!selectedTaskCount || batchConfirmation.saving" @click="clearSelectedTasks">清空选择</button><button class="primary" type="button" :disabled="!selectedTaskCount || loading || batchConfirmation.saving" @click="openBatchConfirmation()">确认选中任务</button></div></div>
    </template>
    <template #table>
      <table v-content-table class="workshop-table" :class="{ 'worker-workshop-table': isWorker }">
        <thead><tr><th v-if="isWorker" class="workshop-select-cell"><input type="checkbox" :checked="allVisibleTasksSelected" :indeterminate="someVisibleTasksSelected" :disabled="!selectableTasks.length || loading || batchConfirmation.saving" aria-label="全选当前页待确认任务" @change="toggleVisibleTasks($event.target.checked)" /></th><th>序号</th><th>工程号</th><th>船号</th><th>分段名</th><th>程序号</th><th>切割长度</th><th>坡口长度</th><th>切割时长</th><th>时间</th><th>待确认</th><th>备注</th></tr></thead>
        <tbody>
          <tr v-if="loading"><td :colspan="isWorker ? 12 : 11" class="table-empty">正在查询任务...</td></tr>
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
    <template #footer><div v-if="result.report" class="history-pagination"><span class="history-page-summary">第 {{ result.page }} / {{ totalPages }} 页</span><div class="history-page-buttons"><button :disabled="loading || result.page <= 1" @click="load(result.page - 1)">上一页</button><button :disabled="loading || result.page >= totalPages" @click="load(result.page + 1)">下一页</button></div></div></template>
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
      <table v-content-table class="worker-time-table">
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
  />

</template>
