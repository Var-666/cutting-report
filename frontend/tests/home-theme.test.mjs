import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import * as Vue from 'vue'
import { renderToString } from '@vue/server-renderer'
import { NConfigProvider, darkTheme, useThemeVars } from 'naive-ui'
import { createPinia, setActivePinia } from 'pinia'
import { createGenerator } from 'unocss'
import { loadConfigFromFile } from 'vite'
import { fileURLToPath } from 'node:url'
import { parse } from '@vue/compiler-sfc'

const read = (path) => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8')
const { config: unoConfig } = await loadConfigFromFile({ command: 'build', mode: 'test' }, fileURLToPath(new URL('../uno.config.js', import.meta.url)))
const flush = async () => { await Promise.resolve(); await Vue.nextTick(); await Promise.resolve(); await Vue.nextTick() }

test('Uno semantic cards include shadow initialization, RGB opacity and shared spacing', async () => {
  const uno = await createGenerator(unoConfig)
  const { css } = await uno.generate('card-wrapper bg-layout bg-container bg-primary/10 gap-16px transition-all-300')
  assert.ok(css.includes('--un-shadow-inset:'))
  assert.ok(css.includes('--un-ring-shadow:'))
  for (const token of ['--layout-bg-color', '--container-bg-color', '--primary-color']) assert.ok(css.includes(`var(${token})`))
  assert.match(css, /gap:16px/)
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
  const setup = new Function(...entries.map(([key]) => key), `${source}\nreturn { overviewLoading, historyLoading, energyLoading, alarmLoading, overview, total, error, recentReports, loadDashboard, workHours, workUnit, trendElement, statusElement }`)
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
