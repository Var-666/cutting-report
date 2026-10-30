import type { Ref } from 'vue'
import type { LeadershipPeriodRecord, LeadershipReport } from '../types/reports'
import type { BarSeriesOption, LineSeriesOption, EChartsOption } from 'echarts'
import type { EChartsType as ECharts } from 'echarts/core'
import { ref } from 'vue'
import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { init, use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import ReportTrendPanel from '../components/ReportTrendPanel.vue'
import { createReportChartGrid } from '../utils/reportChartTheme'
import { useReportChartTheme } from './useReportChartTheme'

export function useLeadershipReportCharts({ report, monthlyReport, dailyChartRecords, monthlyRecords, chartType, pageActive, roundNumber, formatLength }: {
  report: Ref<LeadershipReport | null>
  monthlyReport: Ref<LeadershipReport | null>
  dailyChartRecords: Ref<LeadershipPeriodRecord[]>
  monthlyRecords: Ref<LeadershipPeriodRecord[]>
  chartType: Ref<string>
  pageActive: Ref<boolean>
  roundNumber: (value: unknown, digits?: number) => number
  formatLength: (value: unknown) => string
}) {
  use([BarChart, LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])
  type TrendSeries = Omit<LineSeriesOption, 'type'> & Pick<BarSeriesOption, 'barMaxWidth'> & { type: 'bar' | 'line' }
  const chartTheme = useReportChartTheme(() => { disposeCharts(); renderCharts() })
  const dailyPanel = ref<InstanceType<typeof ReportTrendPanel> | null>(null)
  const monthlyPanel = ref<InstanceType<typeof ReportTrendPanel> | null>(null)
  let dailyChart: ECharts | null = null
  let monthlyChart: ECharts | null = null
  let resizeListening = false
  let chartResizeObserver: ResizeObserver | undefined

  function applyChartType(series: TrendSeries): TrendSeries {
    if (chartType.value === 'bar_line') return series
    const result = { ...series }
    delete result.areaStyle
    if (chartType.value === 'bar') {
      result.type = 'bar'
      delete result.smooth
    } else {
      result.type = 'line'
      result.smooth = true
      if (chartType.value === 'area') result.areaStyle = { opacity: 0.16 }
    }
    return result
  }

  function renderCharts({ monthly = true } = {}) {
    if (!pageActive.value) return
    const dailyChartElement = dailyPanel.value?.getChartElement()
    const monthlyChartElement = monthlyPanel.value?.getChartElement()
    if (dailyChartElement && report.value) {
      dailyChart ||= init(dailyChartElement, chartTheme.darkMode.value ? 'dark' : undefined)
      const records = dailyChartRecords.value
      dailyChart.setOption<EChartsOption>({
        backgroundColor: 'transparent',
        tooltip: { trigger: 'axis', backgroundColor: chartTheme.vars.value.popoverColor, borderColor: chartTheme.vars.value.borderColor, textStyle: { color: chartTheme.vars.value.textColor1 } },
        legend: { data: ['切割长度', '坡口长度', '切割工时'] },
        grid: createReportChartGrid(),
        xAxis: { type: 'category', data: records.map((row) => row.period_start.slice(5)), axisLabel: { hideOverlap: true } },
        yAxis: [
          { type: 'value', name: '长度（米）', splitLine: { lineStyle: { color: chartTheme.vars.value.dividerColor } } },
          { type: 'value', name: '工时（小时）', splitLine: { show: false } },
        ],
        series: ([
          { name: '切割长度', type: 'bar', data: records.map((row) => roundNumber(row.cut_length_m)), tooltip: { valueFormatter: (value) => `${formatLength(value)} 米` }, itemStyle: { color: chartTheme.colors.value.primary } },
          { name: '坡口长度', type: 'bar', data: records.map((row) => roundNumber(row.groove_length_m)), tooltip: { valueFormatter: (value) => `${formatLength(value)} 米` }, itemStyle: { color: chartTheme.colors.value.secondary } },
          { name: '切割工时', type: 'line', yAxisIndex: 1, smooth: true, data: records.map((row) => roundNumber(Number(row.work_time_seconds) / 3600)), tooltip: { valueFormatter: (value) => `${formatLength(value)} 小时` }, itemStyle: { color: chartTheme.colors.value.accent } },
        ] satisfies TrendSeries[]).map(applyChartType),
      }, true)
    }
    if (monthly && monthlyChartElement && monthlyReport.value) {
      monthlyChart ||= init(monthlyChartElement, chartTheme.darkMode.value ? 'dark' : undefined)
      const records = [...monthlyRecords.value].sort((left, right) => left.period_start.localeCompare(right.period_start))
      monthlyChart.setOption<EChartsOption>({
        backgroundColor: 'transparent',
        tooltip: { trigger: 'axis', backgroundColor: chartTheme.vars.value.popoverColor, borderColor: chartTheme.vars.value.borderColor, textStyle: { color: chartTheme.vars.value.textColor1 } },
        legend: { data: ['切割长度', '坡口长度', '完成任务'] },
        grid: createReportChartGrid(),
        xAxis: { type: 'category', data: records.map((row) => row.period_start.slice(0, 7)), axisLabel: { hideOverlap: true } },
        yAxis: [
          { type: 'value', name: '长度（米）', splitLine: { lineStyle: { color: chartTheme.vars.value.dividerColor } } },
          { type: 'value', name: '任务数', splitLine: { show: false } },
        ],
        series: ([
          { name: '切割长度', type: 'bar', data: records.map((row) => roundNumber(row.cut_length_m)), tooltip: { valueFormatter: (value) => `${formatLength(value)} 米` }, itemStyle: { color: chartTheme.colors.value.primary } },
          { name: '坡口长度', type: 'bar', data: records.map((row) => roundNumber(row.groove_length_m)), tooltip: { valueFormatter: (value) => `${formatLength(value)} 米` }, itemStyle: { color: chartTheme.colors.value.secondary } },
          { name: '完成任务', type: 'line', yAxisIndex: 1, smooth: true, data: records.map((row) => row.task_count), itemStyle: { color: chartTheme.colors.value.accent } },
        ] satisfies TrendSeries[]).map(applyChartType),
      }, true)
    }
  }

  function resizeCharts() {
    if (pageActive.value) {
      dailyChart?.resize()
      monthlyChart?.resize()
    }
  }

  function disposeCharts() {
    dailyChart?.dispose()
    monthlyChart?.dispose()
    dailyChart = null
    monthlyChart = null
  }

  function startResizeListening() {
    if (resizeListening) return
    resizeListening = true
    window.addEventListener('resize', resizeCharts)
    chartResizeObserver = new ResizeObserver(resizeCharts)
    for (const panel of [dailyPanel, monthlyPanel]) {
      const element = panel.value?.getChartElement()
      if (element) chartResizeObserver.observe(element)
    }
  }

  function stopResizeListening() {
    if (!resizeListening) return
    resizeListening = false
    window.removeEventListener('resize', resizeCharts)
    chartResizeObserver?.disconnect()
    chartResizeObserver = undefined
  }

  function chartsMissing() { return !dailyChart || Boolean(monthlyReport.value && !monthlyChart) }

  return { dailyPanel, monthlyPanel, renderCharts, resizeCharts, disposeCharts, startResizeListening, stopResizeListening, chartsMissing }
}
