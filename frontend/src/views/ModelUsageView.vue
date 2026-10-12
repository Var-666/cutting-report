<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { init, use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { getLlmUsageReport } from '../api/reports.js'
import { recentTwelveMonthStart, reportToday } from '../api/reportDates.js'
import { formatChineseDate, formatChineseDateTime, formatChineseMonth } from '../utils/dateDisplay.js'
import { REPORT_CHART_COLORS } from '../utils/reportChartTheme.js'
import AppDatePicker from '../components/AppDatePicker.vue'
import AppSelect from '../components/AppSelect.vue'
import ManagementPageHero from '../components/ManagementPageHero.vue'

use([BarChart, LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

const today = reportToday()
const draft = reactive({ startDate: `${today.slice(0, 7)}-01`, endDate: today, scene: '', granularity: 'day' })
const applied = ref({ ...draft })
const report = ref(null)
const loading = ref(false)
const error = ref('')
const chartElement = ref(null)
const scenes = { leadership: '领导报表', workshop: '车间报表', equipment: '设备报表', alarm: '告警分析', custom: '自定义报表', other: '其他调用' }
const formatter = new Intl.NumberFormat('zh-CN')
const summary = computed(() => report.value?.summary)
const periods = computed(() => [...(report.value?.series || [])].reverse())
const missingCount = computed(() => summary.value ? summary.value.call_count - summary.value.metered_count : 0)
let controller = null
let sequence = 0
let disposed = false
let chart = null
let observer = null

function number(value) {
  return value === null || value === undefined ? '—' : formatter.format(value)
}

function seconds(value) {
  return value === null || value === undefined ? '—' : `${(value / 1000).toFixed(2)} 秒`
}

function sceneName(value) {
  return scenes[value] || '其他调用'
}

function preset(value) {
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
  if (disposed || !chartElement.value || !report.value) return
  if (!chart) {
    chart = init(chartElement.value)
    observer = new ResizeObserver(() => chart?.resize())
    observer.observe(chartElement.value)
  }
  const rows = report.value.series
  chart.setOption({
    animation: false,
    color: [REPORT_CHART_COLORS.primary, REPORT_CHART_COLORS.secondary, REPORT_CHART_COLORS.accent],
    textStyle: { fontFamily: 'Microsoft YaHei, PingFang SC, sans-serif', color: '#526b7c' },
    tooltip: { trigger: 'axis', renderMode: 'richText', confine: true },
    legend: { top: 8, data: ['总 token', '输入 token', '输出 token'] },
    grid: { left: 16, right: 16, top: 48, bottom: 16, containLabel: true },
    xAxis: { type: 'category', data: rows.map((row) => report.value.granularity === 'day' ? row.period.slice(5) : row.period), axisTick: { show: false } },
    yAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: '#e8eff3' } } },
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
  } catch (exception) {
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

function changeGranularity(value) {
  draft.granularity = value
  applied.value = { ...applied.value, granularity: value }
  void load()
}

onMounted(() => { void load() })
onBeforeUnmount(() => {
  disposed = true
  sequence += 1
  controller?.abort()
  observer?.disconnect()
  chart?.dispose()
  chart = null
})
</script>

<template>
  <div class="model-usage-page management-page" :aria-busy="loading">
    <ManagementPageHero eyebrow="MODEL USAGE" title="模型用量" description="查看每日、每月 token 消耗趋势，按业务场景和模型汇总使用情况。" icon="sparkles" status-label="统计口径" status-value="供应商实际返回用量" status-note="缓存命中不新增调用，不估算缺失 token" tone="teal" />

    <section class="panel usage-filter-panel" aria-label="用量查询条件">
      <form class="usage-filters" @submit.prevent="query">
        <label><span>开始日期</span><AppDatePicker v-model="draft.startDate" aria-label="开始日期" required /></label>
        <label><span>结束日期</span><AppDatePicker v-model="draft.endDate" aria-label="结束日期" required /></label>
        <label><span>业务场景</span><AppSelect v-model="draft.scene" aria-label="业务场景"><option value="">全部场景</option><option v-for="(label, value) in scenes" :key="value" :value="value">{{ label }}</option></AppSelect></label>
        <button type="submit" class="primary-button" :disabled="loading">{{ loading ? '正在查询…' : '查询用量' }}</button>
      </form>
      <div class="usage-presets"><span>快捷区间</span><button type="button" @click="preset('month')">本月</button><button type="button" @click="preset('previous')">上月</button><button type="button" @click="preset('year')">今年</button><button type="button" @click="preset('twelve')">近 12 个月</button><small>选择后点击“查询用量”，最长 366 天。</small></div>
    </section>

    <div v-if="error" class="usage-message error" role="alert">{{ error }}<span v-if="report">，以下保留上次成功查询的数据。</span><button type="button" :disabled="loading" @click="load()">重试</button></div>
    <p v-if="loading" class="usage-message" role="status">{{ report ? '正在更新用量，暂保留已显示的数据…' : '正在读取模型用量…' }}</p>

    <template v-if="report">
      <div class="usage-range"><strong>{{ formatChineseDate(report.start_date) }} 至 {{ formatChineseDate(report.end_date) }}</strong><span>{{ report.scene ? sceneName(report.scene) : '全部场景' }} · {{ report.timezone }} · 按请求开始时间归属日期</span></div>
      <section class="usage-kpis" aria-label="区间用量概览">
        <article class="usage-total"><span>已记录总 token</span><strong>{{ number(summary.total_tokens) }}</strong><small>{{ number(summary.metered_count) }} / {{ number(summary.call_count) }} 次请求返回总用量</small></article>
        <article><span>输入 token</span><strong>{{ number(summary.input_tokens) }}</strong><small>{{ number(summary.input_metered_count) }} 次返回输入分项</small></article>
        <article><span>输出 token</span><strong>{{ number(summary.output_tokens) }}</strong><small>{{ number(summary.output_metered_count) }} 次返回输出分项</small></article>
        <article><span>实际请求次数</span><strong>{{ number(summary.call_count) }}</strong><small>{{ number(summary.failed_count) }} 次请求失败 · 平均 {{ seconds(summary.average_duration_ms) }}</small></article>
      </section>
      <div v-if="missingCount" class="usage-message warning" role="status">{{ number(missingCount) }} 次请求未返回总 token，用量合计可能不完整；“—”表示未返回，不代表零。</div>

      <section class="panel" aria-label="用量趋势与周期汇总">
        <header class="panel-title"><div><h2>消耗趋势与周期汇总</h2></div><div class="usage-tabs" aria-label="统计粒度"><button type="button" :aria-pressed="report.granularity === 'day'" :class="{ active: report.granularity === 'day' }" :disabled="loading" @click="changeGranularity('day')">每日</button><button type="button" :aria-pressed="report.granularity === 'month'" :class="{ active: report.granularity === 'month' }" :disabled="loading" @click="changeGranularity('month')">每月</button></div></header>
        <div ref="chartElement" class="usage-chart" role="img" aria-label="总 token、输入 token 和输出 token 趋势，完整数值见下方周期汇总表"></div>
        <p v-if="!summary.call_count" class="usage-empty" role="status">当前区间暂无用量记录。可调整日期，或在智能分析实际请求后刷新。</p>
        <div class="history-wrap usage-periods">
          <table v-content-table class="history-table"><caption class="sr-only">每日或每月 token 汇总</caption><thead><tr><th>统计周期</th><th>请求次数</th><th>输入 token</th><th>输出 token</th><th>总 token</th><th>用量已返回 / 请求</th><th>请求失败</th></tr></thead><tbody><tr v-for="row in periods" :key="row.period"><td>{{ report.granularity === 'day' ? formatChineseDate(row.period) : formatChineseMonth(row.period) }}<small v-if="row.before_tracking">统计启用前</small></td><td>{{ number(row.call_count) }}</td><td>{{ number(row.input_tokens) }}</td><td>{{ number(row.output_tokens) }}</td><td><strong>{{ number(row.total_tokens) }}</strong></td><td>{{ row.before_tracking ? '未记录' : `${number(row.metered_count)} / ${number(row.call_count)}` }}</td><td>{{ number(row.failed_count) }}</td></tr></tbody></table>
        </div>
      </section>

      <section class="panel" aria-label="业务场景与模型消耗">
        <header class="panel-title"><div><h2>场景与模型分布</h2></div><p>按当前查询区间统计</p></header>
        <div class="history-wrap"><table v-content-table class="history-table"><thead><tr><th>业务场景</th><th>模型</th><th>请求次数</th><th>输入 token</th><th>输出 token</th><th>总 token</th><th>未返回总用量</th></tr></thead><tbody><tr v-for="row in report.breakdown" :key="`${row.scene}:${row.model}`"><td>{{ sceneName(row.scene) }}</td><td :title="row.model">{{ row.model || '未记录' }}</td><td>{{ number(row.call_count) }}</td><td>{{ number(row.input_tokens) }}</td><td>{{ number(row.output_tokens) }}</td><td><strong>{{ number(row.total_tokens) }}</strong></td><td>{{ number(row.call_count - row.metered_count) }}</td></tr><tr v-if="!report.breakdown.length"><td colspan="7" class="usage-empty">暂无场景用量</td></tr></tbody></table></div>
      </section>

      <section class="panel usage-notes" aria-label="统计说明与历史累计">
        <h2>统计说明</h2>
        <p>按日期统计自 {{ formatChineseDateTime(report.tracking_started_at, '—') }} 启用。月汇总只包含所选起止日期内的记录；启用当日及当月也仅覆盖启用后的调用。</p>
        <p>只统计实际发起的模型 HTTP 请求，失败请求也计次数。响应已返回表示接口完成返回，不代表分析内容通过校验；连接失败、超时或响应不可读取归为请求失败。未请求模型的缓存命中不计入。</p>
        <p>Token 来自供应商 usage；总量缺失且输入、输出均返回时，以两项之和记录。输入与输出分项可能缺失，不能据此重算已返回总量。缓存输入和推理输出是分项信息，不重复加到总量；不估算费用。</p>
        <div class="usage-lifetime"><span>全部历史累计（不随筛选变化）</span><strong>{{ number(report.lifetime.call_count) }} 次请求</strong><strong>{{ report.lifetime.metered_count ? number(report.lifetime.total_tokens) : '—' }} token</strong></div>
        <p v-if="report.legacy.call_count">其中 {{ number(report.legacy.call_count) }} 次、{{ report.legacy.metered_count ? number(report.legacy.total_tokens) : '未返回' }} token 为旧版累计，缺少日期和分项，无法回填每日、每月统计。</p>
      </section>
    </template>
  </div>
</template>

<style scoped>
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap;border:0}
.model-usage-page{min-width:0;color:#193c57}.usage-filters{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr) minmax(0,1fr) auto;gap:12px;align-items:end;padding:16px}.usage-filters label{min-width:0}.usage-filters label>span{display:block;margin-bottom:6px;color:#526b7c;font-size:12px}.usage-filters button{min-height:38px;padding:0 20px}.usage-presets{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:0 16px 16px;font-size:12px}.usage-presets>span,.usage-presets small{color:#526b7c}.usage-presets button{padding:5px 12px;background:#f4f8fb;border:1px solid #d4e2eb;color:#1769aa}.usage-presets small{margin-left:auto;font-size:12px}.usage-range{display:flex;flex-wrap:wrap;gap:8px 20px;align-items:center;font-size:13px}.usage-range span{color:#526b7c;font-size:12px}.usage-kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.usage-kpis article{min-width:0;padding:18px;border:1px solid #dbe6ec;border-radius:10px;background:#fff}.usage-kpis span,.usage-kpis small{display:block;color:#526b7c;font-size:12px;line-height:1.6}.usage-kpis strong{display:block;margin:8px 0;color:#193c57;font-size:28px;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}.usage-kpis .usage-total{border-top:3px solid #1769aa;padding-top:16px}.usage-message{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:0;padding:12px 16px;border:1px solid #d5e5ec;border-radius:8px;background:#eef7fb;color:#30596d;font-size:12px;line-height:1.7}.usage-message.error{border-color:#edcaca;background:#fff6f6;color:#963d3d}.usage-message.warning{border-color:#ead9b8;background:#fffaf0;color:#795b25}.usage-message button{padding:4px 12px;background:#fff;color:inherit;border:1px solid currentColor}.usage-chart{height:280px;margin:12px}.usage-tabs{gap:4px!important}.usage-tabs button{padding:6px 16px;border:1px solid #ccdce5;background:#fff;color:#526b7c;font-size:12px}.usage-tabs button.active{border-color:#1769aa;background:#1769aa;color:#fff}.usage-periods{max-height:330px;overflow:auto}.usage-periods th{position:sticky;top:0;z-index:1}.usage-periods td small{display:block;color:#526b7c;font-size:10px}.usage-empty{padding:20px!important;text-align:center;color:#526b7c;font-size:12px}.usage-notes{padding:16px}.usage-notes h2{margin:0 0 10px;font-size:14px}.usage-notes p{margin:8px 0;color:#526b7c;font-size:12px;line-height:1.8}.usage-lifetime{display:flex;flex-wrap:wrap;gap:10px 24px;margin-top:12px;padding-top:12px;border-top:1px solid #e4edf1;font-size:12px}.usage-lifetime span{color:#526b7c}.model-usage-page button:focus-visible{outline:2px solid #1769aa;outline-offset:3px}.model-usage-page button:disabled{cursor:not-allowed;opacity:.55}
@media(max-width:1000px){.usage-kpis{grid-template-columns:repeat(2,minmax(0,1fr))}.usage-filters{grid-template-columns:repeat(2,minmax(0,1fr))}.usage-filters button{width:100%}.usage-presets small{width:100%;margin-left:0}}
@media(max-width:560px){.usage-filters{grid-template-columns:1fr;padding:12px}.usage-presets{padding:0 12px 12px}.usage-kpis{gap:8px}.usage-kpis article{padding:12px}.usage-kpis strong{font-size:23px}.usage-kpis .usage-total{padding-top:10px}.usage-chart{height:240px;margin:8px}.usage-range{align-items:flex-start;flex-direction:column}.usage-tabs button{padding:6px 10px}}
</style>
