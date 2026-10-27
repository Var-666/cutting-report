<script setup lang="ts">
import type { AlarmIncident, AlarmIncidentSummary, AlarmReport, AlarmStatus } from '../types/alarms'

import { toError } from '../utils/errors'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { getAlarmStatus, listAlarmIncidentPage, listAlarmReportPage, listAlarmReports } from '../api/reports'
import heroImage from '../assets/home-laser-cutting.webp'
import AppIcon from '../components/AppIcon.vue'
import { alarmConfidencePercent, latestModelAlarmReport } from '../utils/alarmAnalysis.ts'
import { formatChineseDateTime } from '../utils/dateDisplay.ts'
import { formatDeviceDisplay } from '../utils/deviceDisplay.ts'

const status = ref<AlarmStatus | null>(null)
const incidents = ref<AlarmIncident[]>([])
const reports = ref<AlarmReport[]>([])
const reportItems = ref<AlarmReport[]>([])
const loading = ref(false)
const error = ref('')
const activeSeverity = ref('all')
const onlyOpen = ref(false)
const incidentPage = ref(1)
const incidentPageSize = 15
const reportPage = ref(1)
const reportPageSize = 10
const reportSummary = ref({ total: 0, completed_count: 0, page_count: 1 })
const incidentSummary = ref<AlarmIncidentSummary>({
  total: 0,
  all_total: 0,
  open_count: 0,
  page_count: 1,
  severity_counts: { critical: 0, high: 0, warning: 0 },
  dominant_alarm: null,
})
const currentTime = ref(new Date())
let refreshTimer: number | null = null
let clockTimer: number | null = null
let requestController: AbortController | null = null
let requestSequence = 0
let pageActive = false

const openCount = computed(() => incidentSummary.value.open_count || 0)
const completedCount = computed(() => reportSummary.value.completed_count || 0)
const severityLabels: Record<string, string> = { warning: '提示', high: '一般', critical: '严重' }
const reportStatusLabels: Record<string, string> = { generating: '生成中', completed: '已生成', failed: '生成失败' }
const monitorReady = computed(() => Boolean(status.value?.enabled && status.value?.worker_running))
const machineLabel = computed(() => formatDeviceDisplay(status.value?.machine_nos?.join('、')) || '未配置设备')

const severityTabs = computed(() => [
  { value: 'all', label: '全部', count: incidentSummary.value.all_total || 0 },
  { value: 'critical', label: '严重', count: incidentSummary.value.severity_counts?.critical || 0 },
  { value: 'high', label: '一般', count: incidentSummary.value.severity_counts?.high || 0 },
  { value: 'warning', label: '提示', count: incidentSummary.value.severity_counts?.warning || 0 },
])

const filteredIncidents = computed(() => incidents.value.map((item) => ({
  ...item,
  reportDisplay: incidentReportDisplay(item),
})))
const incidentPageCount = computed(() => incidentSummary.value.page_count || 1)
const reportPageCount = computed(() => reportSummary.value.page_count || 1)
const latestAiReport = computed(() => latestModelAlarmReport(reports.value))
const latestAiAnalysis = computed(() => latestAiReport.value?.analysis || null)

function formatDateTime(value: unknown) {
  return formatChineseDateTime(value)
}

function formatShortDateTime(value: unknown) {
  if (!value) return '--'
  return formatDateTime(value).replace(/年|月/g, '-').replace('日', '')
}

function formatBytes(value: number | null | undefined) {
  if (!value) return '--'
  return value >= 1024 * 1024 ? `${(value / 1024 / 1024).toFixed(1)} MB` : `${Math.ceil(value / 1024)} KB`
}

function durationLabel(item: AlarmIncident) {
  const startValue = item?.first_start_time
  if (!startValue) return '--'
  const start = new Date(startValue).getTime()
  const endValue = item.status === 'closed' ? item.latest_remove_time : currentTime.value
  if (!endValue) return '--'
  const end = endValue instanceof Date ? endValue.getTime() : new Date(endValue).getTime()
  if (!Number.isFinite(start) || !Number.isFinite(end)) return '--'
  const minutes = Math.max(0, Math.floor((end - start) / 60_000))
  if (minutes < 60) return `${minutes || 1} 分钟`
  const hours = Math.floor(minutes / 60)
  const remain = minutes % 60
  return hours < 24 ? `${hours} 小时 ${remain} 分钟` : `${Math.floor(hours / 24)} 天 ${hours % 24} 小时`
}

function possibleCause(item: AlarmIncident) {
  return item.alarm_class || item.alarm_desc || '等待进一步分析'
}

function reportName(item: AlarmReport) {
  return formatDeviceDisplay(item.pdf_filename || `${item.machine_no || '设备'}_告警报告_${String(item.created_at || '').slice(0, 10).replaceAll('-', '')}.pdf`)
}

function incidentReportDisplay(item: AlarmIncident) {
  if (!item.pdf_report_id) return { label: '待生成', title: '尚未关联报告，等待系统生成', state: 'pending' }
  if (!item.report_status) return { label: '报告不存在', title: '关联的报告记录不存在', state: 'unavailable' }
  if (item.report_status === 'completed') {
    return item.report_view_url
      ? { label: '查看', url: item.report_view_url, title: '查看已生成的告警报告', state: 'completed' }
      : { label: '报告不可用', title: '报告记录已完成，但尚无可用的查看文件', state: 'unavailable' }
  }
  return {
    label: reportStatusLabels[item.report_status] || '状态未知',
    title: item.report_error_message || (item.report_status === 'generating' ? '报告正在生成，完成后可查看' : '请查看下方实体告警报告记录'),
    state: item.report_status,
  }
}

function evidenceLabel(ids: unknown) {
  return (Array.isArray(ids) ? ids : []).map((id) => `[${id}]`).join('、') || '未提供'
}

function priorityLabel(value: unknown) {
  const number = Number(value)
  return Number.isFinite(number) ? `优先级 ${number}` : '建议措施'
}

function relativeSyncTime(value: string | null | undefined) {
  if (!value) return '尚未完成检测'
  const diff = Math.max(0, currentTime.value.getTime() - new Date(value).getTime())
  const minutes = Math.floor(diff / 60_000)
  if (minutes < 1) return '刚刚完成'
  if (minutes < 60) return `${minutes} 分钟前`
  return `${Math.floor(minutes / 60)} 小时前`
}

