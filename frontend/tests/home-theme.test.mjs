import { scriptFunction } from './typescript-helpers.mjs'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import * as Vue from 'vue'
import { renderToString } from '@vue/server-renderer'
import { NAlert, NConfigProvider, darkTheme, useThemeVars } from 'naive-ui'
import { createPinia, setActivePinia } from 'pinia'
import { createGenerator } from 'unocss'
import { loadConfigFromFile } from 'vite'
import { fileURLToPath } from 'node:url'
import { parse } from '@vue/compiler-sfc'
import { sparklineGeometry } from '../src/utils/sparkline.ts'

const read = (path) => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8')
const { config: unoConfig } = await loadConfigFromFile({ command: 'build', mode: 'test' }, fileURLToPath(new URL('../uno.config.ts', import.meta.url)))
const flush = async () => { await Promise.resolve(); await Vue.nextTick(); await Promise.resolve(); await Vue.nextTick() }

test('Uno semantic cards include shadow initialization, RGB opacity and shared spacing', async () => {
  const uno = await createGenerator(unoConfig)
  const { css } = await uno.generate('card-wrapper bg-layout bg-container bg-primary/10 gap-panel gap-field p-panel text-auxiliary transition-all-300')
  assert.ok(css.includes('--un-shadow-inset:'))
  assert.ok(css.includes('--un-ring-shadow:'))
  for (const token of ['--layout-bg-color', '--container-bg-color', '--primary-color']) assert.ok(css.includes(`var(${token})`))
  assert.match(css, /gap:var\(--space-4\)/)
  assert.match(css, /padding:var\(--space-4\)/)
  assert.match(css, /font-size:var\(--font-auxiliary\)/)
  assert.match(css, /border-radius:var\(--card-radius\)/)
  assert.match(css, /cubic-bezier/)
})

function mountHome(overrides = {}) {
  let state
  const charts = []
  const theme = Vue.ref({ primaryColor: 'primary-one', successColor: 'success-one', borderColor: 'border', dividerColor: 'divider', textColor1: 'text', textColor3: 'muted', popoverColor: 'popover' })
  const appState = Vue.reactive({ reducedMotion: false })
  const originalWindow = globalThis.window
  const originalResizeObserver = globalThis.ResizeObserver
  let onResize
  globalThis.ResizeObserver = class { constructor(callback) { onResize = callback } observe() {} disconnect() {} }
  globalThis.window = { setInterval: () => 1, clearInterval() {}, addEventListener() {}, removeEventListener() {} }
  const bindings = {
    ...Vue, use: () => {}, BarChart: {}, LineChart: {}, PieChart: {}, GridComponent: {}, LegendComponent: {}, TooltipComponent: {}, CanvasRenderer: {},
    useAppStore: () => appState, useThemeVars: () => theme,
    reportToday: () => '2026-10-23', session: { systemInfo: {}, user: {} },
    formatChineseDate: String, formatChineseDateTime: String, formatChineseMonth: String, formatDeviceDisplay: String,
    getLeadershipReport: async () => ({ current_month_total: { task_count: 0, cut_length_m: 0, work_time_seconds: 0 }, daily_records: [] }),
    getEnergyMonth: async () => ({ rows: [], summary: {} }), listReports: async () => ({ items: [], total: 0 }),
    getAlarmStatus: async () => ({ enabled: false }), listAlarmIncidents: async () => [],
    init: () => { const chart = { options: [], setOption(value) { this.options.push(value) }, resizeCount: 0, resize() { this.resizeCount++ }, dispose() {} }; charts.push(chart); return chart },
    ...overrides,
  }
  const source = parse(read('views/AdminHomeView.vue')).descriptor.scriptSetup.content.replace(/^import .*$/gm, '')
  const entries = Object.entries(bindings).filter(([key]) => /^[\w$]+$/.test(key))
  const setup = scriptFunction(...entries.map(([key]) => key), `${source}\nreturn { overviewLoading, historyLoading, energyLoading, alarmLoading, overview, total, error, recentReports, loadDashboard, workHours, workUnit, trendElement, statusElement, metricSeries, comparisonStatus, taskComparison, lengthComparison, timeComparison, insightTrend }`)
  const renderer = Vue.createRenderer({
    createComment: () => ({}), createText: () => ({}), createElement: () => ({}), insert() {}, remove() {}, setText() {}, setElementText() {}, parentNode: () => null, nextSibling: () => null, patchProp() {},
  })
  const app = renderer.createApp({ setup() { state = setup(...entries.map(([, value]) => value)); return () => null } })
  app.mount({})
  return { state, charts, theme, appState, resize: () => onResize(), close() { app.unmount(); if (originalResizeObserver === undefined) delete globalThis.ResizeObserver; else globalThis.ResizeObserver = originalResizeObserver; if (originalWindow === undefined) delete globalThis.window; else globalThis.window = originalWindow } }
}

