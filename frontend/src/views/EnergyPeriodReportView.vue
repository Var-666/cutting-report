<script setup lang="ts">
import { useEnergyReportQuery } from '../composables/useEnergyReportQuery'
import { useEnergyReportData } from '../composables/useEnergyReportData'
import { useEnergyReportCharts } from '../composables/useEnergyReportCharts'
import AppReportTable from '../components/AppReportTable.vue'
import type { ReportCreateRequest } from '../types/reports'

import type { ExportFormat } from '../types/api'


import { nextTick, onActivated, onBeforeUnmount, onDeactivated, watch } from 'vue'
import AnalysisPanel from '../components/AnalysisPanel.vue'
import AppIcon from '../components/AppIcon.vue'
import MetricSparkline from '../components/MetricSparkline.vue'
import ReportFeedback from '../components/ReportFeedback.vue'
import ReportTrendPanel from '../components/ReportTrendPanel.vue'
import StandardReportQuery from '../components/StandardReportQuery.vue'
import { formatChineseDateTime } from '../utils/dateDisplay.ts'
defineProps({ exporting: Boolean })
const emit = defineEmits<{ export: [payload: ReportCreateRequest, format: ExportFormat] }>()
const {
  anchorDate, periodStart, periodEnd, appliedPeriodStart, appliedPeriodEnd,
  summaryPeriodStart, summaryPeriodEnd, taskDay, monthlyReport, chartType, loading, summaryLoading, summaryRefreshError,
  error, pageActive, rangeQueryApplied, dateQueryDirty, analysisReady,
  loadRange, loadDefaultRanges, changeChartType, initializeReport, retryReport, refreshAnalysisReport,
} = useEnergyReportQuery()

const {
  taskRows, taskChartRows, summaryRows, summaryChartRows, month,
  units, summary, equipmentScopeLabel, energyConnected, configurationError,
  energyCoverage, deviceMetricSeries, summaryCountLabel, summarySubtitle, format,
  duration, valueText, summaryPeriod,
} = useEnergyReportData({ taskDay, monthlyReport, summaryPeriodStart, summaryPeriodEnd })
const { taskPanel, periodPanel, renderCharts, resizeCharts, disposeCharts, startResizeListening, stopResizeListening, chartsMissing } = useEnergyReportCharts({ taskDay, monthlyReport, taskChartRows, summaryChartRows, units, format, chartType, pageActive })

const energyChartOptions = [
  { value: 'bar_line', label: '柱线组合' },
  { value: 'bar', label: '双柱状图' },
  { value: 'line', label: '双折线图' },
]
const summaryTitle = '近12个月月度能耗汇总'
const summaryDateHeading = '月份'
const summaryChartLabel = '月度能耗图'
const summaryTableLabel = '近12个月月度能耗汇总表'
const summaryEmptyText = '近12个月暂无月度汇总'
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

watch(taskDay, async () => {
  await nextTick()
  renderCharts({ summary: false })
})
watch([monthlyReport, chartType], async () => {
  await nextTick()
  renderCharts()
})
onActivated(async () => {
  await nextTick()
  if (!pageActive.value) return
  startResizeListening()
  if (chartsMissing()) renderCharts()
  resizeCharts()
})
onDeactivated(stopResizeListening)
onBeforeUnmount(() => { stopResizeListening(); disposeCharts() })