async function selectSeverity(value: string) {
  if (loading.value || activeSeverity.value === value) return
  activeSeverity.value = value
  incidentPage.value = 1
  incidents.value = []
  await loadData()
}

async function toggleOpenFilter() {
  if (loading.value) return
  onlyOpen.value = !onlyOpen.value
  incidentPage.value = 1
  incidents.value = []
  await loadData()
}

async function changeIncidentPage(value: number) {
  if (loading.value || value < 1 || value > incidentPageCount.value) return
  incidentPage.value = value
  await loadData()
}

async function changeReportPage(value: number) {
  if (loading.value || value < 1 || value > reportPageCount.value) return
  reportPage.value = value
  await loadData()
}

function stopRefreshTimer() {
  if (!refreshTimer) return
  window.clearTimeout(refreshTimer)
  refreshTimer = null
}

function scheduleRefresh() {
  stopRefreshTimer()
  if (!pageActive) return
  refreshTimer = window.setTimeout(async () => {
    refreshTimer = null
    await loadData()
    scheduleRefresh()
  }, 60_000)
}

async function loadData() {
  if (!pageActive || requestController) return
  const controller = new AbortController()
  const sequence = ++requestSequence
  requestController = controller
  loading.value = true
  error.value = ''
  try {
    const [statusData, incidentData, reportData, reportPageData] = await Promise.all([
      getAlarmStatus({ signal: controller.signal }),
      listAlarmIncidentPage({
        status: onlyOpen.value ? 'open' : '',
        severity: activeSeverity.value === 'all' ? '' : activeSeverity.value,
        page: incidentPage.value,
        pageSize: incidentPageSize,
      }, { signal: controller.signal }),
      listAlarmReports({ limit: 50 }, { signal: controller.signal }),
      listAlarmReportPage({ page: reportPage.value, pageSize: reportPageSize }, { signal: controller.signal }),
    ])
    if (!pageActive || controller.signal.aborted || sequence !== requestSequence) return
    status.value = statusData
    incidents.value = incidentData.items
    incidentPage.value = incidentData.page
    incidentSummary.value = incidentData
    reports.value = reportData
    reportItems.value = reportPageData.items
    reportPage.value = reportPageData.page
    reportSummary.value = reportPageData
    currentTime.value = new Date()
  } catch (caught) {
    const exception = toError(caught)
    if (exception.name !== 'AbortError' && pageActive && sequence === requestSequence) {
      error.value = exception.message
    }
  } finally {
    if (sequence === requestSequence) {
      requestController = null
      loading.value = false
    }
  }
}

onMounted(async () => {
  pageActive = true
  await loadData()
  if (!pageActive) return
  scheduleRefresh()
  clockTimer = window.setInterval(() => { currentTime.value = new Date() }, 30_000)
})
onBeforeUnmount(() => {
  pageActive = false
  stopRefreshTimer()
  window.clearInterval(clockTimer ?? undefined)
  clockTimer = null
  requestSequence += 1
  requestController?.abort()
  requestController = null
  loading.value = false
})
</script>