test('home results remain independent and theme changes redraw existing charts without another query', async () => {
  let resolveOverview
  let resolveEnergy
  let calls = 0
  const home = mountHome({
    getLeadershipReport: () => { calls++; return new Promise((resolve) => { resolveOverview = resolve }) },
    getEnergyMonth: () => new Promise((resolve) => { resolveEnergy = resolve }),
  })
  try {
    resolveOverview({ current_month_total: { task_count: 232, cut_length_m: 9714.2, work_time_seconds: 78390 }, daily_records: [{ period_start: '2026-10-01', task_count: 232, cut_length_m: 9714.2, work_time_seconds: 78390 }] })
    await flush()
    assert.equal(home.state.overviewLoading.value, false)
    assert.equal(home.state.energyLoading.value, true)
    assert.equal(home.state.total.value.task_count, 232)
    assert.equal(home.state.workHours.value, 21)
    assert.equal(home.state.workUnit.value, '小时 46分')
    home.state.trendElement.value = {}
    home.state.statusElement.value = {}
    await flush()
    assert.equal(home.charts.length, 2)
    home.resize()
    assert.ok(home.charts.every((chart) => chart.resizeCount === 1))
    assert.deepEqual(home.charts[0].options.at(-1).color, ['primary-one', 'success-one'])
    home.theme.value = { ...home.theme.value, primaryColor: 'primary-two', successColor: 'success-two' }
    await flush()
    assert.equal(calls, 1)
    assert.deepEqual(home.charts[0].options.at(-1).color, ['primary-two', 'success-two'])
    home.appState.reducedMotion = true
    await flush()
    assert.equal(home.charts[0].options.at(-1).animationDuration, 0)
    resolveEnergy({ rows: [], summary: {}, energy_connected: false })
    await flush()
    assert.equal(home.state.energyLoading.value, false)
  } finally { home.close() }
})

test('home failure and empty data preserve successful independent modules and finish loading', async () => {
  const home = mountHome({ getLeadershipReport: async () => { throw new Error('生产数据读取失败') }, listReports: async () => ({ items: [{ report_id: 'r1' }], total: 1 }) })
  try {
    await flush()
    assert.equal(home.state.error.value, '生产数据读取失败')
    assert.equal(home.state.overview.value, null)
    assert.equal(home.state.recentReports.value.length, 1)
    assert.equal(home.state.historyLoading.value, false)
    assert.equal(home.state.overviewLoading.value, false)
    assert.equal(home.state.alarmLoading.value, false)
    assert.equal(home.state.energyLoading.value, false)
    assert.equal(home.state.total.value.task_count, 0)
  } finally { home.close() }
})

test('home KPI keeps precision and separators with reduced motion, and missing values stay unknown', () => {
  const props = Vue.reactive({ value: 9714.2, precision: 1 })
  const source = parse(read('components/HomeKpiCard.vue')).descriptor.scriptSetup.content.replace(/^import .*$/gm, '')
  const setup = scriptFunction('computed', 'defineProps', 'useAppStore', `${source}\nreturn { numericValue, formattedValue }`)
  const state = setup(Vue.computed, () => props, () => ({ reducedMotion: true }))
  assert.equal(state.formattedValue.value, '9,714.2')
  props.value = 0
  assert.equal(state.formattedValue.value, '0.0')
  props.value = null
  assert.equal(state.numericValue.value, null)
  assert.equal(state.formattedValue.value, '--')
})

