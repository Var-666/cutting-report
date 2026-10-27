<script setup lang="ts">
import type { ReportCreateRequest, ReportDefinition, ReportJob } from '../types/reports'

import { toError } from '../utils/errors'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import ReportForm from '../components/ReportForm.vue'
import ReportJobStatus from '../components/ReportJobStatus.vue'
import ReportTable from '../components/ReportTable.vue'
import ManagementPageHero from '../components/ManagementPageHero.vue'
import {
  createReport,
  getReportStatus,
  listProjects,
  listPublishedDefinitions,
  listReports,
  listShips,
} from '../api/reports'
import { mergeOwnedQuery, queryChoice, queryPositiveInteger, sameRouteQuery } from '../utils/reportRouteState.ts'

defineProps({
  fixedReportType: { type: String, default: '' },
  title: { type: String, default: '报表生成与历史' },
  description: { type: String, default: '领导、车间、设备及自定义报表统一生成、归档和下载。' },
})

const route = useRoute()
const router = useRouter()
const currentJob = ref<ReportJob | null>(null)
const history = ref<ReportJob[]>([])
const projects = ref<string[]>([])
const ships = ref<string[]>([])
const customDefinitions = ref<ReportDefinition[]>([])
const generating = ref(false)
const loadingHistory = ref(false)
const error = ref('')
const historyPage = ref(1)
const historyPageSize = ref(10)
const historyTotal = ref(0)
const historyTotalPages = computed(() => Math.max(1, Math.ceil(historyTotal.value / historyPageSize.value)))
let pollTimer: number | null = null
let pollController: AbortController | null = null
let pollSequence = 0
let historyController: AbortController | null = null
let historySequence = 0
let pageActive = false
let routeStateReady = false

const routeQueryKeys = ['page', 'page_size']

function requestedHistoryState() {
  return {
    page: queryPositiveInteger(route.query.page),
    pageSize: Number(queryChoice(route.query.page_size, ['10', '20', '50'], '10')),
  }
}

async function syncHistoryRouteState() {
  const query = mergeOwnedQuery(route.query, routeQueryKeys, {
    page: historyPage.value === 1 ? '' : historyPage.value,
    page_size: historyPageSize.value === 10 ? '' : historyPageSize.value,
  })
  if (sameRouteQuery(query, route.query)) return
  await router.push({ query })
}

function stopPolling() {
  window.clearTimeout(pollTimer ?? undefined)
  pollTimer = null
  pollSequence += 1
  pollController?.abort()
  pollController = null
}

async function loadHistory(page = historyPage.value, force = false, { pageSize = historyPageSize.value, syncRoute = false } = {}) {
  if (!pageActive) return
  historyController?.abort()
  const controller = new AbortController()
  const sequence = ++historySequence
  historyController = controller
  loadingHistory.value = true
  try {
    const data = await listReports({ page, pageSize, force, signal: controller.signal })
    if (!pageActive || controller.signal.aborted || sequence !== historySequence) return
    history.value = data.items
    historyPage.value = data.page
    historyPageSize.value = data.page_size
    historyTotal.value = data.total
    if (syncRoute) await syncHistoryRouteState()
  } catch (caught) {
    const exception = toError(caught)
    if (exception.name !== 'AbortError' && pageActive && sequence === historySequence) {
      error.value = exception.message
    }
  } finally {
    if (sequence === historySequence) {
      historyController = null
      loadingHistory.value = false
    }
  }
}

function changeHistoryPage(page: number) {
  const target = Math.min(Math.max(Number(page), 1), historyTotalPages.value)
  if (target !== historyPage.value) loadHistory(target, false, { syncRoute: true })
}

function changeHistoryPageSize(pageSize: string | number) {
  loadHistory(1, false, { pageSize: Number(pageSize), syncRoute: true })
}

async function loadProjects(reportDate: string) {
  if (!reportDate) return
  try {
    projects.value = (await listProjects(reportDate)).items
    ships.value = (await listShips(reportDate, '')).items
  } catch (caught) {
    const exception = toError(caught)
    error.value = exception.message
  }
}

async function loadShips({ reportDate, projectCode }: { reportDate: string; projectCode: string }) {
  if (!reportDate) return
  try {
    ships.value = (await listShips(reportDate, projectCode)).items
  } catch (caught) {
    const exception = toError(caught)
    error.value = exception.message
  }
}