<template>
  <div class="alarm-page">
    <section class="page-hero report-hero alarm-hero" :style="{ '--alarm-hero-image': `url(${heroImage})` }">
      <div class="alarm-hero__copy">
        <p class="eyebrow">INTELLIGENT ALARM CENTER</p>
        <h1>智能告警中心</h1>
        <p>按设定周期自动检测切割机运行状态，发现新的异常问题时生成实体 PDF 报告。</p>
      </div>
      <p class="alarm-hero__slogan"><strong>智能监控预警</strong><span>防患于未然</span></p>
      <div class="monitor-card" :class="{ ready: monitorReady }">
        <div class="monitor-card__head">
          <span class="monitor-pulse"><AppIcon :name="monitorReady ? 'check' : 'alert'" size="17" /></span>
          <p><small>监控设备状态</small><strong>{{ status?.enabled ? (status.worker_running ? '运行中' : '等待启动') : '未启用' }}</strong></p>
        </div>
        <dl>
          <div><dt>设备</dt><dd>{{ machineLabel }}</dd></div>
          <div><dt>统计起点</dt><dd>{{ formatDateTime(status?.monitor_start_at) }}</dd></div>
          <div><dt>最近检测</dt><dd>{{ formatDateTime(status?.last_success_at) }}</dd></div>
        </dl>
      </div>
    </section>

    <div v-if="error" class="global-error"><strong>告警数据读取失败</strong><span>{{ error }}</span><button @click="loadData">重试</button></div>
    <div v-if="status && !status.enabled" class="alarm-notice"><AppIcon name="alert" size="18" />告警监控尚未启用。请先在部署配置中开启监控服务。</div>

    <section class="leadership-kpis alarm-kpis" aria-label="告警监控概况">
      <article class="alarm-kpi kpi-sky">
        <span class="alarm-kpi__icon"><AppIcon name="factory" size="22" tone="muted" /></span>
        <div><span>监控设备</span><p><strong>{{ status?.machine_nos?.length || 0 }}</strong> 台</p><em>{{ machineLabel }}</em></div>
      </article>
      <article class="alarm-kpi kpi-red">
        <span class="alarm-kpi__icon"><AppIcon name="alert" size="22" /></span>
        <div><span>当前告警</span><p><strong>{{ openCount }}</strong> 项</p><em :class="{ safe: !openCount }">{{ openCount ? `严重 ${severityTabs[1].count} 项` : '状态正常' }}</em></div>
      </article>
      <article class="alarm-kpi kpi-indigo">
        <span class="alarm-kpi__icon"><AppIcon name="database" size="22" tone="muted" /></span>
        <div><span>同步游标</span><p><strong>{{ status?.last_alarm_id || 0 }}</strong></p><em>ALARM_INFO_ID</em></div>
      </article>
      <article class="alarm-kpi kpi-teal alarm-kpi--wide">
        <span class="alarm-kpi__icon"><AppIcon name="calendar" size="22" tone="muted" /></span>
        <div><span>最近检测时间</span><p><strong>{{ formatShortDateTime(status?.last_success_at) }}</strong></p><em>{{ relativeSyncTime(status?.last_success_at) }}</em></div>
      </article>
      <article class="alarm-kpi kpi-green">
        <span class="alarm-kpi__icon"><AppIcon name="file" size="22" tone="muted" /></span>
        <div><span>已生成报告</span><p><strong>{{ completedCount }}</strong> 份</p><em>实体 PDF 报告</em></div>
      </article>
    </section>

    <div class="alarm-content-grid">
      <section class="panel alarm-list">
        <div class="alarm-section-head">
          <div class="alarm-section-title"><span class="section-icon danger"><AppIcon name="bell" size="18" /></span><h2>告警事件<small>ALARM EVENTS</small></h2></div>
          <div class="alarm-tabs" role="tablist" aria-label="按告警级别筛选">
            <button v-for="tab in severityTabs" :key="tab.value" type="button" role="tab" :aria-selected="activeSeverity === tab.value" :class="{ active: activeSeverity === tab.value }" :disabled="loading" @click="selectSeverity(tab.value)">{{ tab.label }}（{{ tab.count }}）</button>
          </div>
          <button class="alarm-status-filter" :class="{ active: onlyOpen }" type="button" :aria-pressed="onlyOpen" :disabled="loading" @click="toggleOpenFilter"><AppIcon name="alert" size="14" />当前异常（{{ openCount }}）</button>
          <button class="alarm-refresh-button" type="button" :disabled="loading" :aria-busy="loading" @click="loadData">
            <span v-if="loading" class="spinner dark" aria-hidden="true"></span><AppIcon v-else name="activity" size="15" />{{ loading ? '刷新中' : '刷新数据' }}
          </button>
        </div>
        <div v-if="loading && !incidents.length" class="alarm-empty-state" role="status">
          <span class="spinner dark" aria-hidden="true"></span><strong>正在读取告警事件</strong>
        </div>
        <div v-else-if="!filteredIncidents.length" class="alarm-empty-state" role="status">
          <span class="empty-icon"><AppIcon name="check" size="24" /></span><strong>当前筛选下暂无告警</strong><span>{{ onlyOpen ? '当前没有状态异常的告警，系统将持续监控。' : activeSeverity === 'all' ? '当前设备暂无告警记录，系统将持续监控。' : '当前级别暂无告警，系统将持续监控切割机运行状态。' }}</span>
        </div>
        <div v-else class="alarm-table-wrap">
          <table v-content-table aria-label="切割机告警事件" data-content-columns="center left left left left right center left center actions">
            <thead><tr><th>告警级别</th><th>设备名称</th><th>异常类型</th><th>告警时间</th><th>发现时间</th><th>持续时长</th><th>当前状态</th><th>可能原因</th><th>处理状态</th><th>操作</th></tr></thead>
            <tbody><tr v-for="item in filteredIncidents" :key="item.incident_id">
              <td><span class="severity-badge" :class="item.severity"><span>!</span>{{ severityLabels[item.severity] || item.severity }}</span></td>
              <td>{{ formatDeviceDisplay(item.machine_no) || '--' }}</td>
              <td><strong class="alarm-name">{{ item.alarm_desc || '--' }}</strong><small>{{ item.alarm_code || item.alarm_type || '--' }}</small></td>
              <td>{{ formatShortDateTime(item.first_start_time) }}</td>
              <td>{{ formatShortDateTime(item.first_seen_at) }}</td>
              <td>{{ durationLabel(item) }}</td>
              <td><span class="live-state" :class="item.status"><i></i>{{ item.status === 'open' ? '异常' : '正常' }}</span></td>
              <td class="cause-cell">{{ possibleCause(item) }}</td>
              <td><span class="incident-state" :class="item.status">{{ item.status === 'open' ? '待处理' : '已处理' }}</span></td>
              <td class="report-actions"><a v-if="item.reportDisplay.url" :href="item.reportDisplay.url" :title="item.reportDisplay.title" target="_blank" rel="noopener">{{ item.reportDisplay.label }}</a><span v-else class="report-status" :class="item.reportDisplay.state" :title="item.reportDisplay.title">{{ item.reportDisplay.label }}</span></td>
            </tr></tbody>
          </table>
        </div>
        <div v-if="incidentSummary.total > 0" class="alarm-pagination app-pagination" aria-label="告警事件分页">
          <div class="app-pagination__actions">
            <button type="button" :disabled="loading || incidentPage <= 1" @click="changeIncidentPage(incidentPage - 1)">上一页</button>
            <span class="app-pagination__summary">第 {{ incidentPage }} / {{ incidentPageCount }} 页，共 {{ incidentSummary.total }} 条</span>
            <button type="button" :disabled="loading || incidentPage >= incidentPageCount" @click="changeIncidentPage(incidentPage + 1)">下一页</button>
          </div>
        </div>
      </section>

      <aside class="alarm-insights alarm-ai-panel" aria-labelledby="alarm-insights-title">
        <header>
          <div class="alarm-section-title"><span class="section-icon"><AppIcon name="sparkles" size="18" /></span><h2 id="alarm-insights-title">AI 告警分析<small>MODEL ANALYSIS</small></h2></div>
          <span v-if="latestAiReport">{{ formatShortDateTime(latestAiReport.completed_at || latestAiReport.created_at) }}</span>
        </header>
        <div v-if="loading && !reports.length" class="alarm-ai-empty" role="status"><span class="spinner dark" aria-hidden="true"></span><strong>正在读取模型分析</strong></div>
        <template v-else-if="latestAiAnalysis">
          <div class="alarm-ai-summary">
            <div><span class="alarm-ai-risk" :class="latestAiAnalysis.overall_risk">{{ severityLabels[latestAiAnalysis.overall_risk] || latestAiAnalysis.overall_risk }}</span><strong>最新模型结论</strong></div>
            <p>{{ formatDeviceDisplay(latestAiAnalysis.summary) }}</p>
            <a v-if="latestAiReport.view_url" :href="latestAiReport.view_url" target="_blank" rel="noopener">查看完整报告</a>
          </div>

          <div v-if="latestAiAnalysis.findings.length || latestAiAnalysis.actions.length" class="alarm-ai-columns">
            <section v-if="latestAiAnalysis.findings.length" class="alarm-ai-section" aria-labelledby="alarm-ai-findings-title">
              <h3 id="alarm-ai-findings-title">分析发现</h3>
              <article v-for="(finding, index) in latestAiAnalysis.findings" :key="`${finding.title}-${index}`" class="alarm-ai-finding" :class="finding.severity">
                <header><strong>{{ formatDeviceDisplay(finding.title) }}</strong><span v-if="alarmConfidencePercent(finding.confidence) !== null">置信度 {{ alarmConfidencePercent(finding.confidence) }}%</span></header>
                <p>{{ formatDeviceDisplay(finding.analysis) }}</p>
                <p v-if="finding.possible_causes?.length" class="alarm-ai-causes"><strong>可能原因</strong>{{ formatDeviceDisplay(finding.possible_causes.join('；')) }}</p>
                <small>依据 {{ evidenceLabel(finding.evidence_ids) }}</small>
              </article>
            </section>

            <section v-if="latestAiAnalysis.actions.length" class="alarm-ai-section alarm-ai-actions" aria-labelledby="alarm-ai-actions-title">
              <h3 id="alarm-ai-actions-title">建议措施</h3>
              <ol><li v-for="(action, index) in latestAiAnalysis.actions" :key="`${action.priority}-${index}`"><span>{{ priorityLabel(action.priority) }}</span><p>{{ formatDeviceDisplay(action.action) }}</p><small>依据 {{ evidenceLabel(action.evidence_ids) }}</small></li></ol>
            </section>
          </div>

          <details v-if="latestAiAnalysis.facts.length" class="alarm-ai-evidence">
            <summary>查看证据记录（{{ latestAiAnalysis.facts.length }} 条）</summary>
            <article v-for="fact in latestAiAnalysis.facts" :key="fact.id">
              <strong>[{{ fact.id }}] {{ formatDeviceDisplay(fact.alarm_desc || fact.alarm_class) }}</strong>
              <span>{{ formatDeviceDisplay(fact.machine_no) }} · {{ severityLabels[fact.severity] || fact.severity }} · {{ fact.alarm_code || '无编码' }}</span>
              <small>{{ formatDateTime(fact.start_time) }} · {{ fact.status === 'open' ? '未解除' : '已解除' }}</small>
            </article>
          </details>

          <section v-if="latestAiAnalysis.limitations.length" class="alarm-ai-limitations" aria-labelledby="alarm-ai-limitations-title">
            <h3 id="alarm-ai-limitations-title">分析边界</h3>
            <ul><li v-for="item in latestAiAnalysis.limitations" :key="item">{{ formatDeviceDisplay(item) }}</li></ul>
          </section>
          <p class="alarm-ai-disclaimer">模型结论仅用于辅助排查，安全处置以现场规程和授权人员判断为准。</p>
        </template>
        <div v-else class="alarm-ai-empty" role="status"><span class="empty-icon"><AppIcon name="sparkles" size="22" /></span><strong>暂无可展示的模型分析</strong><span>{{ reports.some((item) => item.status === 'generating') ? '新的告警报告正在生成，完成后会在这里展示。' : '发现新告警并成功生成智能报告后，会在这里展示结论、证据和置信度。' }}</span></div>
      </aside>
    </div>

    <section class="panel alarm-list">
      <div class="alarm-section-head report-head">
        <div class="alarm-section-title"><span class="section-icon"><AppIcon name="file" size="18" /></span><h2>实体告警报告<small>ALARM REPORTS</small></h2></div>
        <span class="report-count">共 {{ reportSummary.total }} 份</span>
      </div>
      <div v-if="loading && !reportItems.length" class="alarm-empty-state" role="status">
        <span class="spinner dark" aria-hidden="true"></span><strong>正在读取实体告警报告</strong>
      </div>
      <div v-else-if="!reportItems.length" class="alarm-empty-state" role="status">
        <span class="empty-icon"><AppIcon name="file" size="24" /></span><strong>暂无实体告警报告</strong><span>发现新的告警问题后，系统将在这里生成对应的 PDF 报告。</span>
      </div>
      <div v-else class="alarm-table-wrap report-table-wrap">
        <table v-content-table aria-label="实体告警报告" data-content-columns="left left left right center actions">
          <thead><tr><th>报告名称</th><th>关联事件</th><th>生成时间</th><th>文件大小</th><th>状态</th><th>操作</th></tr></thead>
          <tbody><tr v-for="item in reportItems" :key="item.report_id">
            <td><span class="report-name"><AppIcon name="file" size="16" tone="red" />{{ reportName(item) }}</span></td>
            <td>{{ item.incident_ids.length }} 项 · {{ formatDeviceDisplay(item.machine_no) || '--' }}</td>
            <td>{{ formatShortDateTime(item.created_at) }}</td>
            <td>{{ formatBytes(item.file_size) }}</td>
            <td><span class="report-status" :class="item.status">{{ reportStatusLabels[item.status] || item.status }}</span></td>
            <td class="report-actions"><template v-if="item.status === 'completed'"><a :href="item.download_url || undefined">下载</a><a :href="item.view_url || undefined" target="_blank" rel="noopener">预览</a></template><span v-else :title="item.error_message || ''">{{ item.error_message || '--' }}</span></td>
          </tr></tbody>
        </table>
      </div>
      <div v-if="reportSummary.total > 0" class="alarm-pagination app-pagination" aria-label="实体告警报告分页">
        <div class="app-pagination__actions">
          <button type="button" :disabled="loading || reportPage <= 1" @click="changeReportPage(reportPage - 1)">上一页</button>
          <span class="app-pagination__summary">第 {{ reportPage }} / {{ reportPageCount }} 页，共 {{ reportSummary.total }} 份</span>
          <button type="button" :disabled="loading || reportPage >= reportPageCount" @click="changeReportPage(reportPage + 1)">下一页</button>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.alarm-content-grid>.panel{margin-bottom:0}
