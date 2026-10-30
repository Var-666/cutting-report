<script setup lang="ts">
import { useEnergyReportData } from '../composables/useEnergyReportData'
import { useEnergyReportCharts } from '../composables/useEnergyReportCharts'
import AppReportTable from '../components/AppReportTable.vue'
import type { ReportCreateRequest } from '../types/reports'

import type { EnergyPeriodReport, TaskEnergyReport } from '../types/energy'
import type { ExportFormat } from '../types/api'


import { toError } from '../utils/errors'
import { computed, nextTick, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getEnergyDay, getEnergyMonthlyRange, getEnergyRange, getTaskEnergyDay } from '../api/energy.ts'
import { getLatestWorkshopProductionDate } from '../api/cutting.ts'
import { recentTwelveMonthStart, reportToday } from '../api/reportDates.ts'
import AnalysisPanel from '../components/AnalysisPanel.vue'
import AppIcon from '../components/AppIcon.vue'
import MetricSparkline from '../components/MetricSparkline.vue'
import ReportFeedback from '../components/ReportFeedback.vue'
import ReportTrendPanel from '../components/ReportTrendPanel.vue'
import StandardReportQuery from '../components/StandardReportQuery.vue'
import { formatChineseDateTime } from '../utils/dateDisplay.ts'
import { useDeviceSwitchQuery } from '../composables/useDeviceSwitchQuery.ts'
import { mergeOwnedQuery, queryChoice, queryDateRange, sameRouteQuery } from '../utils/reportRouteState.ts'
defineProps({ exporting: Boolean })
const emit = defineEmits<{ export: [payload: ReportCreateRequest, format: ExportFormat] }>()
const route = useRoute()
const router = useRouter()
const today = reportToday()
const anchorDate = ref(today)
const periodStart = ref(today)
const periodEnd = ref(today)
const appliedPeriodStart = ref(today)
const appliedPeriodEnd = ref(today)
const summaryPeriodStart = ref(recentTwelveMonthStart(today))
const summaryPeriodEnd = ref(today)
const rangeQueryApplied = ref(false)
const taskDay = ref<TaskEnergyReport | null>(null)
const monthlyReport = ref<EnergyPeriodReport | null>(null)
const chartType = ref(queryChoice(route.query.chart, ['bar_line', 'bar', 'line'], 'bar_line'))
const energyChartOptions = [
  { value: 'bar_line', label: '柱线组合' },
  { value: 'bar', label: '双柱状图' },
  { value: 'line', label: '双折线图' },
]
const loading = ref(false)
const summaryLoading = ref(false)
const error = ref('')
const pageActive = ref(true)
let controller: AbortController | null = null
let backgroundController: AbortController | null = null
let refreshController: AbortController | null = null
let requestSequence = 0
let refreshRequestSequence = 0
let summaryRefreshTimer: number | null = null
let summaryRefreshCount = 0
let routeStateReady = false

const {
  taskRows, taskChartRows, summaryRows, summaryChartRows, month,
  units, summary, equipmentScopeLabel, energyConnected, configurationError,
  energyCoverage, deviceMetricSeries, summaryCountLabel, summarySubtitle, format,
  duration, valueText, summaryPeriod,
} = useEnergyReportData({ taskDay, monthlyReport, summaryPeriodStart, summaryPeriodEnd })
const { taskPanel, periodPanel, renderCharts, resizeCharts, disposeCharts, startResizeListening, stopResizeListening, chartsMissing } = useEnergyReportCharts({ taskDay, monthlyReport, taskChartRows, summaryChartRows, units, format, chartType, pageActive })

const routeQueryKeys = ['start', 'end', 'chart']
const dateQueryDirty = computed(() => (
  periodStart.value !== appliedPeriodStart.value || periodEnd.value !== appliedPeriodEnd.value
))

function routeStateQuery() {
  return mergeOwnedQuery(route.query, routeQueryKeys, {
    start: rangeQueryApplied.value ? appliedPeriodStart.value : '',
    end: rangeQueryApplied.value ? appliedPeriodEnd.value : '',
    chart: chartType.value === 'bar_line' ? '' : chartType.value,
  })
}

useDeviceSwitchQuery('equipment-report', () => ({
  ...routeStateQuery(), start: appliedPeriodStart.value, end: appliedPeriodEnd.value,
}))

async function syncRouteState() {
  const query = routeStateQuery()
  if (sameRouteQuery(query, route.query)) return
  await router.push({ query })
}

function cancelReportRequest() {
  requestSequence += 1
  controller?.abort()
  controller = null
  loading.value = false
  summaryLoading.value = false
}

