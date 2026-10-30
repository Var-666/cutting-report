import type { Ref } from 'vue'
import type { EnergyPeriodReport, EnergyTask, TaskEnergyReport } from '../types/energy'
import type { EChartsType as ECharts } from 'echarts/core'
import type { EChartsOption } from 'echarts'
import { ref } from 'vue'
import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { init, use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import ReportTrendPanel from '../components/ReportTrendPanel.vue'
import { createReportChartGrid } from '../utils/reportChartTheme'
import { useReportChartTheme } from './useReportChartTheme'

export function useEnergyReportCharts({ taskDay, monthlyReport, taskChartRows, summaryChartRows, units, format, chartType, pageActive }: {
  taskDay: Ref<TaskEnergyReport | null>
  monthlyReport: Ref<EnergyPeriodReport | null>
  taskChartRows: Ref<EnergyTask[]>
  summaryChartRows: Ref<EnergyPeriodReport['rows']>
  units: Ref<TaskEnergyReport['units']>
  format: (value: unknown, digits?: number) => string
  chartType: Ref<string>
  pageActive: Ref<boolean>
}) {
  use([BarChart, LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])
  const chartTheme = useReportChartTheme(() => { disposeCharts(); renderCharts() })
  const taskPanel = ref<InstanceType<typeof ReportTrendPanel> | null>(null)
  const periodPanel = ref<InstanceType<typeof ReportTrendPanel> | null>(null)
  let taskChart: ECharts | null = null
  let periodChart: ECharts | null = null
  let resizeListening = false
  let chartResizeObserver: ResizeObserver | undefined

  function chartValue(value: unknown) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return null
    return Number(Number(value).toFixed(1))
  }

  function chartOption(rows: { electricity: number | null; gas: number | null }[], labels: string[]): EChartsOption {
    const line = chartType.value === 'line'
    return {
      backgroundColor: 'transparent',
        tooltip: { trigger: 'axis', backgroundColor: chartTheme.vars.value.popoverColor, borderColor: chartTheme.vars.value.borderColor, textStyle: { color: chartTheme.vars.value.textColor1 } },
      legend: { data: ['电耗', '气耗'] },
      grid: createReportChartGrid(),
      xAxis: { type: 'category', data: labels, axisLabel: { hideOverlap: true } },
      yAxis: [
        { type: 'value', name: `电耗\n${units.value.electricity}`, splitLine: { lineStyle: { color: chartTheme.vars.value.dividerColor } } },
        { type: 'value', name: `气耗\n${units.value.gas}`, splitLine: { show: false } },
      ],
      series: [
        { name: '电耗', type: line ? 'line' : 'bar', smooth: line, yAxisIndex: 0, data: rows.map((row) => chartValue(row.electricity)), tooltip: { valueFormatter: (value) => format(value) }, itemStyle: { color: chartTheme.colors.value.primary } },
        { name: '气耗', type: chartType.value === 'bar' ? 'bar' : 'line', smooth: chartType.value !== 'bar', yAxisIndex: 1, data: rows.map((row) => chartValue(row.gas)), tooltip: { valueFormatter: (value) => format(value) }, itemStyle: { color: chartType.value === 'bar_line' ? chartTheme.colors.value.accent : chartTheme.colors.value.secondary } },
      ],
    }
  }

  function renderCharts({ summary: renderSummary = true } = {}) {
    if (!pageActive.value) return
    const taskChartElement = taskPanel.value?.getChartElement()
    const periodChartElement = periodPanel.value?.getChartElement()
    if (taskChartElement && taskDay.value) {
      taskChart ||= init(taskChartElement, chartTheme.darkMode.value ? 'dark' : undefined)
      const rows = taskChartRows.value.map((row) => ({ electricity: row.electricity_consumption, gas: row.gas_consumption }))
      const labels = taskChartRows.value.map((row) => `${String(row.production_time || '').slice(5, 16).replace('T', ' ')} ${row.segment_name || ''}`)
      taskChart.setOption(chartOption(rows, labels), true)
    }
    if (renderSummary && periodChartElement && monthlyReport.value) {
      periodChart ||= init(periodChartElement, chartTheme.darkMode.value ? 'dark' : undefined)
      const rows = summaryChartRows.value.map((row) => ({ electricity: row.electricity?.value, gas: row.gas?.value }))
      const labels = summaryChartRows.value.map((row) => row.report_date)
      periodChart.setOption(chartOption(rows, labels), true)
    }
  }

  function resizeCharts() { if (pageActive.value) { taskChart?.resize(); periodChart?.resize() } }

  function disposeCharts() {
    taskChart?.dispose()
    periodChart?.dispose()
    taskChart = null
    periodChart = null
  }

  function startResizeListening() {
    if (resizeListening) return
    resizeListening = true
    window.addEventListener('resize', resizeCharts)
    chartResizeObserver = new ResizeObserver(resizeCharts)
    for (const panel of [taskPanel, periodPanel]) {
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

  function chartsMissing() { return !taskChart || Boolean(monthlyReport.value && !periodChart) }

  return { taskPanel, periodPanel, renderCharts, resizeCharts, disposeCharts, startResizeListening, stopResizeListening, chartsMissing }
}