.alarm-status-filter{min-height:32px;display:inline-flex;align-items:center;gap:5px;padding:0 10px;color:var(--text-color-3);border:1px solid var(--theme-border-color);border-radius:7px;background:rgb(var(--container-bg-color));font-size:var(--font-auxiliary);font-weight:600}.alarm-status-filter:hover:not(:disabled){background:var(--theme-hover-color)}.alarm-status-filter.active{color:var(--theme-on-primary-color);border-color:var(--alarm-red);background:var(--alarm-red)}
.alarm-page{--alarm-navy:var(--text-color-1);--alarm-blue:rgb(var(--info-color));--alarm-teal:rgb(var(--info-color));--alarm-green:rgb(var(--success-color));--alarm-amber:rgb(var(--warning-color));--alarm-red:rgb(var(--error-color));--alarm-indigo:rgb(var(--primary-color));--alarm-line:var(--theme-border-color);display:grid;gap:14px;padding-bottom:2px;color:var(--alarm-navy)}.alarm-page>.panel{margin-bottom:0}.alarm-page>.global-error{margin-bottom:0}.alarm-hero{position:relative;min-height:165px;display:flex;flex-wrap:wrap;align-items:center;gap:24px;overflow:hidden;padding:24px 28px;border:1px solid var(--theme-border-color);border-radius:14px;background:linear-gradient(90deg,rgb(var(--container-bg-color) / 0.98) 0%,rgb(var(--container-bg-color) / 0.94) 43%,rgb(var(--container-bg-color) / 0.06) 72%),var(--alarm-hero-image) center 46%/cover no-repeat;box-shadow:var(--theme-shadow);animation:surface-in .46s cubic-bezier(.2,.75,.25,1) both}.alarm-hero::after{content:"";position:absolute;inset:0;pointer-events:none;background:transparent}.alarm-hero__copy,.alarm-hero__slogan,.monitor-card{position:relative;z-index:1}.alarm-hero__copy{flex:1 1 320px;min-width:0}.alarm-hero .eyebrow{margin:0;color:var(--alarm-blue);font-size:var(--font-auxiliary);font-weight:700;letter-spacing:1.4px}.alarm-hero h1{margin:6px 0;color:var(--text-color-1);font-size:var(--font-page-title);line-height:1.1;letter-spacing:1px}.alarm-hero__copy>p:last-child{max-width:610px;margin:0;color:var(--text-color-3);font-size:13px;line-height:1.7}.alarm-hero__slogan{flex:0 1 170px;align-self:center;margin:0;color:var(--text-color-1);text-align:center;text-shadow:none;transform:rotate(-7deg)}.alarm-hero__slogan strong,.alarm-hero__slogan span{display:block}.alarm-hero__slogan strong{font-size:17px;letter-spacing:3px}.alarm-hero__slogan span{margin-top:6px;font-size:13px;letter-spacing:5px}.monitor-card{flex:1 1 260px;min-width:0;max-width:320px;padding:15px 17px;border:1px solid var(--theme-border-color);border-radius:11px;background:rgb(var(--container-bg-color));box-shadow:var(--theme-shadow);transition:transform .2s ease,box-shadow .2s ease}.monitor-card:hover{transform:translateY(-2px);box-shadow:var(--theme-shadow)}.monitor-card__head{display:flex;align-items:center;gap:10px;padding-bottom:9px;border-bottom:1px solid var(--theme-border-color)}.monitor-card__head p{min-width:0;margin:0;overflow-wrap:anywhere}.monitor-card__head small,.monitor-card__head strong{display:block}.monitor-card__head small{color:var(--text-color-3);font-size:var(--font-auxiliary)}.monitor-card__head strong{margin-top:2px;color:var(--text-color-3);font-size:17px}.monitor-card.ready .monitor-card__head strong{color:rgb(var(--success-color))}.monitor-pulse{flex-shrink:0;width:32px;height:32px;display:grid;place-items:center;color:var(--alarm-red);border-radius:50%;background:rgb(var(--error-color) / .1);box-shadow:var(--theme-shadow)}.monitor-card.ready .monitor-pulse{background:rgb(var(--success-color) / .1);box-shadow:var(--theme-shadow)}.monitor-card dl{margin:6px 0 0}.monitor-card dl div{display:grid;grid-template-columns:55px minmax(0,1fr);gap:6px;padding-top:6px;font-size:var(--font-auxiliary)}.monitor-card dt{color:var(--text-color-3)}.monitor-card dd{min-width:0;margin:0;color:var(--text-color-2);white-space:normal;overflow-wrap:anywhere}.alarm-notice{display:flex;align-items:center;gap:9px;padding:11px 14px;border:1px solid var(--theme-border-color);border-radius:8px;background:rgb(var(--warning-color) / .1);color:var(--text-color-1);font-size:12px}.alarm-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,240px),1fr));gap:16px;margin-bottom:0;margin:16px 0}.alarm-kpi{--kpi-color:var(--alarm-blue);--kpi-soft:rgb(var(--info-color) / .1);position:relative;min-width:0;min-height:116px;padding:12px;border:1px solid var(--alarm-line);border-top:2px solid var(--kpi-color);border-radius:11px;background:rgb(var(--container-bg-color));box-shadow:var(--theme-shadow);transition:transform .18s ease,box-shadow .18s ease;animation:surface-in .42s cubic-bezier(.2,.75,.25,1) both;display:grid;grid-template-columns:35px minmax(0,1fr);align-items:center;gap:8px 12px}.alarm-kpi:hover{transform:translateY(-2px);box-shadow:var(--theme-shadow)}.alarm-kpi:nth-child(2){animation-delay:.04s}.alarm-kpi:nth-child(3){animation-delay:.08s}.alarm-kpi:nth-child(4){animation-delay:.12s}.alarm-kpi:nth-child(5){animation-delay:.16s}.kpi-red{--kpi-color:var(--alarm-red);--kpi-soft:rgb(var(--error-color) / .1)}.kpi-indigo{--kpi-color:var(--alarm-indigo);--kpi-soft:rgb(var(--primary-color) / .1)}.kpi-teal{--kpi-color:var(--alarm-teal);--kpi-soft:rgb(var(--info-color) / .1)}.kpi-green{--kpi-color:var(--alarm-green);--kpi-soft:rgb(var(--success-color) / .1)}.alarm-kpi__icon{position:relative;z-index:1;width:34px;height:34px;display:grid;place-items:center;flex:0 0 auto;color:var(--kpi-color);border-radius:50%;background:var(--kpi-soft);transition:transform .2s ease}.alarm-kpi:hover .alarm-kpi__icon{transform:scale(1.08) rotate(-3deg)}.alarm-kpi>div{display:contents}.alarm-kpi>div>span{display:block;color:var(--text-color-1);font-size:var(--font-auxiliary);font-weight:700;white-space:normal;overflow-wrap:anywhere;grid-column:2}.alarm-kpi p{margin:0;color:var(--text-color-1);font-size:var(--font-auxiliary);grid-column:1/-1;min-width:0;overflow-wrap:anywhere}.alarm-kpi p strong{font-size:25px;font-variant-numeric:tabular-nums}.alarm-kpi--wide p strong{font-size:16px}.alarm-kpi em{position:relative;z-index:1;display:block;color:var(--muted);font-style:normal;font-size:var(--font-auxiliary);grid-column:1/-1;min-width:0;white-space:normal;overflow-wrap:anywhere;line-height:1.6}.alarm-kpi.kpi-red em{color:var(--alarm-red)}.alarm-kpi.kpi-red em.safe,.alarm-kpi.kpi-green em,.alarm-kpi.kpi-teal em{color:var(--alarm-green)}.alarm-content-grid{display:grid;grid-template-columns:minmax(0,1fr);align-items:start;gap:14px}.alarm-list,.alarm-insights{min-width:0;overflow:hidden;border:1px solid var(--alarm-line);border-radius:11px;background:rgb(var(--container-bg-color));box-shadow:var(--theme-shadow);animation:surface-in .46s cubic-bezier(.2,.75,.25,1) both}.alarm-section-head,.alarm-insights>header{min-height:52px;display:flex;align-items:center;gap:16px;padding:7px 15px;border-bottom:1px solid var(--theme-border-color);background:var(--panel-heading-bg)}.alarm-section-title{display:flex;align-items:center;gap:8px;flex:0 0 auto}.alarm-section-title h2{margin:0;color:var(--text-color-1);font-size:14px}.alarm-section-title h2 small{margin-left:8px;color:var(--text-color-3);font-size:var(--font-auxiliary);font-weight:500;letter-spacing:.8px}.section-icon{width:28px;height:28px;display:grid;place-items:center;color:var(--alarm-teal);border-radius:8px;background:rgb(var(--info-color) / .1)}.section-icon.danger{color:var(--alarm-red);background:rgb(var(--error-color) / .1)}.alarm-tabs{display:flex;align-items:center;gap:5px;margin-left:4px}.alarm-tabs button{min-height:31px;padding:0 14px;border:1px solid transparent;border-radius:7px;color:var(--text-color-3);background:rgb(var(--primary-color) / .1);font-size:var(--font-auxiliary);transition:color .18s ease,background .18s ease,transform .18s ease}.alarm-tabs button:hover{transform:translateY(-1px);color:var(--alarm-blue);background:var(--theme-hover-color)}.alarm-tabs button.active{color:var(--theme-on-primary-color);border-color:rgb(var(--primary-color));background:rgb(var(--info-color));box-shadow:var(--theme-shadow)}.alarm-refresh-button{min-height:32px;display:inline-flex;align-items:center;gap:5px;margin-left:auto;padding:0 10px;color:var(--alarm-blue);border:1px solid var(--theme-border-color);border-radius:7px;background:rgb(var(--info-color) / .1);font-size:var(--font-auxiliary)}.alarm-refresh-button:hover:not(:disabled){background:var(--theme-hover-color)}.alarm-empty-state{min-height:116px;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:7px;padding:26px;text-align:center;background:rgb(var(--container-bg-color))}.alarm-empty-state strong{color:var(--text-color-2);font-size:14px}.alarm-empty-state>span:not(.spinner):not(.empty-icon){color:var(--text-color-3);font-size:var(--font-auxiliary);line-height:1.6}.empty-icon{width:38px;height:38px;display:grid;place-items:center;color:var(--alarm-green);border-radius:50%;background:rgb(var(--success-color) / .1)}.alarm-table-wrap{overflow-x:auto}.alarm-table-wrap table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}.alarm-table-wrap th,.alarm-table-wrap td{height:45px;padding:8px 10px;border-bottom:1px solid var(--theme-border-color);text-align:left;vertical-align:middle;white-space:nowrap}.alarm-table-wrap th{color:var(--text-color-1);background:var(--theme-table-header-color);font-size:var(--font-auxiliary);font-weight:600;letter-spacing:.2px}.alarm-table-wrap tbody tr{transition:background .15s ease}.alarm-table-wrap tbody tr:hover{background:rgb(var(--layout-bg-color))}.alarm-table-wrap td{color:var(--muted);font-size:var(--font-auxiliary)}.alarm-table-wrap td small{display:block;max-width:160px;margin-top:3px;overflow:hidden;color:var(--muted);text-overflow:ellipsis}.alarm-name{display:block;max-width:150px;overflow:hidden;color:var(--text-color-2);text-overflow:ellipsis;font-size:var(--font-auxiliary)}.severity-badge,.incident-state,.report-status{display:inline-flex;align-items:center;gap:5px;padding:4px 8px;border-radius:6px;font-size:var(--font-auxiliary);font-weight:600}.severity-badge>span{width:16px;height:16px;display:grid;place-items:center;color:var(--theme-on-primary-color);border-radius:50%;font-size:var(--font-auxiliary)}.severity-badge.critical{color:rgb(var(--error-color));background:rgb(var(--error-color) / .1)}.severity-badge.critical>span{background:rgb(var(--error-color))}.severity-badge.high{color:rgb(var(--warning-color));background:rgb(var(--warning-color) / .1)}.severity-badge.high>span{background:rgb(var(--warning-color))}.severity-badge.warning{color:rgb(var(--info-color));background:rgb(var(--info-color) / .1)}.severity-badge.warning>span{background:rgb(var(--info-color))}.live-state{display:inline-flex;align-items:center;gap:6px;color:var(--text-color-3)}.live-state i{width:8px;height:8px;border-radius:50%;background:rgb(var(--info-color));box-shadow:var(--theme-shadow)}.live-state.open i{background:rgb(var(--error-color) / .1);box-shadow:var(--theme-shadow)}.incident-state.open{color:rgb(var(--error-color));background:rgb(var(--error-color) / .1)}.incident-state.closed,.report-status.completed{color:rgb(var(--success-color));background:rgb(var(--success-color) / .1)}.report-status.generating{color:rgb(var(--warning-color));background:rgb(var(--warning-color) / .1)}.report-status.failed{color:rgb(var(--error-color));background:rgb(var(--error-color) / .1)}.cause-cell{max-width:150px;overflow:hidden;text-overflow:ellipsis}.report-actions{white-space:nowrap}.report-actions a{margin-right:12px;color:rgb(var(--info-color));text-decoration:none;font-weight:600}.report-actions a:hover{text-decoration:underline}.report-actions>span{display:inline-block;max-width:120px;overflow:hidden;color:var(--text-color-3);text-overflow:ellipsis;vertical-align:middle}.alarm-insights{padding-bottom:10px}.alarm-insights>header{justify-content:space-between}.alarm-insights>header>span{color:rgb(var(--info-color));font-size:var(--font-auxiliary)}.insight-card{display:flex;align-items:flex-start;gap:11px;margin:10px 12px 0;padding:14px;border-radius:8px;background:rgb(var(--info-color) / .1)}.insight-card>span{width:30px;height:30px;display:grid;place-items:center;flex:0 0 auto;color:var(--alarm-blue)}.insight-card p{min-width:0;margin:0}.insight-card strong,.insight-card small{display:block}.insight-card strong{color:rgb(var(--info-color));font-size:var(--font-auxiliary)}.insight-card small{margin-top:5px;color:var(--text-color-3);font-size:var(--font-auxiliary);line-height:1.6}.insight-card--red{background:rgb(var(--error-color) / .1)}.insight-card--red>span,.insight-card--red strong{color:var(--alarm-red)}.insight-card--amber{background:rgb(var(--warning-color) / .1)}.insight-card--amber>span,.insight-card--amber strong{color:var(--alarm-amber)}.report-head{justify-content:space-between}.report-count{color:rgb(var(--info-color));font-size:var(--font-auxiliary)}.report-table-wrap th,.report-table-wrap td{height:39px}.report-table-wrap th{background:var(--theme-table-header-color)}.report-name{display:inline-flex;align-items:center;gap:8px;color:var(--text-color-2)}.report-status{border-radius:999px}.global-error{border-radius:9px}
.alarm-content-grid .alarm-table-wrap th:last-child,.alarm-content-grid .alarm-table-wrap td:last-child{position:sticky;right:0;z-index:1;background:var(--theme-table-header-color);box-shadow:var(--theme-shadow)}.alarm-content-grid .alarm-table-wrap th:last-child{z-index:2;background:var(--theme-table-header-color)}.alarm-content-grid .alarm-table-wrap tbody tr:hover td:last-child{background:rgb(var(--layout-bg-color))}
.alarm-pagination{min-height:48px;display:flex;align-items:center;justify-content:space-between;gap:12px;padding:8px 15px;border-top:1px solid var(--theme-border-color);background:rgb(var(--container-bg-color));color:var(--text-color-3);font-size:var(--font-auxiliary)}.alarm-pagination>div{display:flex;gap:7px}.alarm-pagination button{min-height:30px;padding:0 11px;color:var(--alarm-blue);border:1px solid var(--theme-border-color);border-radius:6px;background:rgb(var(--container-bg-color))}.alarm-pagination button:disabled{color:var(--theme-text-disabled);background:var(--theme-hover-color);cursor:not-allowed}
@media(max-width:1180px){.alarm-hero__slogan{display:none}.alarm-kpi{padding:12px;display:grid;grid-template-columns:35px minmax(0,1fr);align-items:center;gap:8px 12px}.alarm-kpi__icon{width:30px;height:30px}.alarm-section-head{flex-wrap:wrap}.alarm-tabs{order:3;width:100%;margin:0}.alarm-refresh-button{margin-left:auto}.alarm-table-wrap table{min-width:960px}}
@media(max-width:900px){.alarm-page,.alarm-content-grid{gap:12px}.alarm-hero{min-height:200px;padding:24px}.alarm-hero__slogan{display:none}.alarm-insights{display:grid;grid-template-columns:repeat(3,1fr);padding-bottom:10px}.alarm-insights>header{grid-column:1/-1}.insight-card{margin:10px 0 0 10px}.insight-card:last-child{margin-right:10px}}
@media(max-width:620px){.alarm-hero{padding:22px 18px;background:linear-gradient(90deg,rgb(var(--container-bg-color) / 0.96),rgb(var(--container-bg-color) / 0.88)),var(--alarm-hero-image) center/cover}.alarm-hero h1{font-size:var(--font-page-title-mobile)}.alarm-hero__copy>p:last-child{font-size:12px}.monitor-card{width:100%;max-width:none;box-sizing:border-box}.alarm-section-head{position:relative;align-items:flex-start}.alarm-section-title{width:100%}.alarm-tabs{display:grid;grid-template-columns:repeat(2,1fr)}.alarm-tabs button{padding:0 8px}.alarm-refresh-button{position:absolute;right:14px}.alarm-insights{display:block}.insight-card{margin:10px 10px 0}.alarm-empty-state{min-height:104px;padding:22px 16px}.alarm-pagination{align-items:stretch;flex-direction:column}.alarm-pagination>div{justify-content:flex-end}}
.alarm-ai-panel{display:block;padding-bottom:0}
.alarm-ai-columns{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));align-items:start;gap:16px;padding:13px 15px 0}
.alarm-ai-columns>.alarm-ai-section{min-width:0;padding:0}
.alarm-ai-columns>.alarm-ai-section:only-child{grid-column:1/-1}
.alarm-ai-finding>header strong{min-width:0;overflow-wrap:anywhere}
@media(max-width:760px){.alarm-ai-columns{grid-template-columns:minmax(0,1fr)}}
.alarm-ai-summary{padding:15px;border-bottom:1px solid var(--theme-border-color);background:rgb(var(--container-bg-color))}.alarm-ai-summary>div{display:flex;align-items:center;gap:8px}.alarm-ai-summary>div>strong{color:var(--text-color-1);font-size:12px}.alarm-ai-summary>p{margin:9px 0 0;color:var(--text-color-2);font-size:var(--font-auxiliary);line-height:1.7;overflow-wrap:anywhere}.alarm-ai-summary>a{display:inline-flex;margin-top:9px;color:var(--alarm-blue);font-size:var(--font-auxiliary);font-weight:700;text-decoration:none}.alarm-ai-summary>a:hover{text-decoration:underline}.alarm-ai-risk{display:inline-flex;align-items:center;min-height:23px;padding:0 8px;border-radius:999px;color:rgb(var(--warning-color));background:rgb(var(--warning-color) / .1);font-size:var(--font-auxiliary);font-weight:700}.alarm-ai-risk.high{color:rgb(var(--warning-color));background:rgb(var(--warning-color) / .1)}.alarm-ai-risk.critical{color:rgb(var(--error-color));background:rgb(var(--error-color) / .1)}.alarm-ai-section{padding:13px 15px 0}.alarm-ai-section>h3,.alarm-ai-limitations>h3{margin:0 0 8px;color:var(--text-color-2);font-size:var(--font-auxiliary)}.alarm-ai-finding{margin-top:8px;padding:11px 12px;border:1px solid var(--theme-border-color);border-left:3px solid var(--alarm-blue);border-radius:7px;background:rgb(var(--container-bg-color))}.alarm-ai-finding.high{border-left-color:var(--alarm-amber)}.alarm-ai-finding.critical{border-left-color:var(--alarm-red)}.alarm-ai-finding>header{display:flex;align-items:flex-start;justify-content:space-between;gap:8px}.alarm-ai-finding>header strong{color:var(--text-color-1);font-size:var(--font-auxiliary)}.alarm-ai-finding>header span{flex:0 0 auto;color:var(--text-color-2);font-size:var(--font-auxiliary);font-variant-numeric:tabular-nums}.alarm-ai-finding>p{margin:7px 0 0;color:var(--text-color-3);font-size:var(--font-auxiliary);line-height:1.65;overflow-wrap:anywhere}.alarm-ai-finding>.alarm-ai-causes{padding-top:7px;border-top:1px dashed var(--theme-border-color)}.alarm-ai-causes>strong{margin-right:6px;color:var(--text-color-2)}.alarm-ai-finding>small,.alarm-ai-actions li>small{display:block;margin-top:6px;color:var(--text-color-3);font-size:var(--font-auxiliary)}.alarm-ai-actions ol{display:grid;gap:7px;margin:0;padding:0;list-style:none}.alarm-ai-actions li{padding:10px 11px;border-radius:7px;background:rgb(var(--warning-color) / .1)}.alarm-ai-actions li>span{color:rgb(var(--warning-color));font-size:var(--font-auxiliary);font-weight:700}.alarm-ai-actions li>p{margin:4px 0 0;color:var(--text-color-2);font-size:var(--font-auxiliary);line-height:1.6;overflow-wrap:anywhere}.alarm-ai-evidence{margin:13px 15px 0;border:1px solid var(--theme-border-color);border-radius:7px;background:rgb(var(--info-color) / .1)}.alarm-ai-evidence summary{padding:10px 11px;color:var(--text-color-2);font-size:var(--font-auxiliary);font-weight:700;cursor:pointer}.alarm-ai-evidence article{display:grid;gap:3px;padding:9px 11px;border-top:1px solid var(--theme-border-color)}.alarm-ai-evidence article strong{color:var(--text-color-2);font-size:var(--font-auxiliary);overflow-wrap:anywhere}.alarm-ai-evidence article span,.alarm-ai-evidence article small{color:var(--text-color-3);font-size:var(--font-auxiliary)}.alarm-ai-limitations{margin:13px 15px 0;padding:10px 11px;border-radius:7px;background:var(--theme-hover-color)}.alarm-ai-limitations ul{margin:0;padding-left:17px;color:var(--text-color-3);font-size:var(--font-auxiliary);line-height:1.6}.alarm-ai-disclaimer{margin:12px 15px 14px;color:var(--text-color-3);font-size:var(--font-auxiliary);line-height:1.6}.alarm-ai-empty{min-height:210px;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:7px;padding:24px 18px;text-align:center}.alarm-ai-empty strong{color:var(--text-color-2);font-size:12px}.alarm-ai-empty>span:last-child{max-width:270px;color:var(--text-color-3);font-size:var(--font-auxiliary);line-height:1.6}
@media(max-width:900px){.alarm-ai-panel{display:block}.alarm-ai-summary,.alarm-ai-section,.alarm-ai-limitations,.alarm-ai-disclaimer,.alarm-ai-evidence{max-width:none}}
@media(prefers-reduced-motion:reduce){.alarm-hero,.alarm-kpi,.alarm-list,.alarm-insights{animation:none}.alarm-kpi,.monitor-card,.alarm-tabs button{transition:none}}
/* Keep incident rows on shared table typography; reports and AI use the larger scale. */
.alarm-page{--font-section-title:16px;--font-auxiliary:12px}
.alarm-page .report-table-wrap th{font-size:13px!important}
.alarm-page .report-table-wrap td{font-size:14px!important}
.alarm-page .alarm-table-wrap:not(.report-table-wrap) :is(.alarm-name,td small,.severity-badge,.incident-state,.report-status){font-size:inherit}
.alarm-page .report-table-wrap .report-status{font-size:12px}
.alarm-page .alarm-section-title h2,.alarm-page .alarm-ai-summary>div>strong,.alarm-page .alarm-ai-section>h3,.alarm-page .alarm-ai-limitations>h3,.alarm-page .alarm-ai-finding>header strong{font-size:16px}
.alarm-page .alarm-ai-summary>p,.alarm-page .alarm-ai-finding>p,.alarm-page .alarm-ai-actions li>p,.alarm-page .alarm-ai-limitations ul,.alarm-page .alarm-ai-evidence article strong{font-size:14px;line-height:1.75}
.alarm-page .alarm-ai-summary>a,.alarm-page .alarm-ai-risk,.alarm-page .alarm-ai-finding>header span,.alarm-page .alarm-ai-finding>small,.alarm-page .alarm-ai-actions li>small,.alarm-page .alarm-ai-actions li>span,.alarm-page .alarm-ai-evidence summary,.alarm-page .alarm-ai-evidence article span,.alarm-page .alarm-ai-evidence article small,.alarm-page .alarm-ai-disclaimer,.alarm-page .alarm-insights>header>span,.alarm-page .report-count,.alarm-page .monitor-card dl div,.alarm-page .alarm-kpi em{font-size:12px}
.alarm-page .alarm-tabs button,.alarm-page .alarm-status-filter,.alarm-page .alarm-refresh-button{font-size:13px}
.alarm-page .alarm-ai-empty strong{font-size:16px}
.alarm-page .alarm-ai-empty>span:last-child,.alarm-page .alarm-empty-state>span:not(.spinner):not(.empty-icon){font-size:14px}
.alarm-page .alarm-ai-finding>header,.alarm-page .alarm-insights>header{flex-wrap:wrap}
.alarm-page .alarm-ai-evidence article{overflow-wrap:anywhere}
</style>
