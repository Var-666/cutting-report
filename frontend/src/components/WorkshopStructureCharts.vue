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
  topCutLengthTasks: { type: Array, default: () => [] },
  loading: Boolean,
})

const root = ref(null)
const durationElement = ref(null)
const topElement = ref(null)
const durationColors = ['#287fcb', '#2ca9df', '#f2a43a', '#24a873', '#8b9aa5']
const statusColors = { confirmed: '#20a879', pending: '#ee9b2f' }
const durationItems = computed(() => props.durationDistribution.filter((item) => Number(item.count) > 0))
const statusItems = computed(() => props.statusDistribution)
const topItems = computed(() => props.topCutLengthTasks.filter((item) => Number(item.cut_length_m) > 0).slice(0, 5))
const durationTotal = computed(() => durationItems.value.reduce((sum, item) => sum + Number(item.count), 0))
const statusTotal = computed(() => statusItems.value.reduce((sum, item) => sum + Number(item.count), 0))
const hasData = computed(() => durationTotal.value > 0 || statusTotal.value > 0 || topItems.value.length > 0)
let durationChart = null
let topChart = null
let resizeObserver = null

function percent(count, total) {
  return total ? `${(Number(count) / total * 100).toFixed(1)}%` : '0.0%'
}

function length(value) {
  return Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 1, maximumFractionDigits: 1 })
}

function disposeCharts() {
  durationChart?.dispose()
  topChart?.dispose()
  durationChart = null
  topChart = null
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
  if (topElement.value && topItems.value.length) {
    if (!topChart || topChart.getDom() !== topElement.value) {
      topChart?.dispose()
      topChart = init(topElement.value)
    }
    topChart.setOption({
      animation: !reducedMotion,
      tooltip: {
        renderMode: 'richText',
        trigger: 'item',
        formatter: ({ dataIndex }) => {
          const item = topItems.value[dataIndex]
          return `${item.program_no || item.task_id}\n任务 ${item.task_id}\n切割长度 ${length(item.cut_length_m)} 米`
        },
      },
      grid: { left: 110, right: 60, top: 14, bottom: 24 },
      xAxis: {
        type: 'value',
        axisLabel: { color: '#718899', fontSize: 9 },
        splitLine: { lineStyle: { color: '#e7eef2' } },
      },
      yAxis: {
        type: 'category',
        inverse: true,
        data: topItems.value.map((item, index) => `${index + 1}. ${item.program_no || item.task_id}`),
        axisTick: { show: false },
        axisLine: { show: false },
        axisLabel: { color: '#536f83', fontSize: 10, width: 96, overflow: 'truncate' },
      },
      series: [{
        name: '切割长度',
        type: 'bar',
        barMaxWidth: 18,
        data: topItems.value.map((item) => Number(item.cut_length_m)),
        itemStyle: { color: '#287fcb', borderRadius: [0, 4, 4, 0] },
        label: { show: true, position: 'right', color: '#274a62', fontSize: 10, formatter: ({ value }) => length(value) },
      }],
    }, true)
  }
}

async function refreshCharts() {
  await nextTick()
  renderCharts()
  durationChart?.resize()
  topChart?.resize()
}

watch(
  () => [props.durationDistribution, props.statusDistribution, props.topCutLengthTasks, props.loading],
  refreshCharts,
  { deep: true },
)

