<script setup lang="ts">
import { useLeadershipReportQuery } from '../composables/useLeadershipReportQuery'
import { useLeadershipReportData } from '../composables/useLeadershipReportData'
import { useLeadershipReportCharts } from '../composables/useLeadershipReportCharts'
import AppReportTable from '../components/AppReportTable.vue'
import type { ReportCreateRequest } from '../types/reports'

import type { ExportFormat } from '../types/api'


import { computed, nextTick, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AnalysisPanel from '../components/AnalysisPanel.vue'
import MetricSparkline from '../components/MetricSparkline.vue'
import ReportFeedback from '../components/ReportFeedback.vue'
import ReportDataStatus from '../components/ReportDataStatus.vue'
import ReportTrendPanel from '../components/ReportTrendPanel.vue'
import StandardReportQuery from '../components/StandardReportQuery.vue'
import AppIcon from '../components/AppIcon.vue'
import heroImage from '../assets/home-laser-cutting.webp'
import { reportToday } from '../api/reportDates.ts'
import { formatChineseDate, formatChineseMonth } from '../utils/dateDisplay.ts'
import { useDeviceSwitchQuery } from '../composables/useDeviceSwitchQuery.ts'
import { queryDateRange } from '../utils/reportRouteState.ts'

defineProps({ exporting: Boolean })
const emit = defineEmits<{ export: [payload: ReportCreateRequest, format: ExportFormat] }>()
const route = useRoute()
const router = useRouter()
const today = reportToday()
const routeRange = queryDateRange(route.query)
const pageActive = ref(true)
let routeStateReady = false

const {
  periodStart, periodEnd, appliedPeriodStart, appliedPeriodEnd, monthlyPeriodStart,
  monthlyPeriodEnd, defaultMonthlyView, chartType, report, monthlyReport,
  loading, monthlyLoading, refreshLoading, error, dateQueryDirty,
  routeStateQuery, cancelReportRequest, changeChartType, snapshot, loadReport,
  restoreRouteState, loadDefaultReport, scheduleRefreshPoll, retryReport, refreshAnalysisReport, refreshCachedReport,
} = useLeadershipReportQuery({ route, router, today, routeRange, pageActive, renderCharts: options => renderCharts(options) })
const {
  dailyRecords, dailyChartRecords, metricSeries, monthlyRecords, total,
  monthlyTotal, roundNumber, formatLength, formatDuration,
  metricChanges, comparisonLabel,
} = useLeadershipReportData({ report, monthlyReport, appliedPeriodStart, appliedPeriodEnd, defaultMonthlyView })
const { dailyPanel, monthlyPanel, renderCharts, resizeCharts, disposeCharts, startResizeListening, stopResizeListening, chartsMissing } = useLeadershipReportCharts({ report, monthlyReport, dailyChartRecords, monthlyRecords, chartType, pageActive, roundNumber, formatLength })

useDeviceSwitchQuery('leadership-report', routeStateQuery)
const monthlyTitle = '近12个月按月对比与趋势'
const analysisReady = computed(() => Boolean(
  pageActive.value
  &&
  report.value
  && monthlyReport.value?.analysis_summary_source_version
  && snapshot.value.data_as_of
  && !loading.value
  && !monthlyLoading.value
))

function pausePage(dispose = false) {
  if (pageActive.value) {
    pageActive.value = false
    cancelReportRequest()
    stopResizeListening()
  }
  if (dispose) disposeCharts()
}

function exportReport(format: ExportFormat = 'html') {
  emit('export', {
    report_type: 'leadership',
    report_date: appliedPeriodStart.value,
    period_start: appliedPeriodStart.value,
    period_end: appliedPeriodEnd.value,
    project_code: null,
    ship_no: null,
    parameters: {
      chart_type: chartType.value,
      summary_period_start: monthlyPeriodStart.value,
      summary_period_end: monthlyPeriodEnd.value,
    },
  }, format)
}

onMounted(async () => {
  const initialQuery = route.query
  await loadDefaultReport()
  routeStateReady = true
  if (pageActive.value && (routeRange || route.query !== initialQuery)) {
    if (!await restoreRouteState()) scheduleRefreshPoll()
  }
})
onActivated(async () => {
  pageActive.value = true
  await nextTick()
  if (!pageActive.value) return
  startResizeListening()
  if (routeStateReady && await restoreRouteState()) return
  if (!report.value && !loading.value) await loadDefaultReport()
  else if (chartsMissing()) renderCharts()
  resizeCharts()
  if (routeStateReady) refreshCachedReport()
  scheduleRefreshPoll(true)
})
onDeactivated(pausePage)
onBeforeUnmount(() => pausePage(true))
watch(chartType, async () => {
  await nextTick()
  renderCharts()
})
watch(() => route.query, async (query, previous) => {
  if (routeStateReady && pageActive.value && route.name === 'leadership-report') {
    const preserveDraft = query.start === previous.start && query.end === previous.end
    if (!await restoreRouteState({ preserveDraft }) && !preserveDraft) scheduleRefreshPoll()
  }
}, { deep: true })

const dailyTableColumns = [
  { key: 'c0', title: "序号", width: 56, align: 'center', fixed: 'left' },
  { key: 'c1', title: "日期", width: 152, align: 'left' },
  { key: 'c2', title: "任务数", width: 84, align: 'right' },
  { key: 'c3', title: "切割长度", width: 104, align: 'right' },
  { key: 'c4', title: "坡口长度", width: 104, align: 'right' },
  { key: 'c5', title: "切割时长", width: 104, align: 'center' },
] as const

const monthlyTableColumns = [
  { key: 'c0', title: "序号", width: 56, align: 'center', fixed: 'left' },
  { key: 'c1', title: "月份", width: 112, align: 'left' },
  { key: 'c2', title: "任务数", width: 84, align: 'right' },
  { key: 'c3', title: "切割长度", width: 104, align: 'right' },
  { key: 'c4', title: "坡口长度", width: 104, align: 'right' },
  { key: 'c5', title: "切割时长", width: 104, align: 'center' },
] as const
</script>

<template>
  <section class="page-hero report-hero report-hero--query leadership-hero" :style="{ '--leadership-hero-image': `url(${heroImage})` }">
    <div class="report-hero-copy leadership-hero__copy">
      <h1>领导报表</h1>
    </div>
    <StandardReportQuery
      v-model:start-date="periodStart"
      v-model:end-date="periodEnd"
      :chart-type="chartType"
      @update:chart-type="changeChartType"
      class="report-hero-query leadership-query-shell"
      aria-label="领导报表查询条件"
      :anchor-date="today"
      :loading="loading"
      :show-loading="!report"
      :has-data="Boolean(report)"
      :date-dirty="dateQueryDirty"
      :applied-range="report && snapshot.data_as_of ? `${appliedPeriodStart} 至 ${appliedPeriodEnd}` : ''"
      :export-disabled="loading || !report || !snapshot.data_as_of || snapshot.refreshing"
      :exporting="exporting"
      @submit="loadReport(false)"
      @export="exportReport"
      @validation-error="error = $event"
    />
  </section>

  <ReportFeedback v-if="loading && report" type="info" title="领导报表正在后台更新" message="当前显示上次成功结果。" />
  <ReportFeedback v-if="error && report" type="error" title="报表更新失败" :message="`${error}；仍显示 ${appliedPeriodStart} 至 ${appliedPeriodEnd} 的结果。`" action-label="重新查询" dismissible @action="retryReport" @dismiss="error = ''" />
  <ReportDataStatus :meta="report?.meta" label="统计截止" :busy="loading" @refresh="refreshAnalysisReport" />
  <ReportFeedback v-if="report && !report.meta?.report_data && (snapshot.stale || snapshot.refreshing)" :type="snapshot.stale ? 'warning' : 'info'" :title="snapshot.stale ? '当前统计已过期' : '后台刷新中'" :message="`统计截止：${snapshot.data_as_of || '未记录'}；${snapshot.refreshing ? '后台更新完成后自动替换。' : '当前保留上次统计，可重新查询。'}`" :action-label="snapshot.refreshing || loading ? '' : '重新查询'" @action="retryReport" />

  <section class="leadership-kpis" aria-label="区间汇总" :aria-busy="loading">
    <article class="leadership-kpi report-kpi-card kpi-blue">
      <span class="report-kpi-card__icon"><AppIcon name="tasks" size="22" tone="current" /></span>
      <div><span>完成任务</span><p><strong>{{ total.task_count ?? '--' }}</strong> 项</p><em v-if="metricChanges.taskCount" :class="metricChanges.taskCount.direction"><AppIcon :name="metricChanges.taskCount.direction === 'down' ? 'arrow-down' : metricChanges.taskCount.direction === 'flat' ? 'activity' : 'arrow-up'" size="12" tone="current" />{{ metricChanges.taskCount.value }}% <small>{{ comparisonLabel }}</small></em></div>
      <MetricSparkline class="metric-sparkline" :values="metricSeries.taskCount" label="每日完成任务趋势" />
    </article>
    <article class="leadership-kpi report-kpi-card kpi-teal">
      <span class="report-kpi-card__icon"><AppIcon name="ruler" size="22" tone="current" /></span>
      <div><span>切割长度</span><p><strong>{{ formatLength(total.cut_length_m) }}</strong> 米</p><em v-if="metricChanges.cutLength" :class="metricChanges.cutLength.direction"><AppIcon :name="metricChanges.cutLength.direction === 'down' ? 'arrow-down' : metricChanges.cutLength.direction === 'flat' ? 'activity' : 'arrow-up'" size="12" tone="current" />{{ metricChanges.cutLength.value }}% <small>{{ comparisonLabel }}</small></em></div>
      <MetricSparkline class="metric-sparkline" :values="metricSeries.cutLength" label="每日切割长度趋势" />
    </article>
    <article class="leadership-kpi report-kpi-card kpi-violet">
      <span class="report-kpi-card__icon"><AppIcon name="grid" size="22" tone="current" /></span>
      <div><span>坡口长度</span><p><strong>{{ formatLength(total.groove_length_m) }}</strong> 米</p><em v-if="metricChanges.grooveLength" :class="metricChanges.grooveLength.direction"><AppIcon :name="metricChanges.grooveLength.direction === 'down' ? 'arrow-down' : metricChanges.grooveLength.direction === 'flat' ? 'activity' : 'arrow-up'" size="12" tone="current" />{{ metricChanges.grooveLength.value }}% <small>{{ comparisonLabel }}</small></em></div>
      <MetricSparkline class="metric-sparkline" :values="metricSeries.grooveLength" label="每日坡口长度趋势" />
    </article>
    <article class="leadership-kpi report-kpi-card kpi-red">
      <span class="report-kpi-card__icon"><AppIcon name="clock" size="22" tone="current" /></span>
      <div><span>切割时长</span><p><strong>{{ formatDuration(total.work_time_seconds) }}</strong> <small>HH:mm:ss</small></p><em v-if="metricChanges.workTime" :class="metricChanges.workTime.direction"><AppIcon :name="metricChanges.workTime.direction === 'down' ? 'arrow-down' : metricChanges.workTime.direction === 'flat' ? 'activity' : 'arrow-up'" size="12" tone="current" />{{ metricChanges.workTime.value }}% <small>{{ comparisonLabel }}</small></em></div>
      <MetricSparkline class="metric-sparkline" :values="metricSeries.workTime" label="每日切割时长趋势" />
    </article>
  </section>

  <ReportTrendPanel ref="dailyPanel" title="区间每日明细与趋势" :subtitle="snapshot.data_as_of ? `${appliedPeriodStart} 至 ${appliedPeriodEnd}` : '按生产日统计'" chart-label="区间每日趋势图" :height="245" :loading="!snapshot.data_as_of && (loading || refreshLoading)" :has-data="dailyRecords.length > 0" :empty="Boolean(snapshot.data_as_of) && !dailyRecords.length" :error="!snapshot.data_as_of ? error : ''" loading-title="正在读取区间生产数据" empty-title="所选区间暂无已完成任务" empty-message="请调整日期范围，或等待生产数据完成后重新查询。" @retry="retryReport">
    <template #table>
        <AppReportTable flex-height :columns="dailyTableColumns" :items="dailyRecords" :row-key="row => row.period_start" :paginate="false" summary>
        <template #c0="{ row, index: rowIndex }">{{ rowIndex + 1 }}</template>
        <template #c1="{ row }">{{ formatChineseDate(row.period_start) }}</template>
        <template #c2="{ row }">{{ row.task_count }}</template>
        <template #c3="{ row }">{{ formatLength(row.cut_length_m) }}</template>
        <template #c4="{ row }">{{ formatLength(row.groove_length_m) }}</template>
        <template #c5="{ row }"><div class="duration-cell">{{ formatDuration(row.work_time_seconds) }}</div></template>
        <template #summary-c0>汇总</template>
        <template #summary-c1></template>
        <template #summary-c2>{{ total.task_count }}</template>
        <template #summary-c3>{{ formatLength(total.cut_length_m) }}</template>
        <template #summary-c4>{{ formatLength(total.groove_length_m) }}</template>
        <template #summary-c5>{{ formatDuration(total.work_time_seconds) }}</template>
        <template #empty>所选区间暂无已完成任务</template>
      </AppReportTable>
    </template>
  </ReportTrendPanel>

  <ReportTrendPanel ref="monthlyPanel" :title="monthlyTitle" :note="`${monthlyPeriodStart} 至 ${monthlyPeriodEnd}`" chart-label="近12个月月度趋势图" :height="245" :loading="monthlyLoading && !monthlyReport" :has-data="monthlyRecords.length > 0" :empty="Boolean(monthlyReport) && !monthlyRecords.length" :error="!monthlyReport ? error : ''" loading-title="正在读取近12个月趋势" loading-message="月度汇总独立加载，不影响当前区间明细。" empty-title="近12个月暂无已完成任务" empty-message="" @retry="loadDefaultReport">
    <template #table>
        <AppReportTable flex-height :columns="monthlyTableColumns" :items="monthlyRecords" :row-key="row => row.period_start" :paginate="false" summary>
        <template #c0="{ row, index: rowIndex }">{{ rowIndex + 1 }}</template>
        <template #c1="{ row }">{{ formatChineseMonth(row.period_start) }}</template>
        <template #c2="{ row }">{{ row.task_count }}</template>
        <template #c3="{ row }">{{ formatLength(row.cut_length_m) }}</template>
        <template #c4="{ row }">{{ formatLength(row.groove_length_m) }}</template>
        <template #c5="{ row }"><div class="duration-cell">{{ formatDuration(row.work_time_seconds) }}</div></template>
        <template #summary-c0>汇总</template>
        <template #summary-c1></template>
        <template #summary-c2>{{ monthlyTotal.task_count }}</template>
        <template #summary-c3>{{ formatLength(monthlyTotal.cut_length_m) }}</template>
        <template #summary-c4>{{ formatLength(monthlyTotal.groove_length_m) }}</template>
        <template #summary-c5>{{ formatDuration(monthlyTotal.work_time_seconds) }}</template>
        <template #empty>近12个月暂无已完成任务</template>
      </AppReportTable>
    </template>
  </ReportTrendPanel>

  <AnalysisPanel
    report-type="leadership"
    :report-date="appliedPeriodStart"
    :period-start="defaultMonthlyView ? '' : appliedPeriodStart"
    :period-end="defaultMonthlyView ? '' : appliedPeriodEnd"
    :summary-period-start="monthlyPeriodStart"
    :summary-period-end="monthlyPeriodEnd"
    :ready="analysisReady"
    :data-version="report?.analysis_source_version || ''"
    :leadership-report="report"
    :summary-data-version="monthlyReport?.analysis_summary_source_version || ''"
    :version-check-enabled="defaultMonthlyView"
    :prefetch-latest="!queryDateRange(route.query)"
    @refresh-report="refreshAnalysisReport"
  />

</template>

<style scoped>
.leadership-hero{position:relative;min-height:206px;display:grid;grid-template-columns:minmax(360px,.72fr) minmax(660px,1.28fr);grid-template-rows:1fr auto;align-items:center;gap:10px 28px;overflow:hidden;padding:24px 30px 16px;border:1px solid var(--theme-border-color);border-radius:14px;background:linear-gradient(90deg,rgb(var(--container-bg-color) / 0.98) 0%,rgb(var(--container-bg-color) / 0.91) 39%,rgb(var(--container-bg-color) / 0.04) 72%),var(--leadership-hero-image) center 48%/cover no-repeat;box-shadow:var(--theme-shadow);animation:surface-in .46s cubic-bezier(.2,.75,.25,1) both}.leadership-hero::after{content:"";position:absolute;inset:0;pointer-events:none;background:transparent}.leadership-hero__copy{position:relative;z-index:1}.leadership-kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr)); gap:16px; margin:16px 0}.leadership-kpi{--kpi-color:rgb(var(--info-color));--kpi-soft:rgb(var(--info-color) / .1);transition:transform .18s ease,box-shadow .18s ease;animation:surface-in .42s cubic-bezier(.2,.75,.25,1) both}.leadership-kpi:hover{transform:translateY(-2px);box-shadow:var(--theme-shadow)}.leadership-kpi:nth-child(2){animation-delay:.04s}.leadership-kpi:nth-child(3){animation-delay:.08s}.leadership-kpi:nth-child(4){animation-delay:.12s}.leadership-kpi.kpi-teal{--kpi-color:rgb(var(--info-color));--kpi-soft:rgb(var(--info-color) / .1)}.leadership-kpi.kpi-violet{--kpi-color:rgb(var(--primary-color));--kpi-soft:rgb(var(--primary-color) / .1)}.leadership-kpi.kpi-red{--kpi-color:var(--text-color-3);--kpi-soft:rgb(var(--info-color) / .1)}.leadership-kpi:hover .report-kpi-card__icon{transform:scale(1.08) rotate(-3deg)}.leadership-kpi>div{display:contents}.leadership-kpi>div>span{display:block;color:var(--text-color-1);font-size:var(--font-auxiliary);font-weight:700;white-space:normal;overflow-wrap:anywhere;grid-column:2}.leadership-kpi p{margin:0;color:var(--text-color-1); font-size:var(--font-auxiliary); grid-column:1/-1; min-width:0; overflow-wrap:anywhere}.leadership-kpi p strong{margin:0; font-size:21px}.leadership-kpi:nth-child(4) p strong{font-size:19px}.leadership-kpi p small{margin-left:3px;font-size:var(--font-auxiliary)}.leadership-kpi em{display:flex;align-items:center;gap:4px;color:rgb(var(--success-color));font-style:normal;font-size:var(--font-auxiliary);grid-column:1/-1;min-width:0;white-space:normal;overflow-wrap:anywhere;line-height:1.6;flex-wrap:wrap}.leadership-kpi em.down{color:rgb(var(--error-color))}.leadership-kpi em.flat{color:var(--muted)}.leadership-kpi em:not(.up):not(.down):not(.flat){color:var(--muted)}.leadership-kpi em small{margin-left:2px;color:var(--muted);font-size:inherit}.metric-sparkline{opacity:.78; width:58px; height:25px; grid-column:1/-1; justify-self:end}:deep(.report-trend-panel){border:1px solid var(--theme-border-color);box-shadow:var(--theme-shadow); border-radius:var(--report-panel-radius); margin-bottom:24px}:deep(.report-trend-panel__content){ gap:16px}:deep(.report-trend-panel__chart),:deep(.report-trend-panel__table){overflow:hidden;border-color:var(--theme-border-color);border-radius:6px}
@media(max-width:1180px){.leadership-hero{grid-template-columns:1fr;grid-template-rows:auto auto;padding:24px}.leadership-hero__copy{grid-row:auto}.leadership-kpis{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:720px){.leadership-hero{min-height:0;padding:22px 18px;background:linear-gradient(90deg,rgb(var(--container-bg-color) / 0.97),rgb(var(--container-bg-color) / 0.89)),var(--leadership-hero-image) center/cover}.leadership-kpis{grid-template-columns:1fr}:deep(.report-trend-panel__content){grid-template-columns:1fr;gap:16px}}
@media(prefers-reduced-motion:reduce){.leadership-hero,.leadership-kpi{animation:none}.leadership-kpi{transition:none}}


/* Shared report-page density. Keep leadership metrics aligned with equipment and workshop reports. */
.report-trend-panel { margin-bottom:24px; }
:deep(.report-trend-panel__table) {
  height:var(--report-trend-height)!important;
  overflow:hidden!important;
  scrollbar-gutter:auto;
}
</style>
