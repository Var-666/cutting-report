<script setup lang="ts">
import type { EChartsType as ECharts } from 'echarts/core'
import type { EChartsOption } from 'echarts'
import type { UsageReport } from '../types/usage'

import { toError } from '../utils/errors'
import { NAlert, NButton, NCard, NStatistic } from 'naive-ui'
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { init, use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { getLlmUsageReport } from '../api/system.ts'
import { recentTwelveMonthStart, reportToday } from '../api/reportDates.ts'
import { formatChineseDate, formatChineseMonth } from '../utils/dateDisplay.ts'
import { useReportChartTheme } from '../composables/useReportChartTheme.ts'
import AppDatePicker from '../components/AppDatePicker.vue'
import AppSelect from '../components/AppSelect.vue'
import ManagementPageHero from '../components/ManagementPageHero.vue'

use([BarChart, LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

const today = reportToday()
const draft = reactive({ startDate: `${today.slice(0, 7)}-01`, endDate: today, scene: '', granularity: 'day' })
const applied = ref({ ...draft })
const report = ref<UsageReport | null>(null)
const loading = ref(false)
const error = ref('')
const chartElement = ref<HTMLElement | null>(null)
const scenes: Record<string, string> = { leadership: '领导报表', workshop: '车间报表', equipment: '设备报表', alarm: '告警分析', custom: '自定义报表', welding: '焊接分析', other: '其他调用' }
const formatter = new Intl.NumberFormat('zh-CN')
const summary = computed(() => report.value?.summary)
const series = computed(() => (report.value?.series || []).filter(row => row.call_count !== null && row.call_count > 0))
const periods = computed(() => [...series.value].reverse())
const missingCount = computed(() => summary.value ? summary.value.call_count - summary.value.metered_count : 0)
let controller: AbortController | null = null
let sequence = 0
let disposed = false
let chart: ECharts | null = null
let observer: ResizeObserver | null = null
const chartTheme = useReportChartTheme(() => { disposeChart(); drawChart() })

function disposeChart() {
  observer?.disconnect()
  observer = null
  chart?.dispose()
  chart = null
}

function number(value: number | null | undefined) {
  return value === null || value === undefined ? '—' : formatter.format(value)
}

function seconds(value: number | null | undefined) {
  return value === null || value === undefined ? '—' : `${(value / 1000).toFixed(2)} 秒`
}

function sceneName(value: string) {
  return scenes[value] || '其他调用'
}

function preset(value: string) {
  draft.endDate = today
  if (value === 'month') draft.startDate = `${today.slice(0, 7)}-01`
  if (value === 'year') draft.startDate = `${today.slice(0, 4)}-01-01`
  if (value === 'twelve') { draft.startDate = recentTwelveMonthStart(today); draft.granularity = 'month' }
  if (value === 'previous') {
    const first = new Date(`${today.slice(0, 7)}-01T00:00:00Z`)
    const last = new Date(first.getTime() - 86400000).toISOString().slice(0, 10)
    draft.startDate = `${last.slice(0, 7)}-01`
    draft.endDate = last
  }
}

function drawChart() {
  if (!series.value.length) { disposeChart(); return }
  if (disposed || !chartElement.value || !report.value) return
  if (!chart) {
    chart = init(chartElement.value, chartTheme.darkMode.value ? 'dark' : undefined)
    observer = new ResizeObserver(() => chart?.resize())
    observer.observe(chartElement.value)
  }
  const rows = series.value
  chart.setOption<EChartsOption>({
    animation: false,
    backgroundColor: 'transparent',
    color: [chartTheme.colors.value.primary, chartTheme.colors.value.secondary, chartTheme.colors.value.accent],
    textStyle: { fontFamily: 'Microsoft YaHei, PingFang SC, sans-serif', color: chartTheme.vars.value.textColor2 },
    tooltip: { trigger: 'axis', renderMode: 'richText', confine: true, backgroundColor: chartTheme.vars.value.popoverColor, borderColor: chartTheme.vars.value.borderColor, textStyle: { color: chartTheme.vars.value.textColor1 } },
    legend: { top: 8, data: ['总 token', '输入 token', '输出 token'] },
    grid: { left: 16, right: 16, top: 48, bottom: 16, containLabel: true },
    xAxis: { type: 'category', data: rows.map((row) => report.value?.granularity === 'day' ? row.period.slice(5) : row.period), axisTick: { show: false } },
    yAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: chartTheme.vars.value.dividerColor } } },
    series: [
      { name: '总 token', type: 'line', data: rows.map((row) => row.total_tokens), connectNulls: false, symbolSize: 5 },
      { name: '输入 token', type: 'bar', data: rows.map((row) => row.input_tokens), barMaxWidth: 16 },
      { name: '输出 token', type: 'bar', data: rows.map((row) => row.output_tokens), barMaxWidth: 16 },
    ],
  }, true)
}

