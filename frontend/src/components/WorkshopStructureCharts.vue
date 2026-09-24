<script setup>
import { BarChart, PieChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { init, use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { computed, nextTick, onActivated, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import AppIcon from './AppIcon.vue'

use([BarChart, PieChart, GridComponent, TooltipComponent, CanvasRenderer])

const props = defineProps({
  durationDistribution: { type: Array, default: () => [] },
  statusDistribution: { type: Array, default: () => [] },
  loading: Boolean,
})

const root = ref(null)
const durationElement = ref(null)
const statusElement = ref(null)
const durationColors = ['#287fcb', '#2ca9df', '#f2a43a', '#24a873', '#8b9aa5']
const statusColors = { confirmed: '#20a879', pending: '#ee9b2f' }
const durationItems = computed(() => props.durationDistribution.filter((item) => Number(item.count) > 0))
const statusItems = computed(() => props.statusDistribution.filter((item) => Number(item.count) > 0))
const durationTotal = computed(() => durationItems.value.reduce((sum, item) => sum + Number(item.count), 0))
const statusTotal = computed(() => statusItems.value.reduce((sum, item) => sum + Number(item.count), 0))
const hasData = computed(() => durationTotal.value > 0 || statusTotal.value > 0)
let durationChart = null
let statusChart = null
let resizeObserver = null

function percent(count, total) {
  return total ? `${(Number(count) / total * 100).toFixed(1)}%` : '0.0%'
}

function disposeCharts() {
  durationChart?.dispose()
  statusChart?.dispose()
  durationChart = null
  statusChart = null
}

function renderCharts() {
  if (!hasData.value) {
    disposeCharts()
    return
  }
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  if (durationElement.value && durationItems.value.length) {
    if (!durationChart || durationChart.getDom() !== durationElement.value) {
      durationChart?.dispose()
      durationChart = init(durationElement.value)
    }
    durationChart.setOption({
      animation: !reducedMotion,
      color: durationColors,
      tooltip: {
        trigger: 'item',
        formatter: ({ name, value, percent: ratio }) => `${name}<br>${value} 项（${ratio}%）`,
      },
      series: [{
        name: '任务时长',
        type: 'pie',
        radius: ['58%', '82%'],
        center: ['50%', '50%'],
        avoidLabelOverlap: true,
        label: { show: false },
        emphasis: { scaleSize: 5 },
        itemStyle: { borderColor: '#fff', borderWidth: 2 },
        data: durationItems.value.map((item) => ({ name: item.label, value: Number(item.count) })),
      }],
    }, true)
  }
  if (statusElement.value && statusItems.value.length) {
    if (!statusChart || statusChart.getDom() !== statusElement.value) {
      statusChart?.dispose()
      statusChart = init(statusElement.value)
    }
    statusChart.setOption({
      animation: !reducedMotion,
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      grid: { left: 38, right: 18, top: 28, bottom: 34 },
      xAxis: {
        type: 'category',
        data: statusItems.value.map((item) => item.label),
        axisTick: { show: false },
        axisLine: { lineStyle: { color: '#b7c9d3' } },
        axisLabel: { color: '#536f83', fontSize: 10 },
      },
      yAxis: {
        type: 'value',
        minInterval: 1,
        axisLabel: { color: '#718899', fontSize: 9 },
        splitLine: { lineStyle: { color: '#e7eef2' } },
      },
      series: [{
        name: '任务数',
        type: 'bar',
        barMaxWidth: 62,
        data: statusItems.value.map((item) => ({
          value: Number(item.count),
          itemStyle: { color: statusColors[item.key] || '#287fcb', borderRadius: [4, 4, 0, 0] },
        })),
        label: { show: true, position: 'top', color: '#274a62', fontSize: 12, fontWeight: 700 },
      }],
    }, true)
  }
}

async function refreshCharts() {
  await nextTick()
  renderCharts()
  durationChart?.resize()
  statusChart?.resize()
}

watch(
  () => [props.durationDistribution, props.statusDistribution, props.loading],
  refreshCharts,
  { deep: true },
)

onMounted(() => {
  resizeObserver = new ResizeObserver(() => {
    durationChart?.resize()
    statusChart?.resize()
  })
  if (root.value) resizeObserver.observe(root.value)
  refreshCharts()
})
onActivated(refreshCharts)
onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  disposeCharts()
})
</script>

<template>
  <section ref="root" class="workshop-structure" aria-labelledby="workshop-structure-title">
    <header class="workshop-structure__head">
      <div><span><AppIcon name="chart" :size="17" /></span><h2 id="workshop-structure-title">任务结构概览</h2></div>
      <p>基于当前筛选范围的全部任务</p>
    </header>
    <div v-if="loading && !hasData" class="workshop-structure__state" role="status">正在统计任务结构…</div>
    <div v-else-if="!hasData" class="workshop-structure__state">当前筛选范围暂无可统计任务</div>
    <div v-else class="workshop-structure__grid">
      <article>
        <header><div><AppIcon name="clock" :size="17" /><h3>任务时长分布</h3></div><span>{{ durationTotal }} 项</span></header>
        <div class="duration-visual">
          <div class="duration-donut">
            <div ref="durationElement" class="duration-chart" role="img" :aria-label="`任务时长分布，共 ${durationTotal} 项任务`"></div>
            <p><strong>{{ durationTotal }}</strong><span>总任务数</span></p>
          </div>
          <ul aria-label="任务时长分布明细">
            <li v-for="(item, index) in durationItems" :key="item.key"><i :style="{ background: durationColors[index] }"></i><span>{{ item.label }}</span><strong>{{ item.count }}</strong><small>{{ percent(item.count, durationTotal) }}</small></li>
          </ul>
        </div>
      </article>
      <article>
        <header><div><AppIcon name="activity" :size="17" /><h3>任务确认状态</h3></div><span>{{ statusTotal }} 项</span></header>
        <div ref="statusElement" class="status-chart" role="img" :aria-label="`任务确认状态，共 ${statusTotal} 项任务`"></div>
      </article>
    </div>
  </section>
</template>

<style scoped>
.workshop-structure{overflow:hidden;margin:0 0 10px;border:1px solid #dbe6ec;border-radius:10px;background:#fff;box-shadow:0 7px 22px rgba(22,62,89,.05)}
.workshop-structure__head{min-height:47px;display:flex;align-items:center;justify-content:space-between;gap:16px;padding:0 14px;border-bottom:1px solid #e5edf1;background:linear-gradient(90deg,#fff,#fbfdfe)}.workshop-structure__head>div,.workshop-structure article header>div{display:flex;align-items:center;gap:8px}.workshop-structure__head>div>span{width:28px;height:24px;display:grid;place-items:center;color:#237fb7;border-radius:7px;background:#eaf5fb}.workshop-structure__head h2{margin:0;color:#193c57;font-size:14px}.workshop-structure__head p{margin:0;color:#6f8798;font-size:9px}
.workshop-structure__grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;padding:11px 13px 13px}.workshop-structure article{min-width:0;overflow:hidden;border:1px solid #dfe9ee;border-radius:8px;background:#fbfdfe}.workshop-structure article header{min-height:42px;display:flex;align-items:center;justify-content:space-between;gap:12px;padding:0 13px;border-bottom:1px solid #e7eef2;background:#fff}.workshop-structure article h3{margin:0;color:#234b66;font-size:12px}.workshop-structure article header .app-icon{color:#168fad}.workshop-structure article header>span{color:#718899;font-size:9px}
.duration-visual{min-height:190px;display:grid;grid-template-columns:minmax(180px,.8fr) minmax(185px,1fr);align-items:center;gap:6px;padding:6px 12px 10px}.duration-donut{position:relative;min-width:0;height:174px}.duration-chart{width:100%;height:100%}.duration-donut>p{position:absolute;inset:50% auto auto 50%;display:flex;align-items:center;flex-direction:column;margin:0;pointer-events:none;transform:translate(-50%,-50%)}.duration-donut strong{color:#173f64;font-size:24px;line-height:1}.duration-donut span{margin-top:5px;color:#718899;font-size:9px}.duration-visual ul{display:grid;gap:9px;margin:0;padding:0;list-style:none}.duration-visual li{display:grid;grid-template-columns:8px minmax(74px,1fr) 24px 44px;align-items:center;gap:6px;color:#526d80;font-size:10px}.duration-visual li i{width:8px;height:8px;border-radius:50%}.duration-visual li strong{color:#244a65;text-align:right;font-size:11px}.duration-visual li small{color:#8094a2;text-align:right;font-size:9px}.status-chart{width:100%;height:190px}.workshop-structure__state{min-height:150px;display:grid;place-items:center;color:#718899;font-size:11px}
@media(max-width:900px){.workshop-structure__grid{grid-template-columns:1fr}}
@media(max-width:560px){.workshop-structure__head{align-items:flex-start;flex-direction:column;padding:10px 12px}.duration-visual{grid-template-columns:1fr}.duration-donut{height:160px}.duration-visual ul{padding:0 8px 6px}}
</style>
