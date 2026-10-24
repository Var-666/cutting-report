import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import * as Vue from 'vue'
import { parse } from '@vue/compiler-sfc'

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8')
const source = read('../src/views/ModelUsageView.vue')
const script = parse(source).descriptor.scriptSetup.content.replace(/^import .*$/gm, '')

function mount(getReport) {
  let state
  const theme = Vue.reactive({ darkMode: false })
  const vars = Vue.ref({ primaryColor: 'primary', successColor: 'success', warningColor: 'warning', textColor1: 'text', textColor2: 'text2', dividerColor: 'divider', borderColor: 'border', popoverColor: 'popover' })
  const hookSource = read('../src/composables/useReportChartTheme.js').replace(/^import .*$/gm, '').replace('export function', 'function')
  const chartThemeHook = new Function('computed', 'watch', 'useThemeVars', 'useThemeStore', `${hookSource}\nreturn useReportChartTheme`)(Vue.computed, Vue.watch, () => vars, () => theme)
  const charts = []
  const observers = []
  const oldObserver = globalThis.ResizeObserver
  globalThis.ResizeObserver = class { constructor() { observers.push(this) } observe() {} disconnect() { this.disconnected = true } }
  const bindings = {
    ...Vue, use: () => {}, BarChart: {}, LineChart: {}, GridComponent: {}, LegendComponent: {},
    TooltipComponent: {}, CanvasRenderer: {}, getLlmUsageReport: getReport,
    reportToday: () => '2026-10-12', recentTwelveMonthStart: () => '2025-11-01',
    useReportChartTheme: chartThemeHook,
    init: (dom, scheme) => { const chart = { scheme, options: [], setOption(option) { this.options.push(option) }, resize() {}, dispose() { this.disposed = true } }; charts.push(chart); return chart },
  }
  const setup = new Function(...Object.keys(bindings), `${script}\nreturn { draft, applied, report, loading, error, load, query, changeGranularity, preset, number, chartElement, drawChart, series, periods }`)
  const renderer = Vue.createRenderer({
    createComment: () => ({}), createText: () => ({}), createElement: () => ({}),
    insert: () => {}, remove: () => {}, setText: () => {}, setElementText: () => {},
    parentNode: () => null, nextSibling: () => null, patchProp: () => {},
  })
  const app = renderer.createApp({ setup() { state = setup(...Object.values(bindings)); return () => null } })
  app.mount({})
  const unmount = app.unmount.bind(app)
  app.unmount = () => { unmount(); if (oldObserver === undefined) delete globalThis.ResizeObserver; else globalThis.ResizeObserver = oldObserver }
  return { app, state, theme, vars, charts, observers }
}

const result = (marker = 1) => ({ summary: { call_count: 21, metered_count: 20 }, series: [{ period: '2026-10-12', call_count: 21, total_tokens: 0, input_tokens: 0, output_tokens: 0 }], marker })
const flush = async () => { await Promise.resolve(); await Vue.nextTick(); await Promise.resolve() }

test('model usage remains a settings destination and uses the shared authenticated API', () => {
  assert.match(read('../src/router/index.js'), /path: 'model-usage'[\s\S]*ModelUsageView\.vue/)
  assert.match(read('../src/api/reports.js'), /getLlmUsageReport[\s\S]*request\(`\/api\/v1\/llm-usage\/report\?\$\{query\}`, \{ signal \}\)/)
  assert.match(source, /消耗趋势与周期汇总/)
  assert.match(source, /场景与模型分布/)
  assert.doesNotMatch(source, /逐次调用明细|report\.calls|app-pagination/)
  assert.doesNotMatch(source, /usage-notes|usage-lifetime|统计说明|统计启用前|formatChineseDateTime/)
})

test('draft filters do not change refreshed or aggregated usage until applied', async () => {
  const queries = []
  const { app, state } = mount(async (query) => { queries.push(query); return result() })
  try {
    await flush()
    state.draft.scene = 'alarm'
    state.draft.startDate = '2026-09-01'
    await state.load()
    assert.equal(queries.at(-1).scene, '')
    assert.equal(queries.at(-1).startDate, '2026-10-01')
    state.query()
    await flush()
    assert.equal(queries.at(-1).scene, 'alarm')
    assert.equal(queries.at(-1).startDate, '2026-09-01')
    state.changeGranularity('month')
    await flush()
    assert.equal(queries.at(-1).granularity, 'month')
    assert.equal(queries.at(-1).scene, 'alarm')
  } finally { app.unmount() }
})