test('home bars and comparisons react independently to returned production data', async () => {
  const home = mountHome({ getLeadershipReport: async () => ({
    current_month_total: { task_count: 25, cut_length_m: 50, work_time_seconds: 39600 },
    comparison_total: { task_count: 20, cut_length_m: 100, work_time_seconds: 36000 },
    daily_records: [
      { period_start: '2026-10-03', task_count: 10, cut_length_m: 5, work_time_seconds: 7200 },
      { period_start: '2026-10-01', task_count: 4, cut_length_m: 20, work_time_seconds: 3600 },
      { period_start: '2026-10-02', task_count: 1, cut_length_m: 10, work_time_seconds: null },
    ],
  }) })
  try {
    await flush()
    const state = home.state
    assert.deepEqual(state.metricSeries.value.tasks, [4, 1, 10])
    assert.deepEqual(state.metricSeries.value.length, [20, 10, 5])
    assert.deepEqual(state.metricSeries.value.hours, [1, null, 2])
    assert.deepEqual(state.taskComparison.value, { text: '↑ 25% 较上月', direction: 'up' })
    assert.deepEqual(state.lengthComparison.value, { text: '↓ 50% 较上月', direction: 'down' })
    assert.deepEqual(state.timeComparison.value, { text: '↑ 10% 较上月', direction: 'up' })
    const before = sparklineGeometry(state.metricSeries.value.tasks).bars.map(bar => bar.height)
    state.overview.value.daily_records[1].task_count = 20
    const after = sparklineGeometry(state.metricSeries.value.tasks).bars.map(bar => bar.height)
    assert.ok(before[0] < before[2])
    assert.ok(after[0] > after[2])
    state.overview.value.current_month_total.cut_length_m = 100.2
    assert.equal(state.lengthComparison.value.text, '↑ 0.2% 较上月')
    assert.equal(state.insightTrend.value, '切割长度高于上月 0.2%')
  } finally { home.close() }
})

test('home comparison distinguishes tiny changes, true equality and unavailable data', async () => {
  const home = mountHome()
  try {
    await flush()
    const compare = home.state.comparisonStatus
    for (const previous of [null, undefined, 0, '', 'invalid', NaN, Infinity]) {
      assert.deepEqual(compare(10, previous), { text: '暂无上月对比', direction: 'flat' })
    }
    assert.equal(compare(null, 10).text, '暂无上月对比')
    assert.equal(compare(100.01, 100).text, '↑ <0.1% 较上月')
    assert.equal(compare(99.99, 100).text, '↓ <0.1% 较上月')
    assert.equal(compare(100, 100).text, '与上月持平')
    assert.equal(compare(0, 100).text, '↓ 100% 较上月')
  } finally { home.close() }
})

test('cards and the welcome banner have distinct light surfaces with dark theme overrides', () => {
  const css = read('assets/soybean.css')
  const template = parse(read('views/AdminHomeView.vue')).descriptor.template.content
  assert.match(template, /class="card-wrapper bg-container home-welcome"/)
  assert.match(css, /\.card-wrapper \{ border:1px solid var\(--theme-border-color\);/)
  assert.match(css, /linear-gradient\(105deg, color-mix\(in srgb, rgb\(var\(--primary-color\)\) 11%, rgb\(var\(--container-bg-color\)\)\)/)
  assert.match(css, /\.home-welcome\.card-wrapper \{[^}]*border-color:rgb\(var\(--primary-color\) \/ \.2\);/s)
  const dark = css.match(/\.dark \.home-welcome\.card-wrapper \{([^}]+)\}/)[1]
  assert.match(dark, /--container-bg-color/)
  assert.match(dark, /--layout-bg-color/)
  assert.doesNotMatch(dark, /#[a-f0-9]+/i)
})

// The old action slot is ignored by NAlert; its removal must preserve rendered UI.
test('removing the unsupported alert action slot preserves the home error output', async () => {
  const render = slots => renderToString(Vue.createSSRApp({ render: () => Vue.h(NAlert, { title: '部分概览数据读取失败', type: 'error', role: 'alert' }, slots) }))
  const before = await render({ default: () => '读取失败', action: () => Vue.h('button', '重试') })
  const after = await render({ default: () => '读取失败' })
  assert.equal(after, before)
})
