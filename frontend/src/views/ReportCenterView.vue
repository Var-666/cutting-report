<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import ReportForm from '../components/ReportForm.vue'
import ReportJobStatus from '../components/ReportJobStatus.vue'
import ReportTable from '../components/ReportTable.vue'
import {
  createReport,
  getReportStatus,
  listProjects,
  listPublishedDefinitions,
  listReports,
  listShips,
} from '../api/reports'

const props = defineProps({
  fixedReportType: { type: String, default: '' },
  title: { type: String, default: '报表生成与历史' },
  description: { type: String, default: '领导、车间、设备及自定义报表统一生成、归档和下载。' },
})

const route = useRoute()
const router = useRouter()
const currentJob = ref(null)
const history = ref([])
const projects = ref([])
const ships = ref([])
const customDefinitions = ref([])
const generating = ref(false)
const loadingHistory = ref(false)
const error = ref('')
const historyPage = ref(1)
const historyPageSize = ref(10)
const historyTotal = ref(0)
const historyTotalPages = computed(() => Math.max(1, Math.ceil(historyTotal.value / historyPageSize.value)))
let pollTimer = null

async function loadHistory(page = historyPage.value, force = false) {
  loadingHistory.value = true
  try {
    const data = await listReports({ page, pageSize: historyPageSize.value, force })
    history.value = data.items
    historyPage.value = data.page
    historyPageSize.value = data.page_size
    historyTotal.value = data.total
  } catch (exception) {
    error.value = exception.message
  } finally {
    loadingHistory.value = false
  }
}

function changeHistoryPage(page) {
  const target = Math.min(Math.max(Number(page), 1), historyTotalPages.value)
  if (target !== historyPage.value) loadHistory(target)
}

function changeHistoryPageSize(pageSize) {
  historyPageSize.value = Number(pageSize)
  loadHistory(1)
}

async function loadProjects(reportDate) {
  if (!reportDate) return
  try {
    projects.value = (await listProjects(reportDate)).items
    ships.value = (await listShips(reportDate, '')).items
  } catch (exception) {
    error.value = exception.message
  }
}

async function loadShips({ reportDate, projectCode }) {
  if (!reportDate) return
  try {
    ships.value = (await listShips(reportDate, projectCode)).items
  } catch (exception) {
    error.value = exception.message
  }
}

async function loadCustomDefinitions() {
  try {
    customDefinitions.value = (await listPublishedDefinitions()).items
  } catch (exception) {
    error.value = exception.message
  }
}

async function generate(form) {
  window.clearTimeout(pollTimer)
  error.value = ''
  generating.value = true
  try {
    currentJob.value = await createReport(form)
    await router.replace({ query: { ...route.query, job: currentJob.value.report_id } })
    await pollStatus(currentJob.value.report_id)
  } catch (exception) {
    error.value = exception.message
    generating.value = false
  }
}

async function pollStatus(reportId) {
  try {
    currentJob.value = await getReportStatus(reportId)
    const terminal = ['completed', 'completed_with_warnings', 'failed'].includes(currentJob.value.status)
    if (terminal) {
      generating.value = false
      await loadHistory(1, true)
      return
    }
    generating.value = true
    pollTimer = window.setTimeout(() => pollStatus(reportId), 1000)
  } catch (exception) {
    error.value = exception.message
    generating.value = false
  }
}

watch(
  () => route.query.job,
  (reportId) => {
    if (typeof reportId === 'string' && reportId && reportId !== currentJob.value?.report_id) {
      window.clearTimeout(pollTimer)
      pollStatus(reportId)
    }
  },
)

onMounted(async () => {
  await Promise.all([loadHistory(1), loadCustomDefinitions()])
  if (typeof route.query.job === 'string' && route.query.job) {
    await pollStatus(route.query.job)
  }
})
onBeforeUnmount(() => window.clearTimeout(pollTimer))
</script>

<template>
  <section class="page-hero hero">
    <div>
      <span class="eyebrow">REPORT GENERATION</span>
      <h2>{{ title }}</h2>
      <p>{{ description }}</p>
    </div>
    <div class="hero-badge"><strong>SQL</strong><i></i><strong>统计</strong><i></i><strong>图表</strong><i></i><strong>HTML</strong></div>
  </section>

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

  <div v-if="error" class="global-error"><strong>操作未完成</strong><span>{{ error }}</span><button @click="error = ''">关闭</button></div>
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
</template>