async function loadCustomDefinitions() {
  try {
    customDefinitions.value = (await listPublishedDefinitions()).items
  } catch (caught) {
    const exception = toError(caught)
    error.value = exception.message
  }
}

async function generate(form: ReportCreateRequest) {
  stopPolling()
  error.value = ''
  generating.value = true
  try {
    currentJob.value = await createReport(form)
    await router.replace({ query: { ...route.query, job: currentJob.value.report_id } })
    await pollStatus(currentJob.value.report_id)
  } catch (caught) {
    const exception = toError(caught)
    error.value = exception.message
    generating.value = false
  }
}

async function pollStatus(reportId: string) {
  if (!pageActive) return
  window.clearTimeout(pollTimer ?? undefined)
  pollTimer = null
  pollController?.abort()
  const controller = new AbortController()
  const sequence = ++pollSequence
  pollController = controller
  try {
    const job = await getReportStatus(reportId, { signal: controller.signal })
    if (!pageActive || controller.signal.aborted || sequence !== pollSequence) return
    currentJob.value = job
    const terminal = ['completed', 'failed'].includes(job.status)
    if (terminal) {
      generating.value = false
      await loadHistory(1, true, { syncRoute: true })
      return
    }
    generating.value = true
    if (pageActive && sequence === pollSequence) {
      pollTimer = window.setTimeout(() => pollStatus(reportId), 1000)
    }
  } catch (caught) {
    const exception = toError(caught)
    if (exception.name !== 'AbortError' && pageActive && sequence === pollSequence) {
      error.value = exception.message
      generating.value = false
    }
  } finally {
    if (sequence === pollSequence) pollController = null
  }
}

watch(
  () => route.query.job,
  (reportId) => {
    if (typeof reportId === 'string' && reportId && reportId !== currentJob.value?.report_id) {
      stopPolling()
      pollStatus(reportId)
    }
  },
)
watch(
  () => [route.query.page, route.query.page_size],
  async () => {
    if (!pageActive || !routeStateReady || route.name !== 'report-center') return
    const requested = requestedHistoryState()
    if (requested.page !== historyPage.value || requested.pageSize !== historyPageSize.value) {
      await loadHistory(requested.page, false, { pageSize: requested.pageSize })
    }
  },
)

onMounted(async () => {
  pageActive = true
  const requested = requestedHistoryState()
  await Promise.all([
    loadHistory(requested.page, false, { pageSize: requested.pageSize }),
    loadCustomDefinitions(),
  ])
  routeStateReady = true
  if (pageActive && typeof route.query.job === 'string' && route.query.job) {
    await pollStatus(route.query.job)
  }
})
onBeforeUnmount(() => {
  pageActive = false
  stopPolling()
  historySequence += 1
  historyController?.abort()
  historyController = null
  loadingHistory.value = false
})
</script>

<template>
  <div class="management-page report-center-page">
    <ManagementPageHero
      eyebrow="REPORT GENERATION"
      :title="title"
      :description="description"
      icon="file"
      status-label="生成管线"
      status-value="HTML / PDF"
      status-note="查询、统计、图表与归档使用同一口径"
      tone="teal"
    />

  <section class="panel form-panel">
    <div class="panel-title">
      <div><span>01</span><h2>报表条件</h2></div>
      <p>{{ fixedReportType ? '当前页面已固定报表类型' : '三类标准报表可在各自页面按自定义区间查询并导出' }}</p>
    </div>
    <ReportForm
      :loading="generating"
      :projects="projects"
      :ships="ships"
      :custom-definitions="customDefinitions"
      :initial-report-type="fixedReportType || 'leadership'"
      :locked-report-type="Boolean(fixedReportType)"
      @submit="generate"
      @date-change="loadProjects"
      @project-change="loadShips"
    />
  </section>

  <div v-if="error" class="global-error" role="alert"><strong>操作未完成</strong><span>{{ error }}，请检查报表条件后重试。</span><button type="button" @click="error = ''">关闭</button></div>
  <ReportJobStatus :job="currentJob" />

  <section class="panel history-panel">
    <div class="panel-title"><div><span>02</span><h2>最近生成报告</h2></div><button class="text-button" :disabled="loadingHistory" @click="loadHistory(historyPage, true)">刷新列表</button></div>
    <ReportTable
      :items="history"
      :loading="loadingHistory"
      :page="historyPage"
      :page-size="historyPageSize"
      :total="historyTotal"
      @page-change="changeHistoryPage"
      @page-size-change="changeHistoryPageSize"
    />
  </section>
  </div>
</template>