async function load() {
  const current = ++sequence
  controller?.abort()
  controller = new AbortController()
  loading.value = true
  error.value = ''
  try {
    const result = await getLlmUsageReport({ ...applied.value }, { signal: controller.signal })
    if (disposed || current !== sequence) return
    report.value = result
    await nextTick()
    drawChart()
  } catch (caught) {
    const exception = toError(caught)
    if (!disposed && current === sequence && exception.name !== 'AbortError') error.value = exception.message || '模型用量读取失败'
  } finally {
    if (!disposed && current === sequence) loading.value = false
  }
}

function query() {
  if (!draft.startDate || !draft.endDate || draft.endDate < draft.startDate) {
    error.value = '请选择有效的开始和结束日期'
    return
  }
  if ((Date.parse(draft.endDate) - Date.parse(draft.startDate)) / 86400000 >= 366) {
    error.value = '统计区间最长 366 天'
    return
  }
  applied.value = { ...draft }
  void load()
}

function changeGranularity(value: string) {
  draft.granularity = value
  applied.value = { ...applied.value, granularity: value }
  void load()
}

onMounted(() => { void load() })
onBeforeUnmount(() => {
  disposed = true
  sequence += 1
  controller?.abort()
  disposeChart()
})
</script>

<template>
  <div class="model-usage-page management-page" :aria-busy="loading">
    <ManagementPageHero eyebrow="MODEL USAGE" title="模型用量" description="查看每日、每月 token 消耗趋势，按业务场景和模型汇总使用情况。" icon="sparkles" status-label="统计口径" status-value="供应商实际返回用量" status-note="缓存命中不新增调用，不估算缺失 token" tone="teal" />

    <NCard class="usage-filter-panel" :bordered="false" aria-label="用量查询条件">
      <form class="usage-filters" @submit.prevent="query">
        <label><span>开始日期</span><AppDatePicker v-model="draft.startDate" aria-label="开始日期" required /></label>
        <label><span>结束日期</span><AppDatePicker v-model="draft.endDate" aria-label="结束日期" required /></label>
        <label><span>业务场景</span><AppSelect v-model="draft.scene" aria-label="业务场景"><option value="">全部场景</option><option v-for="(label, value) in scenes" :key="value" :value="value">{{ label }}</option></AppSelect></label>
        <NButton attr-type="submit" type="primary" :loading="loading" :disabled="loading">{{ loading ? '正在查询…' : '查询用量' }}</NButton>
      </form>
      <div class="usage-presets"><span>快捷区间</span><NButton @click="preset('month')">本月</NButton><NButton @click="preset('previous')">上月</NButton><NButton @click="preset('year')">今年</NButton><NButton @click="preset('twelve')">近 12 个月</NButton><small>选择后点击“查询用量”，最长 366 天。</small></div>
    </NCard>

    <NAlert v-if="error" type="error" class="usage-message error" role="alert">{{ error }}<span v-if="report">，以下保留上次成功查询的数据。</span><NButton :disabled="loading" @click="load()">重试</NButton></NAlert>
    <p v-if="loading" class="usage-message" role="status">{{ report ? '正在更新用量，暂保留已显示的数据…' : '正在读取模型用量…' }}</p>

    <template v-if="report && summary">
      <div class="usage-range"><strong>{{ formatChineseDate(report.start_date) }} 至 {{ formatChineseDate(report.end_date) }}</strong><span>{{ report.scene ? sceneName(report.scene) : '全部场景' }} · {{ report.timezone }} · 按请求开始时间归属日期</span></div>
      <section class="usage-kpis" aria-label="区间用量概览">
        <NCard class="usage-total" :bordered="false"><NStatistic label="已记录总 token" :value="number(summary.total_tokens)" /><small>{{ number(summary.metered_count) }} / {{ number(summary.call_count) }} 次请求返回总用量</small></NCard>
        <NCard :bordered="false"><NStatistic label="输入 token" :value="number(summary.input_tokens)" /><small>{{ number(summary.input_metered_count) }} 次返回输入分项</small></NCard>
        <NCard :bordered="false"><NStatistic label="输出 token" :value="number(summary.output_tokens)" /><small>{{ number(summary.output_metered_count) }} 次返回输出分项</small></NCard>
        <NCard :bordered="false"><NStatistic label="实际请求次数" :value="number(summary.call_count)" /><small>{{ number(summary.failed_count) }} 次请求失败 · 平均 {{ seconds(summary.average_duration_ms) }}</small></NCard>
      </section>
      <NAlert v-if="missingCount" type="warning" class="usage-message warning" role="status">{{ number(missingCount) }} 次请求未返回总 token，用量合计可能不完整；“—”表示未返回，不代表零。</NAlert>

      <NCard :bordered="false" aria-label="用量趋势与周期汇总">
        <header class="usage-panel-title"><div><h2>消耗趋势与周期汇总</h2></div><div class="usage-tabs" aria-label="统计粒度"><NButton :aria-pressed="report.granularity === 'day'" :class="{ active: report.granularity === 'day' }" :disabled="loading" @click="changeGranularity('day')">每日</NButton><NButton :aria-pressed="report.granularity === 'month'" :class="{ active: report.granularity === 'month' }" :disabled="loading" @click="changeGranularity('month')">每月</NButton></div></header>
        <div v-show="series.length" ref="chartElement" class="usage-chart" role="img" aria-label="总 token、输入 token 和输出 token 趋势，完整数值见下方周期汇总表"></div>
        <p v-if="!series.length" class="usage-empty" role="status">当前区间暂无用量记录。</p>
        <div v-if="series.length" class="history-wrap usage-periods">
          <table v-content-table class="history-table" data-content-columns="left right right right right right right"><caption class="sr-only">每日或每月 token 汇总</caption><thead><tr><th>统计周期</th><th>请求次数</th><th>输入 token</th><th>输出 token</th><th>总 token</th><th>用量已返回 / 请求</th><th>请求失败</th></tr></thead><tbody><tr v-for="row in periods" :key="row.period"><td>{{ report.granularity === 'day' ? formatChineseDate(row.period) : formatChineseMonth(row.period) }}</td><td>{{ number(row.call_count) }}</td><td>{{ number(row.input_tokens) }}</td><td>{{ number(row.output_tokens) }}</td><td><strong>{{ number(row.total_tokens) }}</strong></td><td>{{ number(row.metered_count) }} / {{ number(row.call_count) }}</td><td>{{ number(row.failed_count) }}</td></tr></tbody></table>
        </div>
      </NCard>

      <NCard :bordered="false" aria-label="业务场景与模型消耗">
        <header class="usage-panel-title"><div><h2>场景与模型分布</h2></div><p>按当前查询区间统计</p></header>
        <div class="history-wrap"><table v-content-table class="history-table" data-content-columns="left left right right right right right"><thead><tr><th>业务场景</th><th>模型</th><th>请求次数</th><th>输入 token</th><th>输出 token</th><th>总 token</th><th>未返回总用量</th></tr></thead><tbody><tr v-for="row in report.breakdown" :key="`${row.scene}:${row.model}`"><td>{{ sceneName(row.scene) }}</td><td :title="row.model">{{ row.model || '未记录' }}</td><td>{{ number(row.call_count) }}</td><td>{{ number(row.input_tokens) }}</td><td>{{ number(row.output_tokens) }}</td><td><strong>{{ number(row.total_tokens) }}</strong></td><td>{{ number(row.call_count - row.metered_count) }}</td></tr><tr v-if="!report.breakdown.length"><td colspan="7" class="usage-empty">暂无场景用量</td></tr></tbody></table></div>
      </NCard>

    </template>
  </div>
