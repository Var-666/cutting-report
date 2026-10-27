<script setup lang="ts">
import type { PropType } from 'vue'
import type { EChartsOption, SeriesOption } from 'echarts'
import type { EChartsType as ECharts } from 'echarts/core'
import type { WeldingReport } from '../types/welding'

import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { init, use } from 'echarts/core'
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { SVGRenderer } from 'echarts/renderers'
import { useReportChartTheme } from '../composables/useReportChartTheme.ts'

const props = defineProps({ report: { type: Object as PropType<WeldingReport>, required: true }, kind: { type: String, required: true } })
use([BarChart, LineChart, PieChart, GridComponent, LegendComponent, TooltipComponent, SVGRenderer])
const first = ref<HTMLElement | null>(null)
const second = ref<HTMLElement | null>(null)
let charts: ECharts[] = []
let observer: ResizeObserver | undefined
let observed: HTMLElement[] = []
let mounted = false
let drawnKey = ''
let drawnKind = ''
const { darkMode, colors } = useReportChartTheme(() => draw(true))
const processTypes = [['flat_length_m', '平焊'], ['vertical_length_m', '立焊'], ['flat_wrap_length_m', '平焊包角'], ['vertical_wrap_length_m', '立焊包角'], ['unknown_length_m', '未知类型']]
// Compare only plotted values; paginated rows and snapshot metadata do not affect these charts.
const chartKey = computed(() => {
  const data = props.report
  if (props.kind === 'parts') return JSON.stringify([props.kind, (data.projects || []).map(row => [row.project_name || row.project_no || '未填写项目', row.cumulative_actual_length_m, row.part_count])])
  if (props.kind === 'equipment') return JSON.stringify([props.kind, (data.device_distribution || []).map(row => [row.device_no, row.weld_length_m]), (data.equipment || []).map(row => [row.device_no, row.robot_no, row.weld_length_m, row.working_hours])])
  return JSON.stringify([props.kind, (data.daily || []).map(row => [row.production_date, row.weld_length_m, ...(props.kind === 'daily' ? [row.involved_part_count] : [])]), processTypes.map(([key]) => data.summary?.[key] ?? 0)])
})
const bar = (names: (string | number)[], values: (number | null)[], unit: string, seriesName: string): EChartsOption & { series: SeriesOption[] } => ({ animation: false, grid: { top: 40, right: 28, bottom: 70, left: 70 }, tooltip: { trigger: 'axis' }, xAxis: { type: 'category', data: names, axisLabel: { rotate: names.length > 8 ? 25 : 0 } }, yAxis: { type: 'value', name: unit }, series: [{ type: 'bar', name: seriesName, data: values, itemStyle: { color: colors.value.primary } }] })
function disposeCharts() { charts.forEach(chart => chart.dispose()); charts = []; drawnKey = ''; drawnKind = '' }
function draw(rebuild = false) {
  if (!mounted) return
  if (!first.value || !second.value) {
    if (observed.length) observer?.disconnect()
    observed = []
    disposeCharts()
    return
  }
  const elements = [first.value, second.value]
  if (rebuild || charts.some((chart, index) => chart.getDom() !== elements[index])) disposeCharts()
  if (!charts.length) charts = elements.map(element => init(element, darkMode.value ? 'dark' : undefined, { renderer: 'svg' }))
  if (observer && elements.some((element, index) => observed[index] !== element)) {
    if (observed.length) observer.disconnect()
    elements.forEach(element => observer!.observe(element))
    observed = elements
  }
  if (drawnKey === chartKey.value) return
  const update = { notMerge: drawnKind !== props.kind }
  const data = props.report
  if (props.kind === 'parts') {
    const rows = data.projects || []
    charts[0].setOption(bar(rows.map(row => row.project_name || row.project_no || '未填写项目'), rows.map(row => row.cumulative_actual_length_m), 'm', '涉及工件累计实际长度'), update)
    charts[1].setOption(bar(rows.map(row => row.project_name || row.project_no || '未填写项目'), rows.map(row => row.part_count), '件', '项目涉及工件数'), update)
  } else if (props.kind === 'equipment') {
    const rows = data.equipment || []
    const devices = data.device_distribution || []
    charts[0].setOption(bar(devices.map(row => String(row.device_no ?? '未记录')), devices.map(row => row.weld_length_m), 'm', '设备焊接长度'), update)
    const option = bar(rows.map(row => `${row.device_no ?? '未记录'} / ${row.robot_no ?? '未记录'}`), rows.map(row => row.weld_length_m), 'm', '工位焊接长度')
    option.yAxis = [{ type: 'value', name: 'm' }, { type: 'value', name: 'h' }]
    option.legend = { top: 0 }
    option.series.push({ type: 'line', name: '累计作业用时', yAxisIndex: 1, data: rows.map(row => row.working_hours) })
    charts[1].setOption(option, update)
  } else {
    const rows = data.daily || []
    const trend = bar(rows.map(row => row.production_date), rows.map(row => row.weld_length_m), 'm', '每日焊接长度')
    if (props.kind === 'daily') {
      trend.yAxis = [{ type: 'value', name: 'm' }, { type: 'value', name: '件' }]
      trend.legend = { top: 0 }
      trend.series.push({ type: 'line', name: '参与工件数', yAxisIndex: 1, data: rows.map(row => row.involved_part_count) })
    }
    charts[0].setOption(trend, update)
    const summary = data.summary || {}
    charts[1].setOption<EChartsOption>({ animation: false, tooltip: { trigger: 'item', valueFormatter: value => `${value} m` }, legend: { bottom: 0 }, series: [{ type: 'pie', radius: ['35%', '65%'], center: ['50%', '42%'], data: processTypes.map(([key, name]) => ({ name, value: summary[key] ?? 0 })).filter(item => item.value > 0) }] }, update)
  }
  drawnKey = chartKey.value
  drawnKind = props.kind
}
watch([chartKey, first, second], () => draw(), { flush: 'post' })
onMounted(() => { mounted = true; observer = new ResizeObserver(() => charts.forEach(chart => chart.resize())); draw() })
onBeforeUnmount(() => { mounted = false; observer?.disconnect(); observed = []; disposeCharts() })
</script>
<template>
  <div class="welding-charts">
    <section class="panel"><h2>{{ kind === 'parts' ? '项目涉及工件累计产出' : kind === 'equipment' ? '设备焊接长度对比' : '按日焊接长度趋势' }}</h2><div ref="first" class="welding-chart" role="img" aria-label="生产趋势或产出对比图"></div></section>
    <section class="panel"><h2>{{ kind === 'parts' ? '各项目涉及工件数' : kind === 'equipment' ? '设备机器人工位长度与用时' : '焊接工艺产出结构' }}</h2><div ref="second" class="welding-chart" role="img" aria-label="工艺结构或设备工位对比图"></div></section>
  </div>
</template>
<style scoped>
.welding-charts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.welding-charts section{padding:16px;min-width:0}.welding-charts h2{margin:0 0 12px;font-size:14px;color:var(--text-color-1)}.welding-chart{width:100%;height:290px}@media(max-width:900px){.welding-charts{grid-template-columns:1fr}}
</style>
