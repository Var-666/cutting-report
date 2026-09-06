<script setup>
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import {
  confirmWorkshopTask,
  generateWorkshopDailyReport,
  getLatestWorkshopProductionDate,
  listWorkshopTasks,
} from '../api/reports'
import AnalysisPanel from '../components/AnalysisPanel.vue'
import ReportExportMenu from '../components/ReportExportMenu.vue'
import { reportToday } from '../api/reportDates.js'

const props = defineProps({
  exporting: Boolean,
  user: { type: Object, required: true },
  mode: { type: String, default: 'manager', validator: (value) => ['manager', 'worker'].includes(value) },
})
const emit = defineEmits(['export'])
const today = reportToday()
const pageSize = 10
const filters = reactive({ reportDate: today, projectName: '', confirmationStatus: 'all' })
const result = ref({ report: null, items: [], total: 0, page: 1, page_size: pageSize, project_names: [], confirmed_count: 0 })
const loading = ref(false)
const confirmingId = ref('')
const error = ref('')
const chartType = ref('bar_line')
const confirmationFeedback = reactive({ visible: false, type: 'pending', title: '', message: '' })
const totalPages = computed(() => Math.max(1, Math.ceil(result.value.total / result.value.page_size)))
const isWorker = computed(() => props.mode === 'worker')
let refreshTimer = null
let feedbackTimer = null

function stopRefreshTimer() {
  if (!refreshTimer) return
  window.clearInterval(refreshTimer)
  refreshTimer = null
}

function startRefreshTimer() {
  if (refreshTimer) return
  refreshTimer = window.setInterval(() => {
    if (
      document.visibilityState === 'visible'
      && result.value.report
      && !loading.value
      && !confirmingId.value
    ) {
      load(result.value.page, { silent: true, ensureReport: false })
    }
  }, 5000)
}

watch(() => filters.reportDate, () => {
  filters.projectName = ''
  filters.confirmationStatus = 'all'
  result.value = { report: null, items: [], total: 0, page: 1, page_size: pageSize, project_names: [], confirmed_count: 0 }
})

function display(value) { return value || '--' }
function number(value) { return value == null ? '--' : new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 3 }).format(value) }
function duration(value) {
  if (value == null) return '--'
  const seconds = Math.max(0, Math.round(Number(value)))
  return [Math.floor(seconds / 3600), Math.floor(seconds % 3600 / 60), seconds % 60].map((item) => String(item).padStart(2, '0')).join(':')
}
function time(value) { return value ? String(value).replace('T', ' ').slice(0, 16) : '--' }

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

async function load(page = 1, { silent = false, ensureReport = true, force = true } = {}) {
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
    let data = await listWorkshopTasks(query, { force })
    if (ensureReport && (!data.report || !data.report.item_count)) {
      await generateWorkshopDailyReport(query.reportDate)
      data = await listWorkshopTasks(query, { force: true })
    }
    if (query.reportDate === filters.reportDate) result.value = data
  } catch (exception) {
    if (!silent) error.value = exception.message
  } finally {
    if (!silent) loading.value = false
  }
}

async function confirm(row) {
  if (!isWorker.value || confirmingId.value) return
  confirmingId.value = row.task_id
  error.value = ''
  showConfirmationFeedback('pending', '正在确认任务', `${display(row.program_no)} · 请稍候`)
  try {
    await confirmWorkshopTask(row.task_id, filters.reportDate)
    showConfirmationFeedback(
      'success',
      '确认成功',
      `${display(row.program_no)} 已登记为由 ${props.user.display_name} 完成`,
      5000,
    )
    await load(result.value.page, { silent: true, ensureReport: false })
  } catch (exception) {
    error.value = exception.message
    showConfirmationFeedback('error', '确认失败', exception.message, 7000)
  } finally {
    confirmingId.value = ''
  }
}

function exportReport(format = 'html') {
  emit('export', {
    report_type: 'workshop',
    report_date: filters.reportDate,
    project_code: null,
    ship_no: null,
    parameters: { chart_type: chartType.value },
  }, format)
}

