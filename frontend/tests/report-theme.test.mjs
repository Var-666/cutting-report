import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import { parse } from '@vue/compiler-sfc'
import postcss from 'postcss'
import { renderToString } from '@vue/server-renderer'
import { NConfigProvider, darkTheme, useThemeVars } from 'naive-ui'
import { reportThemeVariables } from '../src/theme/reportVariables.js'
import { createReportChartGrid } from '../src/utils/reportChartTheme.js'
import { mergeOwnedQuery, queryChoice, queryDateRange, sameRouteQuery } from '../src/utils/reportRouteState.js'

const read = path => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8')
const flush = async () => { for (let i = 0; i < 6; i++) { await Promise.resolve(); await Vue.nextTick() } }
const hookSource = read('composables/useReportChartTheme.js').replace(/^import .*$/gm, '').replace('export function', 'function')
function chartThemeHook(theme, vars) {
  return new Function('computed', 'watch', 'useThemeVars', 'useThemeStore', `${hookSource}\nreturn useReportChartTheme`)(Vue.computed, Vue.watch, () => vars, () => theme)
}

test('report CSS aliases use actual Naive light/dark surfaces, text, borders and primary contrast', async () => {
  const modes = []
  for (const dark of [false, true]) {
    let vars
    await renderToString(Vue.createSSRApp({ render: () => Vue.h(NConfigProvider, { theme: dark ? darkTheme : null }, { default: () => Vue.h({ setup() { vars = useThemeVars().value; return () => null } }) }) }))
    const aliases = reportThemeVariables(vars)
    assert.equal(aliases['--text-color-1'], vars.textColor1)
    assert.equal(aliases['--theme-table-header-color'], vars.tableHeaderColor)
    assert.equal(aliases['--theme-on-primary-color'], vars.baseColor)
    assert.ok(Object.values(aliases).every(value => typeof value === 'string' && value.length))
    modes.push(aliases)
  }
  assert.notEqual(modes[0]['--text-color-1'], modes[1]['--text-color-1'])
  assert.notEqual(modes[0]['--theme-input-color'], modes[1]['--theme-input-color'])
  assert.notEqual(modes[0]['--theme-table-header-color'], modes[1]['--theme-table-header-color'])
})