async function changeChartType(value: string) {
  chartType.value = value
  const query = mergeOwnedQuery(route.query, ['chart'], { chart: value === 'bar_line' ? '' : value })
  if (loading.value) Object.assign(query, { start: periodStart.value, end: periodEnd.value })
  if (pageActive.value && !sameRouteQuery(query, route.query)) await router.push({ query })
}

function beginRequest({ preserveSummary = false } = {}) {
  cancelReportRequest()
  if (!preserveSummary) {
    stopSummaryRefresh()
    backgroundController?.abort()
    backgroundController = null
  }
  controller = new AbortController()
  return { sequence: requestSequence, signal: controller.signal }
}

function requestIsCurrent(sequence: number, signal: AbortSignal) {
  return pageActive.value && !signal.aborted && sequence === requestSequence
}
const summaryTitle = '近12个月月度能耗汇总'
const summaryDateHeading = '月份'
const summaryChartLabel = '月度能耗图'
const summaryTableLabel = '近12个月月度能耗汇总表'
const summaryEmptyText = '近12个月暂无月度汇总'
const analysisReady = computed(() => Boolean(
  pageActive.value
  && taskDay.value
  && monthlyReport.value?.analysis_source_version
  && taskDay.value.period_start === appliedPeriodStart.value
  && taskDay.value.period_end === appliedPeriodEnd.value
  && !loading.value
  && !summaryLoading.value
))

async function loadRange(force = false, { syncRoute = true } = {}) {
  if (!periodStart.value || !periodEnd.value) return
  if (periodEnd.value < periodStart.value) {
    error.value = '结束日期不能早于开始日期'
    return
  }
  const { sequence, signal } = beginRequest({ preserveSummary: true })
  loading.value = true
  error.value = ''
  const requestedStart = periodStart.value
  const requestedEnd = periodEnd.value
  try {
    const result = await getEnergyRange(requestedStart, requestedEnd, { force, signal })
    if (!requestIsCurrent(sequence, signal)) return
    taskDay.value = result.task_report
    appliedPeriodStart.value = requestedStart
    appliedPeriodEnd.value = requestedEnd
    rangeQueryApplied.value = true
    await nextTick()
    if (!requestIsCurrent(sequence, signal)) return
    renderCharts({ summary: false })
    if (syncRoute) await syncRouteState()
  } catch (caught) {
    const exception = toError(caught)
    if (exception.name !== 'AbortError' && sequence === requestSequence) error.value = exception.message
  } finally {
    if (sequence === requestSequence) {
      loading.value = false
      controller = null
    }
  }
}

function stopSummaryRefresh() {
  if (summaryRefreshTimer) window.clearTimeout(summaryRefreshTimer)
  summaryRefreshTimer = null
  refreshRequestSequence += 1
  refreshController?.abort()
  refreshController = null
}

async function applyMonthlySummary(summaryResult: EnergyPeriodReport) {
  if (!pageActive.value) return
  monthlyReport.value = summaryResult
  await nextTick()
  renderCharts()
  scheduleSummaryRefresh(summaryResult)
}

function scheduleSummaryRefresh(summaryResult: EnergyPeriodReport) {
  if (summaryRefreshTimer || refreshController || !pageActive.value || !summaryResult?.syncing
      || !summaryResult?.missing_day_count || summaryRefreshCount >= 600) return
  const requestedStart = summaryPeriodStart.value
  const requestedEnd = summaryPeriodEnd.value
  const sequence = refreshRequestSequence
  const current = () => pageActive.value && sequence === refreshRequestSequence
    && requestedStart === summaryPeriodStart.value && requestedEnd === summaryPeriodEnd.value
  summaryRefreshTimer = window.setTimeout(async () => {
    if (!current()) return
    summaryRefreshTimer = null
    summaryRefreshCount += 1
    const controller = new AbortController()
    refreshController = controller
    try {
      const refreshed = await getEnergyMonthlyRange(requestedStart, requestedEnd, { force: true, signal: controller.signal })
      if (!current() || controller.signal.aborted) return
      await applyMonthlySummary(refreshed)
    } catch (caught) {
      const exception = toError(caught)
      if (exception.name === 'AbortError') return
      // Keep the last valid summary and retry within the existing attempt limit.
    } finally {
      if (refreshController === controller) {
        refreshController = null
        if (current() && !controller.signal.aborted) scheduleSummaryRefresh(monthlyReport.value || summaryResult)
      }
    }
  }, 1000)
}

