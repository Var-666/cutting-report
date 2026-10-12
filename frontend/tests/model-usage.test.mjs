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
  const bindings = {
    ...Vue, use: () => {}, BarChart: {}, LineChart: {}, GridComponent: {}, LegendComponent: {},
    TooltipComponent: {}, CanvasRenderer: {}, getLlmUsageReport: getReport,
    reportToday: () => '2026-10-12', recentTwelveMonthStart: () => '2025-11-01',
    formatChineseDateTime: (value) => value, REPORT_CHART_COLORS: {},
    init: () => { throw new Error('detached chart must not initialize') },
  }
  const setup = new Function(...Object.keys(bindings), `${script}\nreturn { draft, applied, report, loading, error, load, query, changeGranularity, preset, number }`)
  const renderer = Vue.createRenderer({
    createComment: () => ({}), createText: () => ({}), createElement: () => ({}),
    insert: () => {}, remove: () => {}, setText: () => {}, setElementText: () => {},
    parentNode: () => null, nextSibling: () => null, patchProp: () => {},
  })
  const app = renderer.createApp({ setup() { state = setup(...Object.values(bindings)); return () => null } })
  app.mount({})
  return { app, state }
}

const result = (marker = 1) => ({ summary: { call_count: 21, metered_count: 20 }, series: [], marker })
const flush = async () => { await Promise.resolve(); await Vue.nextTick(); await Promise.resolve() }

test('model usage is the fifth settings destination and uses the shared authenticated API', () => {
  const links = [...read('../src/layouts/SettingsLayout.vue').matchAll(/<RouterLink to="([^"]+)"/g)]
  assert.equal(links.length, 5)
  assert.equal(links[4][1], '/admin/settings/model-usage')
  assert.match(read('../src/router/index.js'), /path: 'model-usage'[\s\S]*ModelUsageView\.vue/)
  assert.match(read('../src/views/SettingsHomeView.vue'), /status-value="5 个管理模块"/)
  assert.match(read('../src/api/reports.js'), /getLlmUsageReport[\s\S]*request\(`\/api\/v1\/llm-usage\/report\?\$\{query\}`, \{ signal \}\)/)
  assert.match(source, /消耗趋势与周期汇总/)
  assert.match(source, /场景与模型分布/)
  assert.doesNotMatch(source, /逐次调用明细|report\.calls|app-pagination/)
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
    assert.match(source, /统计启用前/)
    assert.match(source, /不重复加到总量/)
  } finally { app.unmount() }
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