const taskTableColumns = [
  { key: 'c0', title: "序号", width: 56, align: 'center', fixed: 'left' },
  { key: 'c1', title: "工程号", width: 76, align: 'left' },
  { key: 'c2', title: "船名", width: 160, align: 'left' },
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
        :show-loading="!taskDay"
        :has-data="Boolean(taskDay)"
        :date-dirty="dateQueryDirty"
        :export-disabled="loading || !taskDay"
        :exporting="exporting"
        @submit="loadRange(true)"
        @export="exportReport"
        @validation-error="error = $event"
      />
    </section>

    <ReportFeedback v-if="loading && taskDay" type="info" title="设备报表正在后台更新" message="当前显示上次加载的数据，更新完成后自动替换。" />
    <ReportFeedback v-if="error && (taskDay || monthlyReport)" type="error" title="设备报表更新失败" :message="`${error}；当前保留已成功加载的数据。`" action-label="重新查询" dismissible @action="retryReport" @dismiss="error = ''" />
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
      :loading="loading && !taskDay"
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
        <template #c1="{ row }">{{ row.project_code?.trim() || '--' }}</template>
        <template #c2="{ row }">{{ row.ship_no?.trim() || '--' }}</template>
        <template #c3="{ row }">{{ row.segment_name?.trim() || '--' }}</template>
        <template #c4="{ row }">{{ formatChineseDateTime(row.production_time) }}</template>
        <template #c5="{ row }">{{ duration(row.work_time) }}</template>
        <template #c6="{ row }"><span :title="row.energy_note || ''">{{ format(row.electricity_consumption) }}</span></template>
        <template #c7="{ row }"><span :title="row.energy_note || ''">{{ format(row.gas_consumption) }}</span></template>
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

    <ReportFeedback v-if="summaryRefreshError && monthlyReport" type="warning" title="月度能耗后台更新失败" :message="`${summaryRefreshError}；当前保留已有汇总。`" action-label="重新查询" @action="initializeReport()" />
    <ReportTrendPanel
      :height="245"
      ref="periodPanel"
      number="02"
      :title="summaryTitle"
      :subtitle="summarySubtitle"
      :chart-label="summaryChartLabel"
      :loading="summaryLoading && !monthlyReport"
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
.device-report-shell>.page-hero{position:relative;min-height:174px;display:flex;align-items:center;overflow:hidden;padding:25px 30px;border:1px solid var(--theme-border-color);border-radius:14px;background:linear-gradient(90deg,rgb(var(--container-bg-color) / 0.99) 0%,rgb(var(--container-bg-color) / 0.92) 39%,rgb(var(--container-bg-color) / 0.08) 69%),url('../assets/home-laser-cutting.webp') center 51%/cover no-repeat;box-shadow:var(--theme-shadow);animation:surface-in .46s cubic-bezier(.2,.75,.25,1) both}.device-report-shell>.page-hero::after{content:"";position:absolute;inset:0;pointer-events:none;background:transparent}.device-hero__copy{position:relative;z-index:1;max-width:540px}.device-breadcrumb{display:block;margin-bottom:7px;color:rgb(var(--info-color));font-size:var(--font-auxiliary)}.device-title-row{display:flex;align-items:flex-end;gap:14px}.device-title-row h2{margin:0;color:var(--text-color-1);font-size:var(--font-page-title);line-height:1.08;letter-spacing:1px}.device-title-row>span{padding-bottom:5px;color:rgb(var(--info-color));font-size:var(--font-auxiliary);font-weight:700;letter-spacing:3px}.device-query-shell{margin:10px 0;border:1px solid var(--theme-border-color)}.device-query-shell .report-query{width:100%;grid-template-columns:minmax(320px,1fr) minmax(180px,.55fr) 128px 128px;padding:0;border:0;background:transparent;box-shadow:none}.device-query-shell :deep(.report-period-picker){grid-column:auto}.device-query-shell :deep(.report-period-picker__trigger),.device-query-shell .report-query>button{height:38px;min-height:38px;border-radius:6px;font-size:var(--font-auxiliary)}.device-query-shell .report-query>button.primary{display:flex;align-items:center;justify-content:center;gap:6px;border-color:var(--theme-border-color);background:rgb(var(--info-color));box-shadow:var(--theme-shadow)}.device-kpis{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:16px;margin:16px 0}.device-kpi{--kpi-color:rgb(var(--info-color));--kpi-soft:rgb(var(--info-color) / .1);position:relative;min-width:0;min-height:108px;padding:12px;border:1px solid var(--theme-border-color);border-top:2px solid var(--kpi-color);border-radius:10px;background:rgb(var(--container-bg-color));box-shadow:var(--theme-shadow);transition:transform .18s ease,box-shadow .18s ease;animation:surface-in .42s cubic-bezier(.2,.75,.25,1) both;display:grid;grid-template-columns:35px minmax(0,1fr);align-items:center;gap:8px 12px}.device-kpi:hover{transform:translateY(-2px);box-shadow:var(--theme-shadow)}.device-kpi:nth-child(2){animation-delay:.04s}.device-kpi:nth-child(3){animation-delay:.08s}.device-kpi:nth-child(4){animation-delay:.12s}.device-kpi:nth-child(5){animation-delay:.16s}.device-kpi.kpi-red{--kpi-color:var(--text-color-3);--kpi-soft:rgb(var(--info-color) / .1)}.device-kpi.kpi-teal{--kpi-color:rgb(var(--info-color));--kpi-soft:rgb(var(--info-color) / .1)}.device-kpi.kpi-violet{--kpi-color:rgb(var(--primary-color));--kpi-soft:rgb(var(--primary-color) / .1)}.device-kpi.kpi-cyan{--kpi-color:rgb(var(--info-color));--kpi-soft:rgb(var(--info-color) / .1)}.device-kpi__icon{position:relative;z-index:1;width:35px;height:35px;display:grid;place-items:center;flex:0 0 auto;color:var(--kpi-color);border-radius:50%;background:var(--kpi-soft);transition:transform .2s ease}.device-kpi:hover .device-kpi__icon{transform:scale(1.08) rotate(-3deg)}.device-kpi>div{display:contents}.device-kpi>div>span{display:block;color:var(--text-color-1);font-size:var(--font-auxiliary);font-weight:700;white-space:normal;overflow-wrap:anywhere;grid-column:2}.device-kpi p{margin:0;color:var(--text-color-1);font-size:var(--font-auxiliary);grid-column:1/-1;min-width:0;overflow-wrap:anywhere}.device-kpi p strong{font-size:21px}.device-kpi p.device-duration strong{font-size:17px}.device-kpi p small{margin-left:2px;color:var(--muted);font-size:var(--font-auxiliary)}.device-kpi em{display:block;color:var(--muted);font-style:normal;font-size:var(--font-auxiliary);grid-column:1/-1;min-width:0;white-space:normal;overflow-wrap:anywhere;line-height:1.6}.device-sparkline{width:58px;height:25px;opacity:.72;grid-column:1/-1;justify-self:end}
:deep(.report-trend-panel){margin-bottom:24px;border:1px solid var(--theme-border-color);border-radius:var(--report-panel-radius);box-shadow:var(--theme-shadow)}:deep(.report-trend-panel__heading){min-height:47px;padding:0 16px;border-bottom:1px solid var(--theme-border-color);background:rgb(var(--container-bg-color))}:deep(.report-trend-panel__heading>div){gap:8px}:deep(.report-trend-panel__heading>div>span){width:28px;height:24px;display:grid;place-items:center;padding:0;color:rgb(var(--info-color));border:0;border-radius:7px;background:rgb(var(--info-color) / .1);font-size:var(--font-auxiliary);font-weight:700}:deep(.report-trend-panel__heading h2){color:var(--text-color-1);font-size:14px}:deep(.report-trend-panel__heading small){color:var(--muted);font-size:var(--font-auxiliary);font-weight:400}:deep(.report-trend-panel__heading p){color:var(--muted);font-size:var(--font-auxiliary)}:deep(.report-trend-panel__content){gap:16px}:deep(.report-trend-panel__chart),:deep(.report-trend-panel__table){height:var(--report-trend-height);overflow:hidden;border-color:var(--theme-border-color);border-radius:6px}
@media(max-width:1120px){.device-kpis{grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;margin:16px 0}.device-kpi:last-child{grid-column:1/-1}}
@media(max-width:900px){.device-query-shell .report-query{grid-template-columns:repeat(2,minmax(180px,1fr))}.device-query-shell :deep(.report-period-picker){grid-column:1/-1}:deep(.report-trend-panel__content){grid-template-columns:1fr;gap:16px}:deep(.report-trend-panel){--report-trend-height:270px!important}:deep(.report-trend-panel__chart),:deep(.report-trend-panel__table){height:var(--report-trend-height);border-radius:6px}}
@media(max-width:560px){.device-report-shell>.page-hero{min-height:0;padding:22px 18px;background:linear-gradient(90deg,rgb(var(--container-bg-color) / 0.97),rgb(var(--container-bg-color) / 0.89)),url('../assets/home-laser-cutting.webp') center/cover}.device-title-row{display:block}.device-title-row h2{font-size:var(--font-page-title-mobile)}.device-title-row>span{display:block;padding:6px 0 0}.device-query-shell .report-query,.device-kpis{grid-template-columns:1fr}.device-query-shell :deep(.report-period-picker),.device-kpi:last-child{grid-column:auto}}
@media(prefers-reduced-motion:reduce){.device-report-shell>.page-hero,.device-kpi{animation:none}.device-kpi{transition:none;display:grid;grid-template-columns:35px minmax(0,1fr);align-items:center;gap:8px 12px;padding:12px}}


/* Report tables use the same low-contrast header treatment across all report pages. */
.device-query-shell { position:relative; padding:12px 14px 13px; border-color:var(--theme-border-color); border-radius:12px; background:rgb(var(--container-bg-color)); box-shadow:var(--theme-shadow); }
.device-query-shell::before { content:""; position:absolute; top:-1px; left:18px; width:34px; height:3px; border-radius:0 0 999px 999px; background:rgb(var(--info-color)); }
.device-query-shell .standard-report-query { gap:10px; }
:deep(.report-trend-panel__table) {
  height:var(--report-trend-height)!important;
  overflow:hidden!important;
  scrollbar-gutter:auto;
}
</style>