async function loadDefaultRanges(force = false) {
  stopSummaryRefresh()
  summaryRefreshCount = 0
  const { sequence, signal } = beginRequest()
  loading.value = true
  summaryLoading.value = true
  error.value = ''
  const selectedDate = periodStart.value
  const recentStart = recentTwelveMonthStart(selectedDate)
  appliedPeriodStart.value = selectedDate
  appliedPeriodEnd.value = selectedDate
  summaryPeriodStart.value = recentStart
  summaryPeriodEnd.value = selectedDate
  rangeQueryApplied.value = false

  const showError = (exception: Error) => {
    if (exception.name !== 'AbortError' && requestIsCurrent(sequence, signal)) error.value ||= exception.message
  }
  const taskRequest = getTaskEnergyDay(selectedDate, { force, signal })
    .then(async (taskResult) => {
      if (!requestIsCurrent(sequence, signal)) return
      taskDay.value = {
        ...taskResult,
        period_start: selectedDate,
        period_end: selectedDate,
      }
      await nextTick()
      renderCharts()
    })
    .catch(showError)
  const summaryRequest = getEnergyMonthlyRange(recentStart, selectedDate, { force, signal })
    .then(async (summaryResult) => {
      if (!requestIsCurrent(sequence, signal)) return
      await applyMonthlySummary(summaryResult)
    })
    .catch(showError)
    .finally(() => {
      if (sequence === requestSequence) summaryLoading.value = false
    })

  // Daily meter synchronization is independent from first paint. Existing
  // monthly snapshots render first; the current month refreshes silently once
  // the live meter request finishes.
  backgroundController = new AbortController()
  const syncController = backgroundController
  const syncSignal = syncController.signal
  getEnergyDay(selectedDate, { signal: syncSignal })
    .then(() => getEnergyMonthlyRange(recentStart, selectedDate, { signal: syncSignal }))
    .then(async (summaryResult) => {
      if (!pageActive.value || syncSignal.aborted) return
      await applyMonthlySummary(summaryResult)
    })
    .catch(() => {})
    .finally(() => {
      if (backgroundController === syncController) backgroundController = null
    })

  await Promise.allSettled([taskRequest, summaryRequest])
  if (requestIsCurrent(sequence, signal)) {
    loading.value = false
    controller = null
  }
}

function exportReport(format: ExportFormat = 'html') {
  if (!taskDay.value || loading.value) return
  emit('export', {
    report_type: 'equipment',
    report_date: appliedPeriodStart.value,
    period_start: appliedPeriodStart.value,
    period_end: appliedPeriodEnd.value,
    project_code: null,
    ship_no: null,
    parameters: {
      chart_type: chartType.value,
      summary_period_start: summaryPeriodStart.value,
      summary_period_end: summaryPeriodEnd.value,
    },
  }, format)
}

function pause(dispose = false) {
  if (pageActive.value) {
    pageActive.value = false
    cancelReportRequest()
    backgroundController?.abort()
    backgroundController = null
    loading.value = false
    summaryLoading.value = false
    stopSummaryRefresh()
    stopResizeListening()
  }
  if (dispose) disposeCharts()
}

async function initializeReport({ restoreQuery = false } = {}) {
  const requestedQuery = route.query
  const requestedRange = restoreQuery ? queryDateRange(route.query, { maxDays: 31 }) : null
  const { sequence, signal } = beginRequest()
  loading.value = true
  try {
    const latest = await getLatestWorkshopProductionDate(today, { signal })
    if (!requestIsCurrent(sequence, signal)) return
    if (latest.report_date) {
      anchorDate.value = latest.report_date
      periodStart.value = latest.report_date
      periodEnd.value = latest.report_date
    }
  } catch (caught) {
    const exception = toError(caught)
    if (exception.name !== 'AbortError' && sequence === requestSequence) error.value = exception.message
  }
  if (!requestIsCurrent(sequence, signal)) return
  await loadDefaultRanges()
  if (requestedRange && pageActive.value && route.query === requestedQuery) {
    periodStart.value = requestedRange.start
    periodEnd.value = requestedRange.end
    await loadRange(false, { syncRoute: false })
  }
}