test('standard report surfaces, controls and feedback contain no fixed light colors', () => {
  for (const path of ['views/LeadershipReportView.vue','views/WorkshopView.vue','views/EnergyPeriodReportView.vue','components/AnalysisPanel.vue','components/ReportTrendPanel.vue','components/ReportPanelState.vue','components/ReportFeedback.vue','components/ReportPeriodPicker.vue','components/WorkshopStructureCharts.vue','components/StandardReportQuery.vue','components/ReportExportMenu.vue']) {
    const styles = parse(read(path)).descriptor.styles.map(style => style.content).join('\n')
    assert.doesNotMatch(styles, /#[a-f0-9]{3,8}\b|rgba?\(\s*\d/i, path)
  }
  const tables = read('assets/content-tables.css')
  assert.match(tables, /background:linear-gradient\(var\(--report-table-head-bg\),var\(--report-table-head-bg\)\),rgb\(var\(--container-bg-color\)\)/)
  assert.match(tables, /color:var\(--report-table-head-ink\)/)
  assert.match(read('App.vue'), /reportThemeVariables\(vars.value\)/)
})

test('other business pages and shared states use theme colors, preserving only the export preview palette', () => {
  for (const path of ['views/AlarmCenterView.vue','views/CuttingDispatchView.vue','views/DatabaseView.vue','views/AutomationView.vue','views/AdminAccountsView.vue','views/SettingsHomeView.vue','views/ModelUsageView.vue','components/ManagementPageHero.vue','components/CatalogTreeNode.vue','components/EmailDeliveryResolution.vue','components/DownloadPdfButton.vue','components/AppIcon.vue','assets/style.css']) {
    const source = read(path)
    const css = path.endsWith('.css') ? source : parse(source).descriptor.styles.map(style => style.content).join('\n')
    postcss.parse(css).walkRules(rule => {
      if (/template-preview|fake-chart|fake-table/.test(rule.selector)) return
      rule.walkDecls(decl => assert.doesNotMatch(decl.value, /#[a-f0-9]{3,8}\b|rgba?\(\s*[\d.]/i, `${path}: ${rule.selector} ${decl.prop}`))
    })
  }
  assert.doesNotMatch(read('components/AppIcon.vue'), /#[a-f0-9]{3,8}\b/i)
  assert.match(read('assets/style.css'), /button\.primary, \.button\.primary[^}]*background: rgb\(var\(--primary-color\)\)/)
  assert.match(read('assets/style.css'), /\.template-preview-page \{ --muted:#[a-f0-9]+; --ink:#[a-f0-9]+/)
})

function mountCharts(name) {
  const theme = Vue.reactive({ darkMode: false })
  const vars = Vue.ref({ primaryColor: 'primary', successColor: 'success', warningColor: 'warning', infoColor: 'info', textColor1: 'text', textColor3: 'muted', borderColor: 'border', dividerColor: 'divider', popoverColor: 'popover', cardColor: 'container' })
  const charts = []
  let calls = 0
  const makeChart = (dom, scheme) => { const chart = { scheme, dom, disposed: false, options: [], getDom() { return dom }, setOption(option) { this.options.push(option) }, resize() {}, dispose() { this.disposed = true } }; charts.push(chart); return chart }
  const daily = { period_start: '2026-10-23', task_count: 4, cut_length_m: 12, work_time_seconds: 3600 }
  const report = { snapshot: { data_as_of: '2026-10-23T12:00:00', refreshing: false }, daily_records: [daily], monthly_records: [daily], current_month_total: { task_count: 4 }, rows: [], records: [] }
  const query = async () => { calls++; return report }
  const route = Vue.reactive({ name: name.startsWith('Leadership') ? 'leadership-report' : 'equipment-report', query: {} })
  const oldWindow = globalThis.window
  const oldObserver = globalThis.ResizeObserver
  globalThis.window = { addEventListener() {}, removeEventListener() {}, setTimeout: () => 1, clearTimeout() {}, matchMedia: () => ({ matches: false }) }
  globalThis.ResizeObserver = class { observe() {} disconnect() {} }
  const bindings = {
    ...Vue, useReportChartTheme: chartThemeHook(theme, vars), createReportChartGrid,
    BarChart: {}, LineChart: {}, PieChart: {}, GridComponent: {}, LegendComponent: {}, TooltipComponent: {}, CanvasRenderer: {}, use() {}, init: makeChart,
    defineProps: () => ({ durationDistribution: [{ key: 'short', label: '短任务', count: 4 }], statusDistribution: [{ key: 'confirmed', count: 4 }], topCutLengthTasks: [{ task_id: '1', cut_length_m: 12 }] }), defineEmits: () => () => {},
    useRoute: () => route, useRouter: () => ({ push: async () => {} }), useDeviceSwitchQuery() {},
    reportToday: () => '2026-10-23', recentTwelveMonthStart: () => '2025-11-01', taskProductionDate: () => '2026-10-23',
    getLeadershipReport: query, getLeadershipRange: query, getLatestWorkshopProductionDate: async () => ({ report_date: '2026-10-23' }),
    getTaskEnergyDay: query, getEnergyMonthlyRange: query, getEnergyDay: query, getEnergyRange: query,
    formatChineseDate: String, formatChineseMonth: String, formatChineseDateTime: String, formatDeviceDisplay: String, deviceSelection: { id: 'Cutter01' }, sortReportPeriods: rows => rows,
    mergeOwnedQuery, queryChoice, queryDateRange, sameRouteQuery,
  }
  const folder = name === 'WorkshopStructureCharts' ? 'components' : 'views'
  const script = parse(read(`${folder}/${name}.vue`)).descriptor.scriptSetup.content.replace(/^import[\s\S]*?from ['"][^'"]+['"];?\s*$/gm, '')
  const names = name === 'LeadershipReportView' ? 'dailyPanel, monthlyPanel' : name === 'EnergyPeriodReportView' ? 'taskPanel, periodPanel' : 'durationElement, topElement'
  const entries = Object.entries(bindings).filter(([key]) => /^[\w$]+$/.test(key))
  const setup = new Function(...entries.map(([key]) => key), `${script}\nreturn { ${names}, pageActive, renderCharts }`)
  let state
  const renderer = Vue.createRenderer({ createComment: () => ({}), createText: () => ({}), createElement: () => ({}), insert() {}, remove() {}, setText() {}, setElementText() {}, parentNode: () => null, nextSibling: () => null, patchProp() {} })
  const app = renderer.createApp({ setup() { state = setup(...entries.map(([, value]) => value)); return () => null } })
  app.mount({})
  return { state, charts, theme, vars, calls: () => calls, async attach() {
    await flush()
    for (const name of names.split(', ')) state[name].value = name.endsWith('Element') ? {} : { getChartElement: () => ({}) }
    state.renderCharts()
    await flush()
  }, close() { app.unmount(); if (oldWindow === undefined) delete globalThis.window; else globalThis.window = oldWindow; if (oldObserver === undefined) delete globalThis.ResizeObserver; else globalThis.ResizeObserver = oldObserver } }
}

for (const name of ['LeadershipReportView', 'EnergyPeriodReportView', 'WorkshopStructureCharts']) test(`${name} rebuilds themed charts from existing data and defers hidden cached charts`, async () => {
  const page = mountCharts(name)
  try {
    await page.attach()
    const before = page.calls()
    const oldCharts = [...page.charts]
    assert.equal(oldCharts.length, 2)
    page.theme.darkMode = true
    await flush()
    assert.ok(oldCharts.every(chart => chart.disposed))
    const current = page.charts.slice(-2)
    assert.ok(current.every(chart => chart.scheme === 'dark'))
    assert.ok(current.every(chart => chart.options.at(-1).backgroundColor === 'transparent'))
    assert.equal(page.calls(), before, 'theme changes must not start another data query')
    page.vars.value = { ...page.vars.value, primaryColor: 'new-primary' }
    await flush()
    assert.ok(page.charts.slice(-2).some(chart => JSON.stringify(chart.options.at(-1)).includes('new-primary')))
    assert.equal(page.calls(), before)
    page.state.pageActive.value = false
    const count = page.charts.length
    page.theme.darkMode = false
    await flush()
    assert.equal(page.charts.length, count, 'inactive KeepAlive pages must not initialize hidden charts')
    page.state.pageActive.value = true
    page.state.renderCharts()
    assert.ok(page.charts.slice(-2).every(chart => chart.scheme === undefined))
  } finally { page.close() }
})
