<script setup lang="ts">
import type { EChartsOption } from 'echarts'
import type { EChartsType } from 'echarts/core'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { init, use } from 'echarts/core'
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent, DataZoomComponent } from 'echarts/components'
import { SVGRenderer } from 'echarts/renderers'
import { useReportChartTheme } from '../composables/useReportChartTheme'

const props = defineProps<{ option: EChartsOption; label: string }>()
use([BarChart, LineChart, PieChart, GridComponent, LegendComponent, TooltipComponent, DataZoomComponent, SVGRenderer])
const element = ref<HTMLElement | null>(null)
let chart: EChartsType | undefined
let observer: ResizeObserver | undefined
const { darkMode, vars } = useReportChartTheme(() => { dispose(); draw() })
function dispose() { chart?.dispose(); chart = undefined }
function draw() {
  if (!element.value || !observer) return
  chart ||= init(element.value, darkMode.value ? 'dark' : undefined, { renderer: 'svg' })
  chart.setOption({ ...props.option, animation: false, backgroundColor: 'transparent', textStyle: { color: vars.value.textColor2, fontFamily: 'inherit' }, tooltip: { ...props.option.tooltip, backgroundColor: vars.value.popoverColor, borderColor: vars.value.borderColor, textStyle: { color: vars.value.textColor1 } } }, { replaceMerge: ['series', 'xAxis', 'yAxis', 'dataZoom'] })
}
watch(() => props.option, draw, { flush: 'post' })
onMounted(() => { observer = new ResizeObserver(() => chart?.resize()); observer.observe(element.value!); draw() })
onBeforeUnmount(() => { observer?.disconnect(); observer = undefined; dispose() })
</script>

<template><div ref="element" class="welding-chart" role="img" :aria-label="label"></div></template>
<style scoped>.welding-chart{width:100%;height:100%;min-height:240px;min-width:0}</style>