async function restoreRouteState({ preserveDraft = false } = {}) {
  if (route.name !== 'equipment-report') return false
  cancelReportRequest()
  const range = queryDateRange(route.query, { maxDays: 31 })
  const nextChartType = queryChoice(route.query.chart, ['bar_line', 'bar', 'line'], 'bar_line')
  chartType.value = nextChartType

  if (range) {
    const needsLoad = !taskDay.value || !monthlyReport.value || !rangeQueryApplied.value || appliedPeriodStart.value !== range.start || appliedPeriodEnd.value !== range.end
    if (!preserveDraft || needsLoad) {
      periodStart.value = range.start
      periodEnd.value = range.end
    }
    if (!taskDay.value || !monthlyReport.value) {
      await initializeReport({ restoreQuery: true })
      return true
    }
    if (!rangeQueryApplied.value || appliedPeriodStart.value !== range.start || appliedPeriodEnd.value !== range.end) {
      await loadRange(false, { syncRoute: false })
      return true
    }
    return false
  }

  if (!taskDay.value || !monthlyReport.value || rangeQueryApplied.value) {
    await initializeReport()
    return true
  }
  if (!preserveDraft) {
    periodStart.value = appliedPeriodStart.value
    periodEnd.value = appliedPeriodEnd.value
  }
  return false
}

function retryReport() {
  return rangeQueryApplied.value ? loadRange(false) : initializeReport()
}

async function refreshRangeAnalysisReport() {
  const { sequence, signal } = beginRequest()
  loading.value = true
  summaryLoading.value = true
  error.value = ''
  try {
    const [rangeResult, summaryResult] = await Promise.all([
      getEnergyRange(appliedPeriodStart.value, appliedPeriodEnd.value, { force: true, signal }),
      getEnergyMonthlyRange(
        summaryPeriodStart.value,
        summaryPeriodEnd.value,
        { force: true, signal },
      ),
    ])
    if (!requestIsCurrent(sequence, signal)) return
    taskDay.value = rangeResult.task_report
    await applyMonthlySummary(summaryResult)
    await nextTick()
    renderCharts()
  } catch (caught) {
    const exception = toError(caught)
    if (exception.name !== 'AbortError' && sequence === requestSequence) error.value = exception.message
  } finally {
    if (sequence === requestSequence) {
      loading.value = false
      summaryLoading.value = false
      controller = null
    }
  }
}

async function refreshAnalysisReport() {
  if (!pageActive.value || loading.value) return
  return rangeQueryApplied.value
    ? refreshRangeAnalysisReport()
    : loadDefaultRanges(true)
}

onMounted(async () => {
  const initialQuery = route.query
  await initializeReport({ restoreQuery: true })
  routeStateReady = true
  if (pageActive.value && route.query !== initialQuery) await restoreRouteState()
})
onActivated(async () => {
  pageActive.value = true
  await nextTick()
  if (!pageActive.value) return
  startResizeListening()
  if (routeStateReady && await restoreRouteState()) return
  if ((!taskDay.value || !monthlyReport.value) && !loading.value) await initializeReport()
  else if (chartsMissing()) renderCharts()
  resizeCharts()
  if (monthlyReport.value) scheduleSummaryRefresh(monthlyReport.value)
})
onDeactivated(pause)
onBeforeUnmount(() => pause(true))
watch(chartType, async () => {
  await nextTick()
  renderCharts()
})
watch(() => route.query, async (query, previous) => {
  if (routeStateReady && pageActive.value && route.name === 'equipment-report') {
    await restoreRouteState({ preserveDraft: query.start === previous.start && query.end === previous.end })
  }
}, { deep: true })

const taskTableColumns = [
  { key: 'c0', title: "序号", width: 56, align: 'center', fixed: 'left' },
  { key: 'c1', title: "工程号", width: 76, align: 'left' },
  { key: 'c2', title: "船号", width: 68, align: 'left' },
  { key: 'c3', title: "分段名", width: 76, align: 'left' },
  { key: 'c4', title: "任务开始", width: 184, align: 'left' },
  { key: 'c5', title: "切割时长", width: 96, align: 'center' },
  { key: 'c6', title: "电耗", width: 80, align: 'right' },
  { key: 'c7', title: "气耗", width: 80, align: 'right' }
] as const

const summaryTableColumns = [
  { key: 'c0', title: "序号", width: 56, align: 'center', fixed: 'left' },
  { key: 'c1', title: '', width: 112, align: 'left' },
  { key: 'c2', title: "任务数", width: 84, align: 'right' },
  { key: 'c3', title: "切割时长", width: 112, align: 'center' },
  { key: 'c4', title: "电耗", width: 104, align: 'right' },
  { key: 'c5', title: "气耗", width: 104, align: 'right' }
] as const
</script>

