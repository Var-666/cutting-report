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
  restoreRouteState, loadDefaultReport, scheduleRefreshPoll, retryReport, refreshAnalysisReport,
} = useLeadershipReportQuery({ route, router, today, routeRange, pageActive, renderCharts: options => renderCharts(options) })
const {
  dailyRecords, dailyChartRecords, metricSeries, monthlyRecords, total,
  monthlyTotal, roundNumber, formatLength, formatDuration,
  metricChanges, changeText, comparisonLabel,
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
    if (!await restoreRouteState({ preserveDraft })) scheduleRefreshPoll()
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
      <span class="report-hero-eyebrow eyebrow">MANAGEMENT OVERVIEW</span>
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
      :has-data="Boolean(report)"
      :date-dirty="dateQueryDirty"
      :export-disabled="loading || !report || !snapshot.data_as_of || snapshot.refreshing"
      :exporting="exporting"
      @submit="loadReport(false)"
      @export="exportReport"
      @validation-error="error = $event"
    />
  </section>

  <ReportFeedback v-if="error && report" type="error" title="报表更新失败" :message="error" action-label="重新查询" dismissible @action="retryReport" @dismiss="error = ''" />

  <section class="leadership-kpis" aria-label="区间汇总" :aria-busy="loading">
    <article class="leadership-kpi kpi-blue">
      <span class="leadership-kpi__icon"><AppIcon name="tasks" size="22" tone="current" /></span>
      <div><span>完成任务</span><p><strong>{{ total.task_count ?? '--' }}</strong> 项</p><em :class="metricChanges.taskCount?.direction"><AppIcon v-if="metricChanges.taskCount" :name="metricChanges.taskCount.direction === 'down' ? 'arrow-down' : metricChanges.taskCount.direction === 'flat' ? 'activity' : 'arrow-up'" size="12" tone="current" />{{ changeText(metricChanges.taskCount) }} <small>{{ comparisonLabel(metricChanges.taskCount) }}</small></em></div>
      <MetricSparkline class="metric-sparkline" :values="metricSeries.taskCount" label="每日完成任务趋势" />
    </article>
    <article class="leadership-kpi kpi-teal">
      <span class="leadership-kpi__icon"><AppIcon name="ruler" size="22" tone="current" /></span>
      <div><span>切割长度</span><p><strong>{{ formatLength(total.cut_length_m) }}</strong> 米</p><em :class="metricChanges.cutLength?.direction"><AppIcon v-if="metricChanges.cutLength" :name="metricChanges.cutLength.direction === 'down' ? 'arrow-down' : metricChanges.cutLength.direction === 'flat' ? 'activity' : 'arrow-up'" size="12" tone="current" />{{ changeText(metricChanges.cutLength) }} <small>{{ comparisonLabel(metricChanges.cutLength) }}</small></em></div>
      <MetricSparkline class="metric-sparkline" :values="metricSeries.cutLength" label="每日切割长度趋势" />
    </article>
    <article class="leadership-kpi kpi-violet">
      <span class="leadership-kpi__icon"><AppIcon name="grid" size="22" tone="current" /></span>
      <div><span>坡口长度</span><p><strong>{{ formatLength(total.groove_length_m) }}</strong> 米</p><em :class="metricChanges.grooveLength?.direction"><AppIcon v-if="metricChanges.grooveLength" :name="metricChanges.grooveLength.direction === 'down' ? 'arrow-down' : metricChanges.grooveLength.direction === 'flat' ? 'activity' : 'arrow-up'" size="12" tone="current" />{{ changeText(metricChanges.grooveLength) }} <small>{{ comparisonLabel(metricChanges.grooveLength) }}</small></em></div>
      <MetricSparkline class="metric-sparkline" :values="metricSeries.grooveLength" label="每日坡口长度趋势" />
    </article>
    <article class="leadership-kpi kpi-red">
      <span class="leadership-kpi__icon"><AppIcon name="clock" size="22" tone="current" /></span>
      <div><span>切割时长</span><p><strong>{{ formatDuration(total.work_time_seconds) }}</strong> <small>HH:mm:ss</small></p><em :class="metricChanges.workTime?.direction"><AppIcon v-if="metricChanges.workTime" :name="metricChanges.workTime.direction === 'down' ? 'arrow-down' : metricChanges.workTime.direction === 'flat' ? 'activity' : 'arrow-up'" size="12" tone="current" />{{ changeText(metricChanges.workTime) }} <small>{{ comparisonLabel(metricChanges.workTime) }}</small></em></div>
      <MetricSparkline class="metric-sparkline" :values="metricSeries.workTime" label="每日切割时长趋势" />
    </article>
  </section>

  <ReportTrendPanel ref="dailyPanel" number="01" title="区间每日明细与趋势" subtitle="查看所选时间段内每日任务与加工指标变化" chart-label="区间每日趋势图" :height="245" :loading="loading || (refreshLoading && !snapshot.data_as_of)" :has-data="dailyRecords.length > 0" :empty="Boolean(report) && !dailyRecords.length" :error="!report ? error : ''" loading-title="正在读取区间生产数据" loading-message="正在汇总任务、切割长度、坡口长度和切割时长。" empty-title="所选区间暂无已完成任务" empty-message="请调整日期范围，或等待生产数据完成后重新查询。" @retry="retryReport">
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

  <ReportTrendPanel ref="monthlyPanel" number="02" :title="monthlyTitle" subtitle="查看近12个月各项数据的对比趋势" :note="`${monthlyPeriodStart} 至 ${monthlyPeriodEnd}`" chart-label="近12个月月度趋势图" :height="245" :loading="monthlyLoading" :has-data="monthlyRecords.length > 0" :empty="Boolean(monthlyReport) && !monthlyRecords.length" :error="!monthlyReport ? error : ''" loading-title="正在读取近12个月趋势" loading-message="月度汇总独立加载，不影响当前区间明细。" empty-title="近12个月暂无已完成任务" empty-message="当前统计范围内还没有可汇总的生产任务。" @retry="loadDefaultReport">
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
    :summary-data-version="monthlyReport?.analysis_summary_source_version || ''"
    :version-check-enabled="defaultMonthlyView"
    @refresh-report="refreshAnalysisReport"
  />

</template>

<style scoped>
.leadership-hero{position:relative;min-height:206px;display:grid;grid-template-columns:minmax(360px,.72fr) minmax(660px,1.28fr);grid-template-rows:1fr auto;align-items:center;gap:10px 28px;overflow:hidden;padding:24px 30px 16px;border:1px solid var(--theme-border-color);border-radius:14px;background:linear-gradient(90deg,rgb(var(--container-bg-color) / 0.98) 0%,rgb(var(--container-bg-color) / 0.91) 39%,rgb(var(--container-bg-color) / 0.04) 72%),var(--leadership-hero-image) center 48%/cover no-repeat;box-shadow:var(--theme-shadow);animation:surface-in .46s cubic-bezier(.2,.75,.25,1) both}.leadership-hero::after{content:"";position:absolute;inset:0;pointer-events:none;background:transparent}.leadership-hero__copy,.leadership-hero .report-query{position:relative;z-index:1}.leadership-hero .eyebrow{display:block;margin:0 0 5px;color:rgb(var(--info-color));font-size:var(--font-auxiliary);font-weight:700;letter-spacing:1.4px}.leadership-hero h1{margin:0 0 7px;color:var(--text-color-1);font-size:var(--font-page-title);line-height:1.08;letter-spacing:1px}.leadership-hero .report-query{grid-column:2;grid-row:2;width:100%;grid-template-columns:minmax(145px,1fr) minmax(145px,1fr) 120px 118px 126px;gap:8px;padding:11px 12px;border-color:var(--theme-border-color);border-radius:9px;background:rgb(var(--container-bg-color));box-shadow:var(--theme-shadow);backdrop-filter:blur(5px)}.leadership-hero :deep(.report-period-picker__label),.leadership-hero .report-query label>span{margin-bottom:5px;font-size:var(--font-auxiliary)}.leadership-hero :deep(.report-period-picker__trigger),.leadership-hero :deep(.app-select__trigger),.leadership-hero .report-query>button{height:36px;min-height:36px;border-radius:6px;font-size:var(--font-auxiliary)}.leadership-hero .report-query>button.primary{border-color:var(--theme-border-color);background:rgb(var(--info-color));box-shadow:var(--theme-shadow)}.leadership-kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr))}.leadership-kpi{--kpi-color:rgb(var(--info-color));--kpi-soft:rgb(var(--info-color) / .1);position:relative;min-width:0;border:1px solid var(--theme-border-color);border-top:2px solid var(--kpi-color);background:rgb(var(--container-bg-color));box-shadow:var(--theme-shadow);transition:transform .18s ease,box-shadow .18s ease;animation:surface-in .42s cubic-bezier(.2,.75,.25,1) both}.leadership-kpi:hover{transform:translateY(-2px);box-shadow:var(--theme-shadow)}.leadership-kpi:nth-child(2){animation-delay:.04s}.leadership-kpi:nth-child(3){animation-delay:.08s}.leadership-kpi:nth-child(4){animation-delay:.12s}.leadership-kpi.kpi-teal{--kpi-color:rgb(var(--info-color));--kpi-soft:rgb(var(--info-color) / .1)}.leadership-kpi.kpi-violet{--kpi-color:rgb(var(--primary-color));--kpi-soft:rgb(var(--primary-color) / .1)}.leadership-kpi.kpi-red{--kpi-color:var(--text-color-3);--kpi-soft:rgb(var(--info-color) / .1)}.leadership-kpi__icon{position:relative;z-index:1;display:grid;place-items:center;flex:0 0 auto;color:var(--kpi-color);border-radius:50%;background:var(--kpi-soft);transition:transform .2s ease}.leadership-kpi:hover .leadership-kpi__icon{transform:scale(1.08) rotate(-3deg)}.leadership-kpi>div{display:contents}.leadership-kpi>div>span{display:block;color:var(--text-color-1);font-size:var(--font-auxiliary);font-weight:700;white-space:normal;overflow-wrap:anywhere;grid-column:2}.leadership-kpi p{margin:0;color:var(--text-color-1)}.leadership-kpi p strong{margin:0}.leadership-kpi:nth-child(4) p strong{font-size:19px}.leadership-kpi p small{margin-left:3px;font-size:var(--font-auxiliary)}.leadership-kpi em{display:flex;align-items:center;gap:4px;color:rgb(var(--success-color));font-style:normal;font-size:var(--font-auxiliary);grid-column:1/-1;min-width:0;white-space:normal;overflow-wrap:anywhere;line-height:1.6;flex-wrap:wrap}.leadership-kpi em.down{color:rgb(var(--error-color))}.leadership-kpi em.flat{color:var(--muted)}.leadership-kpi em:not(.up):not(.down):not(.flat){color:var(--muted)}.leadership-kpi em small{margin-left:2px;color:var(--muted);font-size:inherit}.metric-sparkline{opacity:.78}.global-error{margin:12px 0 0;border-radius:9px}:deep(.report-trend-panel){margin-bottom:12px;border:1px solid var(--theme-border-color);box-shadow:var(--theme-shadow)}:deep(.report-trend-panel__heading){border-bottom:1px solid var(--theme-border-color);background:rgb(var(--container-bg-color))}:deep(.report-trend-panel__heading>div){gap:8px}:deep(.report-trend-panel__heading>div>span){width:28px;height:24px;display:grid;place-items:center;padding:0;color:rgb(var(--info-color));border:0;border-radius:7px;background:rgb(var(--info-color) / .1);font-size:var(--font-auxiliary);font-weight:700}:deep(.report-trend-panel__heading h2){color:var(--text-color-1);font-size:14px}:deep(.report-trend-panel__heading small){color:var(--muted);font-size:var(--font-auxiliary);font-weight:400}:deep(.report-trend-panel__heading p){color:var(--muted);font-size:var(--font-auxiliary)}:deep(.report-trend-panel__content){gap:10px}:deep(.report-trend-panel__chart),:deep(.report-trend-panel__table){overflow:hidden;border-color:var(--theme-border-color);border-radius:6px}:deep(table.content-table th),:deep(table.content-table td){height:35px!important;padding:6px 8px!important;font-size:var(--font-auxiliary)!important}:deep(table.content-table thead th){letter-spacing:.15px!important}:deep(table.content-table tfoot th){color:var(--text-color-1)!important;border-top:1px solid var(--theme-border-color)!important;background:rgb(var(--info-color) / .1)!important}
@media(max-width:1180px){.leadership-hero{grid-template-columns:1fr;grid-template-rows:auto auto;padding:24px}.leadership-hero__copy{grid-row:auto}.leadership-hero .report-query{grid-column:1;grid-row:auto}.leadership-kpis{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:720px){.leadership-hero{min-height:0;padding:22px 18px;background:linear-gradient(90deg,rgb(var(--container-bg-color) / 0.97),rgb(var(--container-bg-color) / 0.89)),var(--leadership-hero-image) center/cover}.leadership-hero h1{font-size:var(--font-page-title-mobile)}.leadership-hero .report-query{grid-template-columns:1fr}.leadership-hero :deep(.report-period-picker){grid-column:1}.leadership-kpis{grid-template-columns:1fr}:deep(.report-trend-panel__content){grid-template-columns:1fr}}
@media(prefers-reduced-motion:reduce){.leadership-hero,.leadership-kpi{animation:none}.leadership-kpi{transition:none;display:grid;grid-template-columns:35px minmax(0,1fr);align-items:center;gap:8px 12px}}


/* Shared report-page density. Keep leadership metrics aligned with equipment and workshop reports. */
.leadership-kpis { gap:16px; margin:16px 0; }
.leadership-kpi { min-height:108px; padding:12px; border-radius:var(--report-panel-radius); display:grid; grid-template-columns:35px minmax(0,1fr); align-items:center; gap:8px 12px; }
.leadership-kpi__icon { width:35px; height:35px; }
.leadership-kpi p { font-size:var(--font-auxiliary); grid-column:1/-1; min-width:0; overflow-wrap:anywhere; }
.leadership-kpi p strong { font-size:21px; }
.metric-sparkline { width:58px; height:25px; grid-column:1/-1; justify-self:end; }
.report-trend-panel { margin-bottom:24px; }
:deep(.report-trend-panel) { border-radius:var(--report-panel-radius); margin-bottom:24px; }
:deep(.report-trend-panel__heading) { min-height:47px; padding:0 16px; }
:deep(.report-trend-panel__content) { gap:16px; }
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
@media(max-width:720px) {
  :deep(.report-trend-panel__content) { grid-template-columns:1fr; gap:16px; }
}
</style>