</template>

<style scoped>
.model-usage-page{display:grid;gap:16px;min-width:0}.usage-filters{display:grid;grid-template-columns:repeat(3,minmax(0,1fr)) auto;align-items:end;gap:16px}.usage-filters label{display:block;min-width:0}.usage-filters label>span{display:block;margin-bottom:6px;color:var(--text-color-3);font-size:13px}.usage-presets{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-top:16px;font-size:12px;color:var(--text-color-3)}.usage-presets small{margin-left:auto;font-size:var(--font-auxiliary)}.usage-range{display:flex;align-items:center;flex-wrap:wrap;gap:8px 20px;font-size:13px}.usage-range span,.usage-kpis small{color:var(--text-color-3);font-size:12px;line-height:1.7}.usage-kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px}.usage-kpis>.n-card{min-width:0}.usage-kpis small{display:block;margin-top:8px}.usage-kpis :deep(.n-statistic-value){overflow-wrap:anywhere;font-variant-numeric:tabular-nums}.usage-message{margin:0}.usage-message .n-button{margin-left:12px}.usage-tabs{display:flex;gap:6px}.usage-tabs .active{color:rgb(var(--primary-color))}.usage-chart{height:280px;margin-top:12px}.usage-periods{max-height:330px;overflow:auto}.usage-periods th{position:sticky;top:0;z-index:1}.usage-empty{padding:20px!important;text-align:center;color:var(--text-color-3)}.usage-panel-title{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px}.usage-panel-title h2{margin:0;font-size:16px;font-weight:600}
@media(max-width:1100px){.usage-kpis,.usage-filters{grid-template-columns:repeat(2,minmax(0,1fr))}.usage-presets small{width:100%;margin-left:0}}
@media(max-width:560px){.usage-filters{grid-template-columns:1fr}.usage-kpis{gap:12px}.usage-chart{height:240px}.usage-range{align-items:flex-start;flex-direction:column}.usage-kpis :deep(.n-card__content){padding:12px}.usage-kpis :deep(.n-statistic-value__content){font-size:22px}}
</style>