<template>
  <div class="device-report-shell">
    <section class="page-hero report-hero report-hero--query equipment-hero">
      <div class="report-hero-copy device-hero__copy">
        <span class="report-hero-eyebrow">EQUIPMENT INTELLIGENCE</span>
        <h2>设备报表</h2>
      </div>
      <StandardReportQuery
        v-model:start-date="periodStart"
        v-model:end-date="periodEnd"
        :chart-type="chartType"
        @update:chart-type="changeChartType"
        class="report-hero-query device-query-shell"
        aria-label="设备报表查询条件"
        :anchor-date="anchorDate"
        :max-days="31"
        :chart-options="energyChartOptions"
        :loading="loading"
        :has-data="Boolean(taskDay)"
        :date-dirty="dateQueryDirty"
        :export-disabled="loading || !taskDay"
        :exporting="exporting"
        @submit="loadRange(true)"
        @export="exportReport"
        @validation-error="error = $event"
      />
    </section>

    <ReportFeedback v-if="error && taskDay && monthlyReport" type="error" title="设备报表更新失败" :message="error" action-label="重新查询" dismissible @action="retryReport" @dismiss="error = ''" />
    <ReportFeedback v-if="!energyConnected" type="info" title="能耗暂未接入" message="当前设备暂未接入电表和气表，生产任务与工时正常统计。" />
    <ReportFeedback v-else-if="configurationError" type="warning" title="能管数据提示" :message="`${configurationError}。缺失值显示 --，不按零计算。`" />

    <section class="device-kpis" aria-label="设备区间汇总" :aria-busy="loading">
      <article class="device-kpi kpi-red">
        <span class="device-kpi__icon"><AppIcon name="zap" size="21" tone="current" /></span>
        <div><span>电耗</span><p><strong>{{ format(summary.total_electricity_consumption) }}</strong> <small>{{ units.electricity }}</small></p><em>区间合计</em></div>
        <MetricSparkline class="device-sparkline" :values="deviceMetricSeries.electricity" label="任务电耗趋势" />
      </article>
      <article class="device-kpi kpi-blue">
        <span class="device-kpi__icon"><AppIcon name="wind" size="21" tone="current" /></span>
        <div><span>气耗</span><p><strong>{{ format(summary.total_gas_consumption) }}</strong> <small>{{ units.gas }}</small></p><em>区间合计</em></div>
        <MetricSparkline class="device-sparkline" :values="deviceMetricSeries.gas" label="任务气耗趋势" />
      </article>
      <article class="device-kpi kpi-teal">
        <span class="device-kpi__icon"><AppIcon name="activity" size="21" tone="current" /></span>
        <div><span>能耗覆盖率</span><p><strong>{{ taskDay ? energyCoverage : '--' }}</strong> <small>%</small></p><em>电 / 气数据匹配</em></div>
        <MetricSparkline class="device-sparkline" :values="deviceMetricSeries.coverage" label="单项任务能耗覆盖率趋势" />
      </article>
      <article class="device-kpi kpi-violet">
        <span class="device-kpi__icon"><AppIcon name="tasks" size="21" tone="current" /></span>
        <div><span>任务数量</span><p><strong>{{ taskDay ? (summary.task_count ?? '--') : '--' }}</strong> <small>项</small></p><em>{{ equipmentScopeLabel }}</em></div>
        <MetricSparkline class="device-sparkline" :values="deviceMetricSeries.tasks" label="每日任务数量趋势" />
      </article>
      <article class="device-kpi kpi-cyan">
        <span class="device-kpi__icon"><AppIcon name="clock" size="21" tone="current" /></span>
        <div><span>累计切割时长</span><p class="device-duration"><strong>{{ duration(summary.total_work_time) }}</strong></p><em>当前查询区间</em></div>
        <MetricSparkline class="device-sparkline" :values="deviceMetricSeries.workTime" label="单项任务切割时长趋势" />
      </article>
    </section>

    <ReportTrendPanel
      :height="245"
      ref="taskPanel"
      number="01"
      title="区间任务能耗明细"
      :subtitle="`${appliedPeriodStart} 至 ${appliedPeriodEnd}`"
      chart-label="任务能耗图"
      :loading="loading"
      :has-data="taskRows.length > 0"
      :empty="Boolean(taskDay) && !taskRows.length"
      :error="!taskDay ? error : ''"
      empty-title="所选区间暂无任务"
      empty-message="当前统计区间没有可展示的设备任务，可调整日期后重新查询。"
      @retry="retryReport"
    >
    <template #table>
        <AppReportTable flex-height :columns="taskTableColumns" :items="taskRows" :row-key="row => row.task_id" :paginate="false" aria-label="任务能耗明细表" summary>
        <template #c0="{ row, index: rowIndex }">{{ rowIndex + 1 }}</template>
        <template #c1="{ row }">{{ row.project_code || '--' }}</template>
        <template #c2="{ row }">{{ row.ship_no || '--' }}</template>
        <template #c3="{ row }">{{ row.segment_name || '--' }}</template>
        <template #c4="{ row }">{{ formatChineseDateTime(row.production_time) }}</template>
        <template #c5="{ row }">{{ duration(row.work_time) }}</template>
        <template #c6="{ row }">{{ format(row.electricity_consumption) }}</template>
        <template #c7="{ row }">{{ format(row.gas_consumption) }}</template>
        <template #summary-c0>汇总</template>
        <template #summary-c1></template>
        <template #summary-c2></template>
        <template #summary-c3></template>
        <template #summary-c4>--</template>
        <template #summary-c5>{{ duration(summary.total_work_time) }}</template>
        <template #summary-c6>{{ format(summary.total_electricity_consumption) }}</template>
        <template #summary-c7>{{ format(summary.total_gas_consumption) }}</template>
        <template #empty>{{ loading ? '正在查询…' : '所选区间暂无任务' }}</template>
      </AppReportTable>
    </template>
    </ReportTrendPanel>

    <ReportTrendPanel
      :height="245"
      ref="periodPanel"
      number="02"
      :title="summaryTitle"
      :subtitle="summarySubtitle"
      :chart-label="summaryChartLabel"
      :loading="summaryLoading"
      :has-data="summaryRows.length > 0"
      :empty="Boolean(monthlyReport) && !summaryRows.length"
      :error="!monthlyReport ? error : ''"
      :empty-title="summaryEmptyText"
      empty-message="当前月份范围没有可展示的能耗汇总。"
      @retry="initializeReport"
    >
    <template #table><AppReportTable flex-height :columns="summaryTableColumns" :items="summaryRows" :row-key="row => row.report_date" :paginate="false" :aria-label="summaryTableLabel" summary>
        <template #header-c1>{{ summaryDateHeading }}</template>
        <template #c0="{ row, index: rowIndex }">{{ rowIndex + 1 }}</template>
        <template #c1="{ row }">{{ summaryPeriod(row.report_date) }}</template>
        <template #c2="{ row }">{{ row.task_count }}</template>
        <template #c3="{ row }">{{ duration(row.work_time_seconds) }}</template>
        <template #c4="{ row }">{{ valueText(row.electricity) }}</template>
        <template #c5="{ row }">{{ valueText(row.gas) }}</template>
        <template #summary-c0>汇总</template>
        <template #summary-c1></template>
        <template #summary-c2>{{ month?.total_task_count || 0 }}</template>
        <template #summary-c3>{{ duration(month?.total_work_time_seconds) }}</template>
        <template #summary-c4>{{ valueText(month?.summary?.electricity) }}</template>
        <template #summary-c5>{{ valueText(month?.summary?.gas) }}</template>
      <template #empty>{{ summaryLoading ? '正在查询…' : summaryEmptyText }}</template>
      </AppReportTable></template>
    </ReportTrendPanel>

    <AnalysisPanel
      report-type="equipment"
      :report-date="appliedPeriodStart"
      :period-start="rangeQueryApplied ? appliedPeriodStart : ''"
      :period-end="rangeQueryApplied ? appliedPeriodEnd : ''"
      :summary-period-start="summaryPeriodStart"
      :summary-period-end="summaryPeriodEnd"
      :ready="analysisReady"
      :data-version="taskDay?.analysis_source_version || ''"
      :summary-data-version="monthlyReport?.analysis_source_version || ''"
      :version-check-enabled="!rangeQueryApplied"
      @refresh-report="refreshAnalysisReport"
    />
  </div>