onMounted(() => {
  resizeObserver = new ResizeObserver(() => {
    durationChart?.resize()
    topChart?.resize()
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
        <div v-if="statusTotal" class="status-visual">
          <div class="status-progress" role="img" :aria-label="`任务确认状态：${statusItems.map((item) => `${item.label} ${item.count} 项`).join('，')}`">
            <span v-for="item in statusItems" :key="item.key" :style="{ width: percent(item.count, statusTotal), background: statusColors[item.key] || '#287fcb' }"></span>
          </div>
          <ul aria-label="任务确认状态明细">
            <li v-for="item in statusItems" :key="item.key"><i :style="{ background: statusColors[item.key] || '#287fcb' }"></i><span>{{ item.label }}</span><strong>{{ item.count }} 项</strong><small>{{ percent(item.count, statusTotal) }}</small></li>
          </ul>
        </div>
        <div v-else class="chart-empty">暂无确认状态数据</div>
      </article>
      <article>
        <header><div><AppIcon name="ruler" :size="17" /><h3>切割长度 Top 5</h3></div><span>单位：米</span></header>
        <div v-if="topItems.length" ref="topElement" class="top-chart" role="img" :aria-label="`切割长度前五名：${topItems.map((item) => `${item.program_no || item.task_id} ${length(item.cut_length_m)} 米`).join('，')}`"></div>
        <div v-else class="chart-empty">当前任务暂无有效切割长度</div>
      </article>
    </div>
  </section>
</template>

<style scoped>
.workshop-structure{overflow:hidden;margin:0 0 10px;border:1px solid #dbe6ec;border-radius:10px;background:#fff;box-shadow:0 7px 22px rgba(22,62,89,.05)}
.workshop-structure__head{min-height:47px;display:flex;align-items:center;justify-content:space-between;gap:16px;padding:0 14px;border-bottom:1px solid #e5edf1;background:linear-gradient(90deg,#fff,#fbfdfe)}.workshop-structure__head>div,.workshop-structure article header>div{display:flex;align-items:center;gap:8px}.workshop-structure__head>div>span{width:28px;height:24px;display:grid;place-items:center;color:#237fb7;border-radius:7px;background:#eaf5fb}.workshop-structure__head h2{margin:0;color:#193c57;font-size:14px}.workshop-structure__head p{margin:0;color:#6f8798;font-size:9px}
.workshop-structure__grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;padding:11px 13px 13px}.workshop-structure article{min-width:0;overflow:hidden;border:1px solid #dfe9ee;border-radius:8px;background:#fbfdfe}.workshop-structure article header{min-height:42px;display:flex;align-items:center;justify-content:space-between;gap:12px;padding:0 13px;border-bottom:1px solid #e7eef2;background:#fff}.workshop-structure article h3{margin:0;color:#234b66;font-size:12px}.workshop-structure article header .app-icon{color:#168fad}.workshop-structure article header>span{color:#718899;font-size:9px}
.duration-visual{min-height:190px;display:grid;grid-template-columns:minmax(125px,.8fr) minmax(0,1fr);align-items:center;gap:6px;padding:6px 10px 10px}.duration-donut{position:relative;min-width:0;height:174px}.duration-chart{width:100%;height:100%}.duration-donut>p{position:absolute;inset:50% auto auto 50%;display:flex;align-items:center;flex-direction:column;margin:0;pointer-events:none;transform:translate(-50%,-50%)}.duration-donut strong{color:#173f64;font-size:24px;line-height:1}.duration-donut span{margin-top:5px;color:#718899;font-size:9px}.duration-visual ul{display:grid;gap:9px;min-width:0;margin:0;padding:0;list-style:none}.duration-visual li{display:grid;grid-template-columns:8px minmax(0,1fr) 20px 35px;align-items:center;gap:5px;color:#526d80;font-size:10px}.duration-visual li i{width:8px;height:8px;border-radius:50%}.duration-visual li span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.duration-visual li strong{color:#244a65;text-align:right;font-size:11px}.duration-visual li small{color:#8094a2;text-align:right;font-size:9px}.status-visual{min-height:190px;display:flex;flex-direction:column;justify-content:center;gap:22px;padding:22px 18px}.status-progress{height:26px;display:flex;overflow:hidden;border-radius:7px;background:#e7eef2}.status-progress span{height:100%}.status-visual ul{display:grid;gap:12px;margin:0;padding:0;list-style:none}.status-visual li{display:grid;grid-template-columns:9px minmax(0,1fr) auto 47px;align-items:center;gap:8px;color:#526d80;font-size:11px}.status-visual li i{width:9px;height:9px;border-radius:50%}.status-visual li strong{color:#244a65;font-size:12px}.status-visual li small{text-align:right;color:#718899;font-size:10px}.top-chart{width:100%;height:190px}.chart-empty{min-height:190px;display:grid;place-items:center;padding:20px;color:#718899;font-size:11px;text-align:center}.workshop-structure__state{min-height:150px;display:grid;place-items:center;color:#718899;font-size:11px}
@media(max-width:1100px){.workshop-structure__grid{grid-template-columns:repeat(2,minmax(0,1fr))}.workshop-structure article:last-child{grid-column:1/-1}.top-chart{max-width:650px;margin:0 auto}}
@media(max-width:760px){.workshop-structure__grid{grid-template-columns:1fr}.workshop-structure article:last-child{grid-column:auto}}
@media(max-width:560px){.workshop-structure__head{align-items:flex-start;flex-direction:column;padding:10px 12px}.duration-visual{grid-template-columns:1fr}.duration-donut{height:160px}.duration-visual ul{padding:0 8px 6px}}
</style>
