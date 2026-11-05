<script setup lang="ts">
import type { DispatchTrend } from '../../api/generated/types.gen'
import type { DispatchTrendFilters } from '../../api/dispatch'
import type { EChartsType } from 'echarts/core'
import { computed, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, watch } from 'vue'
import { NButton, useThemeVars } from 'naive-ui'
import { init, use } from 'echarts/core'
import { BarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components'
import { SVGRenderer } from 'echarts/renderers'
import AppSelect from '../AppSelect.vue'
import ReportPeriodPicker from '../ReportPeriodPicker.vue'
import ReportPanelState from '../ReportPanelState.vue'

const props = defineProps<{
  trend: DispatchTrend | null
  filters: DispatchTrendFilters
  loading: boolean
  error: string
  selectedDateType?: 'sent' | 'completed'
  selectedDay: string
  machineIps: string[]
  machineName: (ip: string) => string
  disabled: boolean
}>()
const emit = defineEmits<{
  apply: [filters: DispatchTrendFilters]
  'select-day': [day: string, dateType: 'sent' | 'completed']
  clear: []
  retry: []
}>()
use([BarChart, GridComponent, TooltipComponent, LegendComponent, SVGRenderer])
const theme = useThemeVars()
const element = ref<HTMLElement | null>(null)
const validationError = ref('')
const isEmpty = computed(() => props.trend?.items.every(item => item.sent_count === 0 && item.completed_count === 0))
let chart: EChartsType | undefined
let observer: ResizeObserver | undefined
let active = false

function applyPeriod(period: { startDate: string; endDate: string }) {
  validationError.value = ''
  emit('apply', { ...props.filters, start_date: period.startDate, end_date: period.endDate })
}
function selectMachine(value: string) {
  emit('apply', { ...props.filters, machine_ip: value || undefined })
}
function selectDayOption(value: string) {
  const [day, dateType] = value.split('|')
  if (day && (dateType === 'sent' || dateType === 'completed')) emit('select-day', day, dateType)
}
function draw() {
  if (!active || !element.value || !props.trend) return
  if (!chart) {
    chart = init(element.value, undefined, { renderer: 'svg' })
    chart.on('click', params => {
      if (props.disabled || props.loading) return
      const day = params.componentType === 'xAxis' ? String(params.value) : params.name
      if (props.trend?.items.some(item => item.date === day)) emit('select-day', day, params.seriesName === '加工完成' ? 'completed' : 'sent')
    })
  }
  const items = props.trend.items
  chart.setOption({
    animation: false, backgroundColor: 'transparent',
    textStyle: { color: theme.value.textColor2, fontFamily: 'inherit' },
    grid: { left: 48, right: 20, top: 48, bottom: 36 },
    legend: { top: 0, data: ['成功派发', '加工完成'], textStyle: { color: theme.value.textColor2 } },
    tooltip: { trigger: 'axis', backgroundColor: theme.value.popoverColor, borderColor: theme.value.borderColor,
      textStyle: { color: theme.value.textColor1 }, axisPointer: { type: 'shadow' } },
    xAxis: { type: 'category', data: items.map(item => item.date), triggerEvent: true,
      axisLabel: { hideOverlap: true, color: theme.value.textColor2, formatter: (value: string) => value.slice(5) },
      axisLine: { lineStyle: { color: theme.value.borderColor } } },
    yAxis: { type: 'value', name: '任务数（条）', minInterval: 1,
      nameTextStyle: { color: theme.value.textColor2 }, axisLabel: { color: theme.value.textColor2 },
      splitLine: { lineStyle: { color: theme.value.dividerColor } } },
    series: [{ type: 'bar', name: '成功派发', barMaxWidth: 32, itemStyle: { color: theme.value.infoColor },
      data: items.map(item => ({ value: item.sent_count, itemStyle: { opacity: props.selectedDay && props.selectedDay !== item.date ? 0.35 : 1 } })) },
      { type: 'bar', name: '加工完成', barMaxWidth: 32, itemStyle: { color: theme.value.successColor },
        data: items.map(item => ({ value: item.completed_count, itemStyle: { opacity: props.selectedDay && props.selectedDay !== item.date ? 0.35 : 1 } })) }],
  }, true)
}
function activate() {
  active = true
  if (element.value && !observer) {
    observer = new ResizeObserver(() => chart?.resize())
    observer.observe(element.value)
  }
  draw()
}
function dispose() {
  active = false
  observer?.disconnect()
  observer = undefined
  chart?.dispose()
  chart = undefined
}
watch(() => [props.trend, props.selectedDay, theme.value], draw, { flush: 'post' })
onMounted(activate)
onActivated(activate)
onDeactivated(dispose)
onBeforeUnmount(dispose)
</script>

<template>
  <div class="dispatch-trend" :aria-busy="loading">
    <div class="dispatch-trend__heading"><h3>每日派发与完成趋势</h3><span v-if="loading && trend" role="status">正在更新…</span></div>
    <div class="dispatch-trend__filters" aria-label="派发统计筛选">
      <ReportPeriodPicker :start-date="filters.start_date" :end-date="filters.end_date" :disabled="disabled" @apply="applyPeriod" @validation-error="validationError = $event" />
      <label><span>机床</span><AppSelect aria-label="派发记录机床" :model-value="filters.machine_ip || ''" :disabled="disabled" @update:model-value="selectMachine"><option value="">全部机床</option><option v-for="ip in machineIps" :key="ip" :value="ip">{{ machineName(ip) }} · {{ ip }}</option></AppSelect></label>
      <NButton :disabled="disabled" @click="emit('clear')">清除筛选 / 全部记录</NButton>
    </div>
    <p class="dispatch-trend__note">派发按成功派发时间统计，完成按魔盒完成时间统计已保留的真实任务数；同一任务重试或重复回调不重复计数，后续撤销保留原成功次数。点击对应柱子或选择日期查看派发／完成明细。{{ trend?.timezone ? `时区：${trend.timezone}。` : '' }}</p>
    <p v-if="validationError" class="dispatch-trend__error" role="alert">{{ validationError }}</p>
    <div v-if="error && trend" class="dispatch-trend__error" role="alert">趋势更新失败：{{ error }}<NButton text :disabled="loading" @click="emit('retry')">重试</NButton></div>
    <ReportPanelState v-if="!trend" :type="error ? 'error' : 'loading'" :title="error ? '趋势读取失败' : '正在读取派发趋势'" :message="error" :action-label="error ? '重试' : ''" @action="emit('retry')" />
    <div v-show="trend" ref="element" class="dispatch-trend__chart" role="img" aria-label="每日派发和加工完成任务数柱状图，可通过下方日期选择查看对应明细"></div>
    <p v-if="trend && isEmpty" class="dispatch-trend__note" role="status">所选区间暂无派发或完成记录。</p>
    <label v-if="trend" class="dispatch-trend__day"><span>查看某日明细</span><AppSelect aria-label="查看某日派发或完成明细" :model-value="selectedDay ? `${selectedDay}|${selectedDateType || 'sent'}` : ''" :disabled="disabled || loading" @update:model-value="selectDayOption"><option value="" disabled>点击图表或选择日期</option><template v-for="item in trend.items" :key="item.date"><option :value="`${item.date}|sent`">{{ item.date }} · 派发 {{ item.sent_count }} 条</option><option :value="`${item.date}|completed`">{{ item.date }} · 完成 {{ item.completed_count }} 条</option></template></AppSelect></label>
  </div>
</template>

<style scoped>
.dispatch-trend { padding:16px; border-bottom:1px solid var(--theme-border-color); }
.dispatch-trend__heading,.dispatch-trend__filters { display:flex; align-items:center; flex-wrap:wrap; gap:12px; }
.dispatch-trend__heading { justify-content:space-between; margin-bottom:12px; }
.dispatch-trend__heading h3 { margin:0; color:var(--text-color-1); font-size:var(--font-section-title); }
.dispatch-trend__heading > span,.dispatch-trend__note { color:var(--text-color-3); font-size:var(--font-auxiliary); }
.dispatch-trend__filters label,.dispatch-trend__day { display:flex; align-items:center; gap:8px; min-width:0; color:var(--text-color-2); font-size:var(--font-auxiliary); }
.dispatch-trend__filters .app-select { width:240px; }
.dispatch-trend__filters :deep(.n-button),.dispatch-trend__filters :deep(.n-base-selection) { min-height:40px; }
.dispatch-trend__day { margin-top:12px; }
.dispatch-trend__day .app-select { width:260px; }
.dispatch-trend__note { margin:12px 0 0; line-height:1.6; }
.dispatch-trend__chart { width:100%; height:260px; }
.dispatch-trend__error { color:rgb(var(--error-color)); font-size:var(--font-auxiliary); }
@media(max-width:560px) {
  .dispatch-trend { padding:12px; }
  .dispatch-trend__filters { align-items:stretch; flex-direction:column; }
  .dispatch-trend__filters label { flex-direction:column; align-items:stretch; }
  .dispatch-trend__filters .app-select { width:100%; }
  .dispatch-trend__day { align-items:stretch; flex-direction:column; }
  .dispatch-trend__day .app-select { width:100%; }
}
</style>