</template>

<style scoped>
.device-report-shell>.page-hero{position:relative;min-height:174px;display:flex;align-items:center;overflow:hidden;padding:25px 30px;border:1px solid var(--theme-border-color);border-radius:14px;background:linear-gradient(90deg,rgb(var(--container-bg-color) / 0.99) 0%,rgb(var(--container-bg-color) / 0.92) 39%,rgb(var(--container-bg-color) / 0.08) 69%),url('../assets/home-laser-cutting.webp') center 51%/cover no-repeat;box-shadow:var(--theme-shadow);animation:surface-in .46s cubic-bezier(.2,.75,.25,1) both}.device-report-shell>.page-hero::after{content:"";position:absolute;inset:0;pointer-events:none;background:transparent}.device-hero__copy{position:relative;z-index:1;max-width:540px}.device-breadcrumb{display:block;margin-bottom:7px;color:rgb(var(--info-color));font-size:var(--font-auxiliary)}.device-title-row{display:flex;align-items:flex-end;gap:14px}.device-title-row h2{margin:0;color:var(--text-color-1);font-size:var(--font-page-title);line-height:1.08;letter-spacing:1px}.device-title-row>span{padding-bottom:5px;color:rgb(var(--info-color));font-size:var(--font-auxiliary);font-weight:700;letter-spacing:3px}.device-query-shell{margin:10px 0;border:1px solid var(--theme-border-color)}.device-query-shell .report-query{width:100%;grid-template-columns:minmax(320px,1fr) minmax(180px,.55fr) 128px 128px;padding:0;border:0;background:transparent;box-shadow:none}.device-query-shell :deep(.report-period-picker){grid-column:auto}.device-query-shell :deep(.report-period-picker__trigger),.device-query-shell :deep(.app-select__trigger),.device-query-shell .report-query>button{height:38px;min-height:38px;border-radius:6px;font-size:var(--font-auxiliary)}.device-query-shell .report-query>button.primary{display:flex;align-items:center;justify-content:center;gap:6px;border-color:var(--theme-border-color);background:rgb(var(--info-color));box-shadow:var(--theme-shadow)}
.energy-config-note{display:grid;gap:6px;padding:12px 16px;margin-bottom:10px;border:1px solid var(--theme-border-color);border-radius:9px;background:rgb(var(--warning-color) / .1);color:rgb(var(--warning-color));font-size:var(--font-auxiliary)}.device-kpis{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:16px;margin:16px 0}.device-kpi{--kpi-color:rgb(var(--info-color));--kpi-soft:rgb(var(--info-color) / .1);position:relative;min-width:0;min-height:108px;padding:12px;border:1px solid var(--theme-border-color);border-top:2px solid var(--kpi-color);border-radius:10px;background:rgb(var(--container-bg-color));box-shadow:var(--theme-shadow);transition:transform .18s ease,box-shadow .18s ease;animation:surface-in .42s cubic-bezier(.2,.75,.25,1) both;display:grid;grid-template-columns:35px minmax(0,1fr);align-items:center;gap:8px 12px}.device-kpi:hover{transform:translateY(-2px);box-shadow:var(--theme-shadow)}.device-kpi:nth-child(2){animation-delay:.04s}.device-kpi:nth-child(3){animation-delay:.08s}.device-kpi:nth-child(4){animation-delay:.12s}.device-kpi:nth-child(5){animation-delay:.16s}.device-kpi.kpi-red{--kpi-color:var(--text-color-3);--kpi-soft:rgb(var(--info-color) / .1)}.device-kpi.kpi-teal{--kpi-color:rgb(var(--info-color));--kpi-soft:rgb(var(--info-color) / .1)}.device-kpi.kpi-violet{--kpi-color:rgb(var(--primary-color));--kpi-soft:rgb(var(--primary-color) / .1)}.device-kpi.kpi-cyan{--kpi-color:rgb(var(--info-color));--kpi-soft:rgb(var(--info-color) / .1)}.device-kpi__icon{position:relative;z-index:1;width:35px;height:35px;display:grid;place-items:center;flex:0 0 auto;color:var(--kpi-color);border-radius:50%;background:var(--kpi-soft);transition:transform .2s ease}.device-kpi:hover .device-kpi__icon{transform:scale(1.08) rotate(-3deg)}.device-kpi>div{display:contents}.device-kpi>div>span{display:block;color:var(--text-color-1);font-size:var(--font-auxiliary);font-weight:700;white-space:normal;overflow-wrap:anywhere;grid-column:2}.device-kpi p{margin:0;color:var(--text-color-1);font-size:var(--font-auxiliary);grid-column:1/-1;min-width:0;overflow-wrap:anywhere}.device-kpi p strong{font-size:21px}.device-kpi p.device-duration strong{font-size:17px}.device-kpi p small{margin-left:2px;color:var(--muted);font-size:var(--font-auxiliary)}.device-kpi em{display:block;color:var(--muted);font-style:normal;font-size:var(--font-auxiliary);grid-column:1/-1;min-width:0;white-space:normal;overflow-wrap:anywhere;line-height:1.6}.device-sparkline{width:58px;height:25px;opacity:.72;grid-column:1/-1;justify-self:end}.global-error{margin:10px 0 0;border-radius:9px}
.energy-period-table { width:100%; border-collapse:collapse; font-size:12px; }
.energy-period-table th,.energy-period-table td { height:47px; padding-block:10px; border-bottom:1px solid var(--theme-border-color); text-align:center; white-space:nowrap; }
.energy-task-table th:nth-child(2),.energy-task-table td:nth-child(2),.energy-task-table th:nth-child(3),.energy-task-table td:nth-child(3),.energy-task-table th:nth-child(4),.energy-task-table td:nth-child(4) { text-align:left; }
.energy-period-table thead th { position:sticky; top:0; z-index:2; color:var(--report-table-head-ink); background:var(--report-table-head-bg); }
.energy-period-table tfoot th { position:sticky; bottom:0; background:rgb(var(--info-color) / .1); color:var(--navy); }
.energy-task-table tfoot th:nth-child(2),.energy-task-table tfoot th:nth-child(3),.energy-task-table tfoot th:nth-child(4) { text-align:left; }
.energy-empty { height:120px; color:var(--text-color-3); }
:deep(.report-trend-panel){margin-bottom:24px;border:1px solid var(--theme-border-color);border-radius:var(--report-panel-radius);box-shadow:var(--theme-shadow)}:deep(.report-trend-panel__heading){min-height:47px;padding:0 16px;border-bottom:1px solid var(--theme-border-color);background:rgb(var(--container-bg-color))}:deep(.report-trend-panel__heading>div){gap:8px}:deep(.report-trend-panel__heading>div>span){width:28px;height:24px;display:grid;place-items:center;padding:0;color:rgb(var(--info-color));border:0;border-radius:7px;background:rgb(var(--info-color) / .1);font-size:var(--font-auxiliary);font-weight:700}:deep(.report-trend-panel__heading h2){color:var(--text-color-1);font-size:14px}:deep(.report-trend-panel__heading small){color:var(--muted);font-size:var(--font-auxiliary);font-weight:400}:deep(.report-trend-panel__heading p){color:var(--muted);font-size:var(--font-auxiliary)}:deep(.report-trend-panel__content){gap:16px}:deep(.report-trend-panel__chart),:deep(.report-trend-panel__table){height:var(--report-trend-height);overflow:hidden;border-color:var(--theme-border-color);border-radius:6px}:deep(table.content-table th),:deep(table.content-table td){height:35px!important;padding-block:6px!important;font-size:var(--font-auxiliary)!important}:deep(table.content-table thead th){letter-spacing:.15px!important}:deep(table.content-table tfoot th){color:var(--text-color-1)!important;border-top:1px solid var(--theme-border-color)!important;background:rgb(var(--info-color) / .1)!important}
@media(max-width:1120px){.device-kpis{grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;margin:16px 0}.device-kpi:last-child{grid-column:1/-1}}
@media(max-width:900px){.device-query-shell .report-query{grid-template-columns:repeat(2,minmax(180px,1fr))}.device-query-shell :deep(.report-period-picker){grid-column:1/-1}:deep(.report-trend-panel__content){grid-template-columns:1fr;gap:16px}:deep(.report-trend-panel){--report-trend-height:270px!important}:deep(.report-trend-panel__chart),:deep(.report-trend-panel__table){height:var(--report-trend-height);border-radius:6px}}
@media(max-width:560px){.device-report-shell>.page-hero{min-height:0;padding:22px 18px;background:linear-gradient(90deg,rgb(var(--container-bg-color) / 0.97),rgb(var(--container-bg-color) / 0.89)),url('../assets/home-laser-cutting.webp') center/cover}.device-title-row{display:block}.device-title-row h2{font-size:var(--font-page-title-mobile)}.device-title-row>span{display:block;padding:6px 0 0}.device-query-shell .report-query,.device-kpis{grid-template-columns:1fr}.device-query-shell :deep(.report-period-picker),.device-kpi:last-child{grid-column:auto}}
@media(prefers-reduced-motion:reduce){.device-report-shell>.page-hero,.device-kpi{animation:none}.device-kpi{transition:none;display:grid;grid-template-columns:35px minmax(0,1fr);align-items:center;gap:8px 12px;padding:12px}}


/* Report tables use the same low-contrast header treatment across all report pages. */
.device-query-shell { position:relative; padding:12px 14px 13px; border-color:var(--theme-border-color); border-radius:12px; background:rgb(var(--container-bg-color)); box-shadow:var(--theme-shadow); }
.device-query-shell::before { content:""; position:absolute; top:-1px; left:18px; width:34px; height:3px; border-radius:0 0 999px 999px; background:rgb(var(--info-color)); }
.device-query-shell .standard-report-query { gap:10px; }
:deep(table.content-table thead th) {
  color:var(--report-table-head-ink)!important;
  background:var(--report-table-head-bg)!important;
  font-size:var(--font-auxiliary)!important;
}
:deep(table.content-table tbody td),
:deep(table.content-table tbody th),
:deep(table.content-table tfoot th) { font-size:var(--font-auxiliary)!important; }
:deep(.report-trend-panel__table) {
  height:var(--report-trend-height)!important;
  overflow:hidden!important;
  scrollbar-gutter:auto;
}
</style>