test('invalid ranges never query and missing tokens remain different from zero', async () => {
  let calls = 0
  const { app, state } = mount(async () => { calls++; return result() })
  try {
    await flush()
    state.draft.startDate = '2026-11-01'
    state.query()
    assert.equal(calls, 1)
    assert.match(state.error.value, /有效/)
    state.draft.startDate = '2020-01-01'
    state.query()
    assert.equal(calls, 1)
    assert.match(state.error.value, /366/)
    assert.equal(state.number(null), '—')
    assert.equal(state.number(0), '0')
  } finally { app.unmount() }
})

for (const granularity of ['day', 'month']) test(`${granularity} table and chart omit empty periods but keep failed, missing-token and zero-token requests`, async () => {
  const dates = granularity === 'day' ? ['2026-10-01', '2026-10-03', '2026-10-05', '2026-10-07'] : ['2026-07', '2026-08', '2026-09', '2026-10']
  const rows = [
    { period: dates[0], before_tracking: true, call_count: null, total_tokens: null },
    { period: dates[1], call_count: 1, metered_count: 0, failed_count: 1, total_tokens: null },
    { period: dates[2], call_count: 0, total_tokens: 0 },
    { period: dates[3], call_count: 1, metered_count: 1, total_tokens: 0 },
  ]
  const data = { ...result(), granularity, series: rows, summary: { call_count: 2, metered_count: 1, failed_count: 1, total_tokens: 0 } }
  const page = mount(async () => data)
  try {
    await flush()
    assert.deepEqual(page.state.series.value.map(row => row.period), [dates[1], dates[3]])
    assert.deepEqual(page.state.periods.value.map(row => row.period), [dates[3], dates[1]])
    page.state.chartElement.value = {}
    page.state.drawChart()
    const option = page.charts.at(-1).options.at(-1)
    assert.deepEqual(option.xAxis.data, [dates[1], dates[3]].map(period => granularity === 'day' ? period.slice(5) : period))
    assert.deepEqual(option.series[0].data, [null, 0])
    assert.equal(page.state.report.value.summary.call_count, 2)
    assert.equal(data.series.length, 4, 'display filtering must not mutate the API response')
    page.state.report.value = { ...data, series: [rows[0], rows[2]], summary: { call_count: 0 } }
    page.state.drawChart()
    assert.equal(page.state.periods.value.length, 0)
    assert.equal(page.charts.at(-1).disposed, true)
    assert.equal(page.observers.at(-1).disconnected, true)
    assert.match(source, /v-show="series.length" ref="chartElement"/)
    assert.match(source, /v-if="series.length" class="history-wrap usage-periods"/)
  } finally { page.app.unmount() }
})

test('superseded and unmounted requests cannot replace the selected usage report', async () => {
  const pending = []
  const { app, state } = mount((query, { signal }) => new Promise((resolve) => pending.push({ query, signal, resolve })))
  const newer = state.load()
  assert.equal(pending[0].signal.aborted, true)
  pending[1].resolve(result(2))
  await newer
  pending[0].resolve(result(1))
  await flush()
  assert.equal(state.report.value.marker, 2)
  const last = state.load()
  app.unmount()
  assert.equal(pending[2].signal.aborted, true)
  pending[2].resolve(result(1))
  await last
  assert.equal(state.report.value.marker, 2)
})

test('query failures preserve the last successful report and can be retried', async () => {
  let fail = false
  const { app, state } = mount(async () => {
    if (fail) throw new Error('用量读取失败')
    return result()
  })
  try {
    await flush()
    fail = true
    await state.load()
    assert.equal(state.error.value, '用量读取失败')
    assert.equal(state.report.value.marker, 1)
    assert.equal(state.loading.value, false)
    fail = false
    await state.load()
    assert.equal(state.error.value, '')
  } finally { app.unmount() }
})

test('usage chart follows dark mode and semantic colors without refetching or leaking observers', async () => {
  let calls = 0
  const page = mount(async () => { calls++; return result() })
  try {
    await flush()
    page.state.chartElement.value = {}
    page.state.drawChart()
    assert.equal(page.charts.length, 1)
    page.theme.darkMode = true
    await flush()
    assert.equal(page.charts[0].disposed, true)
    assert.equal(page.observers[0].disconnected, true)
    assert.equal(page.charts.at(-1).scheme, 'dark')
    assert.equal(page.charts.at(-1).options.at(-1).backgroundColor, 'transparent')
    page.vars.value = { ...page.vars.value, primaryColor: 'new-primary' }
    await flush()
    assert.equal(page.charts.at(-1).options.at(-1).color[0], 'new-primary')
    page.theme.darkMode = false
    await flush()
    assert.equal(page.charts.at(-1).scheme, undefined)
    assert.equal(calls, 1)
  } finally { page.app.unmount() }
  assert.ok(page.charts.every(chart => chart.disposed))
  assert.ok(page.observers.every(observer => observer.disconnected))
})