async function initializeReport() {
  loading.value = true
  error.value = ''
  try {
    const latest = await getLatestWorkshopProductionDate(today)
    if (latest.report_date) filters.reportDate = latest.report_date
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
  if (result.value.report && !loading.value && !confirmingId.value) {
    load(result.value.page, { silent: true, ensureReport: false })
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

  <section class="workshop-hero">
    <div><span class="eyebrow">{{ isWorker ? 'WORKER TASK CONFIRMATION' : 'WORKSHOP REPORT' }}</span><h2>{{ isWorker ? '我的任务确认' : '车间报表' }}</h2><p>{{ isWorker ? `当前工人：${user.display_name}。请确认本人完成的任务，其他工人的确认结果会自动同步。` : '管理端只读展示全部任务和工人确认结果，页面会自动同步更新。' }}</p></div>
    <form v-if="!isWorker" class="workshop-query" @submit.prevent="load(1)">
      <label><span>生产日期</span><input v-model="filters.reportDate" type="date" required /></label>
      <label><span>图表类型</span><select v-model="chartType"><option value="bar_line">柱线组合</option><option value="bar">柱状图</option><option value="line">折线图</option><option value="area">面积图</option></select></label>
      <button class="primary" type="submit" :disabled="loading"><span v-if="loading" class="spinner"></span>{{ loading ? '查询中' : '查询报表' }}</button>
      <ReportExportMenu :disabled="loading || !result.report" :loading="exporting" @select="exportReport" />
    </form>
  </section>

  <div v-if="error" class="global-error"><strong>操作未完成</strong><span>{{ error }}</span><button @click="error = ''">关闭</button></div>
  <section class="panel workshop-panel">
    <div class="panel-title"><div><span>01</span><h2>{{ isWorker ? '待确认任务表' : '当日车间报表' }}</h2></div><p>{{ isWorker ? '只能确认尚未被其他工人确认的任务' : '工人确认后会在人员列自动显示' }}</p></div>
    <form class="workshop-filters" :class="{ manager: !isWorker }" @submit.prevent="load(1)">
      <label v-if="isWorker"><span>生产日期</span><input v-model="filters.reportDate" type="date" required /></label>
      <label><span>工程/船号</span><select v-model="filters.projectName" :disabled="!result.report"><option value="">全部</option><option v-for="name in result.project_names" :key="name" :value="name">{{ name }}</option></select></label>
      <label><span>确认状态</span><select v-model="filters.confirmationStatus" :disabled="!result.report"><option value="all">全部</option><option value="unconfirmed">未确认</option><option value="confirmed">已确认</option></select></label>
      <button type="submit" :disabled="loading || !result.report">{{ loading ? '筛选中' : '筛选明细' }}</button>
    </form>
    <div v-if="result.report" class="workshop-report-state"><strong>{{ result.report.report_date }} 车间报表</strong><span>生成人：{{ result.report.generated_by_name }} · 生成时间：{{ time(result.report.generated_at) }} · 总任务 {{ result.report.item_count }} 项 · 已确认 {{ result.report.confirmed_count }} 项</span></div>
    <div v-else class="workshop-empty-report"><strong>{{ loading ? '正在加载最近车间报表' : '该日期暂无车间报表' }}</strong><span>{{ loading ? '系统正在查找最近有数据的生产日并加载全部明细。' : '当前源数据在该日期没有已完成任务。' }}</span></div>
    <div v-if="result.report" class="workshop-summary">当前筛选返回 {{ result.total }} 项任务，所选工程/船号已确认 {{ result.confirmed_count }} 项</div>
    <div v-if="result.report" class="history-wrap workshop-table-wrap">
      <table v-content-table class="workshop-table">
        <colgroup>
          <col class="col-sequence" /><col class="col-project" /><col class="col-ship" />
          <col class="col-segment" /><col class="col-program" /><col class="col-length" />
          <col class="col-length" /><col class="col-duration" /><col class="col-time" />
          <col class="col-person" /><col class="col-remark" />
        </colgroup>
        <thead><tr><th>序号</th><th>工程号</th><th>船号</th><th>分段名</th><th>程序号</th><th>切割长度</th><th>坡口长度</th><th>切割时长</th><th>时间</th><th>人</th><th>备注</th></tr></thead>
        <tbody>
          <tr v-if="loading"><td colspan="11" class="table-empty">正在查询任务...</td></tr>
          <tr v-else-if="!result.items.length"><td colspan="11" class="table-empty">当前条件下暂无任务</td></tr>
          <tr v-for="(row, index) in result.items" v-else :key="row.task_id">
            <td>{{ (result.page - 1) * result.page_size + index + 1 }}</td><td>{{ display(row.project_code) }}</td><td>{{ display(row.ship_no) }}</td><td>{{ display(row.segment_name) }}</td><td class="program-cell" :title="display(row.program_no)">{{ display(row.program_no) }}</td><td>{{ number(row.cut_length) }}</td><td>{{ number(row.groove_length) }}</td><td class="duration-cell">{{ duration(row.work_time) }}</td><td class="time-cell">{{ time(row.production_time) }}</td>
            <td class="confirm-cell"><span v-if="row.confirmed_by" class="confirmed-mark">✓ {{ row.confirmed_by }}<small>{{ time(row.confirmed_at) }}</small></span><button v-else-if="isWorker" type="button" :disabled="Boolean(confirmingId)" @click="confirm(row)"><span v-if="confirmingId === row.task_id" class="spinner dark" aria-hidden="true"></span>{{ confirmingId === row.task_id ? '确认中' : '确认是我完成' }}</button><span v-else class="pending-confirmation">待工人确认</span></td>
            <td>{{ display(row.remark) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-if="result.report" class="history-pagination"><span class="history-page-summary">第 {{ result.page }} / {{ totalPages }} 页</span><div class="history-page-buttons"><button :disabled="loading || result.page <= 1" @click="load(result.page - 1)">上一页</button><button :disabled="loading || result.page >= totalPages" @click="load(result.page + 1)">下一页</button></div></div>
  </section>

  <AnalysisPanel
    v-if="!isWorker"
    report-type="workshop"
    :report-date="filters.reportDate"
    :ready="Boolean(result.report)"
    :context-version="`${result.report?.item_count || 0}-${result.report?.confirmed_count || 0}`"
  />

</template>
