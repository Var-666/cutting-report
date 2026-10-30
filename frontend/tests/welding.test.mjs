import { setup as setupCssr } from '@css-render/vue3-ssr'
import { renderToString } from '@vue/server-renderer'
import { SharedDataTable, createSharedDataTable } from './data-table-helpers.mjs'
import { scriptFunction } from './typescript-helpers.mjs'
import test, { afterEach } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { computed, h, reactive, ref } from 'vue'
import * as Vue from 'vue'
import { createMemoryHistory, createRouter, RouterView, useRoute, useRouter } from 'vue-router'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'
import { lineHome, productionGroups, selectedLineType } from '../src/utils/productionLines.ts'
import { adminMenus } from '../src/router/menus.ts'
import { analyzeWelding, downloadWelding, getWeldingPart, getWeldingReport, weldingQuery } from '../src/api/welding.ts'
import { weldingColumns, weldingVisibleColumns, weldingColumnLayout, weldingDisplay, weldingKpis, weldingTitles } from '../src/utils/weldingReport.ts'
import { weldingRoutes } from '../src/router/weldingRoutes.ts'
import { reportCache } from '../src/api/reportCache.ts'
import { queryDateRange, sameRouteQuery } from '../src/utils/reportRouteState.ts'
import { parseWeldingRouteQuery, weldingRouteQuery } from '../src/utils/weldingRouteState.ts'

const source = path => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8')
afterEach(() => reportCache.clear())

test('device types contain independent welding lines without adding a third navigation level', () => {
  const items = [{ id: 'Cutter01', name: '20KW' }, { id: 'Cutter02', name: '30KW' }, { id: 'welding', type: 'welding', name: '小组立焊接线' }, { id: 'other-welding', type: 'welding', name: '另一焊接线' }]
  const groups = productionGroups(items)
  assert.deepEqual(groups.map(group => [group.name, group.items.length]), [['切割', 2], ['焊接', 2]])
  assert.equal(selectedLineType(items, 'other-welding'), 'welding')
  assert.equal(selectedLineType(items, 'Cutter01'), 'cutting')
})

test('cross-line switching enters the correct home and restores selection if navigation is cancelled', async () => {
  const script = source('composables/useDeviceSelection.ts').replace(/^import .*$/gm, '').replace('export function', 'function')
  const selection = { id: 'Cutter01', items: [{ id: 'Cutter01' }, { id: 'Cutter02' }, { id: 'welding', type: 'welding' }, { id: 'other-welding', type: 'welding' }] }
  const destinations = []
  let cancel = false
  const router = { replace: async to => { destinations.push(to); return cancel ? { type: 4 } : undefined } }
  const bindings = { lineHome, selectedLineType, ref, onMounted() {}, useRoute: () => ({ name: 'admin-home', query: {} }), useRouter: () => router,
    deviceSwitchQuery: () => null, changeDevice: id => { selection.id = id }, deviceSelection: selection, loadDevices() {} }
  const app = scriptFunction(...Object.keys(bindings), `${script}; return useDeviceSelection();`)(...Object.values(bindings))
  await app.switchDevice('welding')
  assert.equal(selection.id, 'welding')
  assert.equal(destinations.at(-1), '/admin/welding/home')
  await app.switchDevice('Cutter02')
  assert.equal(selection.id, 'Cutter02')
  assert.equal(destinations.at(-1), '/admin/home')
  await app.switchDevice('other-welding')
  assert.equal(selection.id, 'other-welding')
  assert.equal(destinations.at(-1), '/admin/welding/home')
  await app.switchDevice('welding')
  assert.equal(selection.id, 'welding')
  await app.switchDevice('Cutter02')
  cancel = true
  await app.switchDevice('other-welding')
  assert.equal(selection.id, 'Cutter02')
  assert.equal(app.switching.value, false)
})

test('business menus isolate cutting capabilities and flatten the welding layout', () => {
  const routes = [{ path: 'alarms', name: 'alarms', meta: { line: 'cutting', title: '告警' } }, weldingRoutes,
    { path: 'settings', meta: { title: '设置' }, children: [{ path: 'database', name: 'database', meta: { title: '数据库' } }, { path: 'automation', name: 'automation', meta: { title: '自动化', line: 'cutting' } }] }]
  const menus = adminMenus(routes, '/admin', 'welding')
  assert.ok(menus.some(menu => menu.key === 'welding-home'))
  assert.ok(menus.some(menu => menu.key === '/admin/welding/reports' && menu.children.length === 4))
  const text = JSON.stringify(menus)
  assert.ok(!text.includes('alarms') && !text.includes('automation'))
  assert.ok(JSON.stringify(adminMenus(routes)).includes('alarms'))
  assert.ok(!JSON.stringify(adminMenus(routes)).includes('welding-home'))
  assert.ok(!menus.some(menu => menu.label === '智能分析'))
  const legacyAnalysis = weldingRoutes.children.find(route => route.path === 'analysis')
  assert.deepEqual(legacyAnalysis.redirect({ query: { start: '2026-09-01', end: '2026-09-02' } }), {
    path: '/admin/welding/reports/daily', query: { start: '2026-09-01', end: '2026-09-02' },
  })
  assert.match(source('views/WeldingReportView.vue'), /<ReportAnalysis[\s\S]*生成当前区间分析[\s\S]*@reload="runAnalysis"/)
})

test('query parameters retain zero device, robot and process codes; unknown is not zero', () => {
  assert.deepEqual(weldingQuery({ device_no: 0, robot_no: 0, weld_type: 0, state: '', project_no: null, anomalies_only: false }), { device_no: 0, robot_no: 0, weld_type: 0, anomalies_only: false })
  assert.equal(weldingDisplay(null, 'completed_part_count'), '待核实')
  assert.equal(weldingDisplay(0, 'weld_length_m'), '0.000')
  assert.equal(weldingDisplay(null, 'weld_length_m'), '未记录')
  const cards = weldingKpis('daily', { summary: { weld_length_m: 0, involved_part_count: 0 }, meta: { pending_metrics: { completed_part_count: 'pending' } } })
  assert.equal(cards[1].value, 0)
  assert.equal(cards[2].value, 0)
  assert.equal(cards[3].value, null)
  const parts = weldingKpis('parts', { summary: { part_row_count: 0, part_actual_length_m: null }, meta: { ambiguous_part_count: 2, pending_metrics: { part_actual_length_m: '歧义关联' } } })
  assert.equal(parts[1].value, 0)
  assert.equal(parts[2].value, '待核实')
  assert.equal(parts[3].value, 2)
})

test('a successful JSON response cannot be downloaded as a PDF', async t => {
  t.mock.method(globalThis, 'fetch', async () => new Response('{}', { headers: { 'Content-Type': 'application/json' } }))
  await assert.rejects(downloadWelding('/api/v1/welding/export', 'test.pdf'), /文件格式不正确/)
})

function view(readReport, options = {}) {
  const script = parse(source('views/WeldingReportView.vue')).descriptor.scriptSetup.content.replace(/^import .*$/gm, '')
  const cleanup = []
  const bindings = {
    selectedLineType, computed, reactive, ref, watch: (source, callback, config) => { if (options.watchers) { options.watchers.push({ source, callback }); if (config?.immediate) callback(source()) } }, onBeforeUnmount: fn => cleanup.push(fn),
    defineProps: () => ({ kind: options.kind || 'jobs' }), useRoute: () => options.route || ({ query: {} }), useRouter: () => options.router || ({ push() {}, replace() {} }), queryDateRange, sameRouteQuery, parseWeldingRouteQuery, weldingRouteQuery,
    reportToday: () => '2026-10-26', getWeldingReport: readReport,
    deviceSelection: { id: 'welding', items: [{ id: 'welding', type: 'welding', enabled: options.enabled ?? true }] }, weldingTitles, weldingKpis, weldingColumns, weldingColumnLayout, weldingDisplay, weldingQuery,
  }
  return { ...scriptFunction(...Object.keys(bindings), `${script}; return { read, submit, changePage, changePageSize, report, loading, error, activeQuery, draft, detailJob, goToPart, actionError };`)(...Object.values(bindings)), cleanup }
}

const period = { start: '2026-10-26', end: '2026-10-26' }
const busyResponse = (retryAfter = '1') => Response.json({ detail: '焊接数据查询请求繁忙，请稍后重试。' }, { status: 503, headers: { 'Retry-After': retryAfter } })
const settle = () => new Promise(resolve => setImmediate(resolve))

function snapshotWaitTimers(t) {
  const timers = new Map()
  let clock = 0, sequence = 0
  t.mock.method(Date, 'now', () => clock)
  t.mock.method(globalThis, 'setTimeout', (callback, delay) => { timers.set(++sequence, { callback, delay }); return sequence })
  t.mock.method(globalThis, 'clearTimeout', id => timers.delete(id))
  return { timers, advance(milliseconds) { clock += milliseconds }, fire() { const [id, timer] = timers.entries().next().value; timers.delete(id); clock += timer.delay; timer.callback() } }
}
const preparingResponse = () => Response.json({ detail: '统计正在生成，请稍后重试' }, { status: 503, headers: { 'Retry-After': '1', 'X-Welding-Error': 'snapshot_refresh_busy' } })

for (const kind of ['dashboard', 'daily', 'parts', 'jobs', 'equipment']) test(`${kind} keeps its snapshot visible and replaces it when background refresh finishes without applying draft edits`, async t => {
  const clock = snapshotWaitTimers(t)
  const calls = []
  let complete
  const app = view((kind, values, signal, force) => {
    calls.push({ kind, values, signal, force })
    return calls.length === 1
      ? Promise.resolve({ query: values, data_version: 'previous', meta: { report_data: { refreshing: true } } })
      : new Promise(resolve => { complete = resolve })
  }, { kind })
  t.after(() => app.cleanup.forEach(cleanup => cleanup()))
  await app.read({ ...period, device_no: 0 })
  assert.equal(app.loading.value, false)
  assert.equal(app.report.value.data_version, 'previous')
  assert.equal(clock.timers.size, 1)
  app.draft.device_no = 7
  clock.fire(); await settle()
  assert.equal(app.loading.value, false)
  assert.equal(app.report.value.data_version, 'previous')
  assert.equal(calls[1].values.device_no, 0)
  assert.equal(calls[1].force, false)
  complete({ query: calls[1].values, data_version: 'latest', meta: { report_data: { refreshing: false } } })
  await settle()
  assert.equal(app.report.value.data_version, 'latest')
  assert.equal(app.draft.device_no, 7)
  assert.equal(clock.timers.size, 0)
})

test('a new homepage filter or unmount cancels background checks and ignores late results', async t => {
  const clock = snapshotWaitTimers(t)
  const calls = []
  const app = view((kind, values, signal) => new Promise(resolve => calls.push({ values, signal, resolve })), { kind: 'dashboard' })
  const first = app.read({ ...period, device_no: 0 })
  calls[0].resolve({ query: calls[0].values, data_version: 'old', meta: { report_data: { refreshing: true } } })
  await first
  clock.fire(); await settle()
  const selected = app.read({ ...period, device_no: 1 })
  assert.equal(calls[1].signal.aborted, true)
  calls[2].resolve({ query: calls[2].values, data_version: 'selected', meta: { report_data: { refreshing: true } } })
  await selected
  calls[1].resolve({ query: calls[1].values, data_version: 'late' }); await settle()
  assert.equal(app.report.value.data_version, 'selected')
  assert.equal(clock.timers.size, 1)
  app.cleanup.forEach(cleanup => cleanup())
  assert.equal(clock.timers.size, 0)
})

test('background snapshot checks stop at the same bounded count as cutting reports', async t => {
  const clock = snapshotWaitTimers(t)
  let calls = 0
  const app = view(async (kind, values) => {
    calls += 1
    return { query: values, data_version: 'previous', meta: { report_data: { refreshing: true } } }
  })
  t.after(() => app.cleanup.forEach(cleanup => cleanup()))
  await app.read(period)
  for (let index = 0; index < 75; index += 1) { clock.fire(); await settle() }
  assert.equal(calls, 76)
  assert.equal(clock.timers.size, 0)
  assert.equal(app.loading.value, false)
  assert.equal(app.report.value.data_version, 'previous')
})

test('snapshot preparation keeps the page loading and reads the completed snapshot without forcing another refresh', async t => {
  const clock = snapshotWaitTimers(t)
  const calls = []
  t.mock.method(globalThis, 'fetch', async url => {
    calls.push(new URL(url, 'http://test'))
    return calls.length === 1 ? preparingResponse() : Response.json({ success: true, data: { query: period, data_version: 'prepared' } })
  })
  const app = view(getWeldingReport)
  const pending = app.read(period, true)
  await settle()
  assert.equal(app.loading.value, true)
  assert.equal(app.error.value, '')
  assert.equal(clock.timers.size, 1)
  assert.equal(calls[0].searchParams.get('refresh'), 'true')
  clock.fire(); await pending
  assert.equal(calls.length, 2)
  assert.equal(calls[1].searchParams.has('refresh'), false)
  assert.equal(app.report.value.data_version, 'prepared')
  assert.equal(app.loading.value, false)
  assert.equal(app.error.value, '')
})

test('snapshot preparation stops at its bounded deadline instead of retrying forever', async t => {
  const clock = snapshotWaitTimers(t)
  const fetchMock = t.mock.method(globalThis, 'fetch', async () => preparingResponse())
  const result = getWeldingReport('dashboard', period).then(() => null, error => error)
  await settle()
  clock.advance(180000)
  clock.fire()
  const error = await result
  assert.equal(error.code, 'snapshot_refresh_busy')
  assert.equal(error.status, 503)
  assert.equal(fetchMock.mock.callCount(), 1)
  assert.equal(clock.timers.size, 0)
})

test('leaving the page cancels a snapshot wait and clears its timer', async t => {
  const { timers } = snapshotWaitTimers(t)
  const fetchMock = t.mock.method(globalThis, 'fetch', async () => preparingResponse())
  const app = view(getWeldingReport)
  const pending = app.read(period)
  await settle()
  assert.equal(timers.size, 1)
  app.cleanup.forEach(cleanup => cleanup())
  await pending
  assert.equal(timers.size, 0)
  assert.equal(fetchMock.mock.callCount(), 1)
  assert.equal(app.error.value, '')
})

test('a new filter cancels the previous snapshot wait and cannot receive its result', async t => {
  const { timers } = snapshotWaitTimers(t)
  let calls = 0
  t.mock.method(globalThis, 'fetch', async url => {
    calls++
    return new URL(url, 'http://test').searchParams.get('device_no') === '0' ? preparingResponse() : Response.json({ success: true, data: { query: { ...period, device_no: 1 }, data_version: 'new-filter' } })
  })
  const app = view(getWeldingReport)
  const old = app.read({ ...period, device_no: 0 })
  await settle()
  await app.read({ ...period, device_no: 1 }); await old
  assert.equal(timers.size, 0)
  assert.equal(calls, 2)
  assert.equal(app.activeQuery.value.device_no, 1)
  assert.equal(app.report.value.data_version, 'new-filter')
})

test('welding route parsing validates all boundaries, preserves numeric zero and drops incompatible filters', () => {
  const all = { start: '2026-09-01', end: '2026-09-02', device_no: '0', robot_no: '0', weld_type: '0', state: '0', part_state: '0', serial_number: 'S', project_no: 'P', section_no: 'SEC', part_no: 'PART', anomalies_only: 'true', sort_by: 'length', descending: 'false', page: '3', page_size: '50', unrelated: 'keep' }
  const jobs = parseWeldingRouteQuery(all, 'jobs', '2026-10-26')
  assert.deepEqual([jobs.device_no, jobs.robot_no, jobs.weld_type, jobs.state], [0, 0, 0, 0])
  assert.equal(jobs.anomalies_only, true)
  assert.equal(jobs.descending, false)
  const parts = parseWeldingRouteQuery(all, 'parts', '2026-10-26')
  assert.deepEqual([parts.project_no, parts.section_no, parts.part_no, parts.part_state], ['P', 'SEC', 'PART', 0])
  assert.equal(parts.robot_no, '')
  for (const kind of ['daily', 'equipment', 'dashboard']) {
    const parsed = parseWeldingRouteQuery(all, kind, '2026-10-26')
    assert.equal(parsed.serial_number, '')
    assert.equal(parsed.project_no, '')
    assert.equal(parsed.anomalies_only, false)
    assert.equal(parsed.robot_no, kind === 'equipment' ? 0 : '')
  }
  for (const value of ['', ' ', '0x10', '1e2', '1.2', '-1', '9007199254740992', ['0', '1'], null]) {
    const parsed = parseWeldingRouteQuery({ ...all, device_no: value, robot_no: value }, 'jobs', '2026-10-26')
    assert.equal(parsed.device_no, '', `invalid device ${value}`)
    assert.equal(parsed.robot_no, '')
  }
  const invalid = parseWeldingRouteQuery({ start: '2025-01-01', end: '2026-10-26', serial_number: '\u0000bad', page: '100001', page_size: '101', state: 'no', sort_by: 'bad', anomalies_only: '1', descending: '0' }, 'jobs', '2026-10-26')
  assert.deepEqual([invalid.start, invalid.end, invalid.page, invalid.page_size, invalid.state, invalid.serial_number, invalid.sort_by, invalid.anomalies_only, invalid.descending], ['2026-10-26', '2026-10-26', 1, 10, '', '', 'time', false, true])
  const query = weldingRouteQuery(all, jobs)
  assert.equal(query.unrelated, 'keep')
  assert.equal(query.device_no, '0')
  assert.equal(query.anomalies_only, 'true')
  assert.equal(query.descending, 'false')
  assert.equal('project_no' in query, false)
  assert.deepEqual(parseWeldingRouteQuery(query, 'jobs', '2026-10-26'), jobs)
})

const flushWelding = async () => { await settle(); await Vue.nextTick() }
async function mountWelding(t, kind = 'jobs', query = {}) {
  const calls = [], downloads = [], analyses = []
  let held = false, state
  const selection = Vue.reactive({ id: 'welding', items: [{ id: 'welding', type: 'welding' }, { id: 'line-b', type: 'welding' }, { id: 'Cutter01', type: 'cutting' }] })
  let router
  const bindings = {
    ...Vue, useRoute, useRouter, defineProps: () => ({ get kind() { return router.currentRoute.value.meta.kind } }),
    selectedLineType, deviceSelection: selection, queryDateRange, sameRouteQuery, parseWeldingRouteQuery, weldingRouteQuery,
    reportToday: () => '2026-10-26', weldingTitles, weldingKpis, weldingColumns, weldingColumnLayout, weldingDisplay, weldingQuery,
    getWeldingReport: (kind, values, signal) => {
      // Match the backend's parts query normalization instead of echoing conflicting state fields.
      const normalized = kind === 'parts' ? { ...values, state: null, part_state: values.part_state !== '' && values.part_state != null ? values.part_state : values.state === '' ? null : values.state } : { ...values }
      const response = { query: normalized, data_version: `version-${calls.length + 1}`, report_type: kind === 'dashboard' ? 'jobs' : kind, summary: {}, rows: [], meta: {} }
      const call = { kind, query: { ...values }, line: selection.id, signal }
      calls.push(call)
      if (!held) return Promise.resolve(response)
      return new Promise((resolve, reject) => { call.resolve = () => resolve(response); call.reject = reject })
    },
    downloadWelding: async (url, filename) => downloads.push({ url: new URL(url, 'http://test'), filename }),
    analyzeWelding: async (kind, query, version) => { analyses.push({ kind, query: { ...query }, version }); return { overview: 'ok' } },
    getWeldingPart: async (id, page) => ({ part: { id }, seams: { page, items: [] } }),
  }
  const code = parse(source('views/WeldingReportView.vue')).descriptor.scriptSetup.content.replace(/^import .*$/gm, '')
  const setup = scriptFunction(...Object.keys(bindings), `${code}; return { draft, activeQuery, report, submit, changePage, changePageSize, loading, error, exportReport, runAnalysis, analysis, openDetail, details, detailJob, goToPart }`)
  const page = { setup() { state = setup(...Object.values(bindings)); return () => null } }
  router = createRouter({ history: createMemoryHistory(), routes: ['dashboard', 'daily', 'parts', 'jobs', 'equipment'].map(kind => ({ path: kind === 'dashboard' ? '/admin/welding/home' : `/admin/welding/reports/${kind}`, name: `welding-${kind}`, meta: { kind }, component: page })) })
  await router.push({ name: `welding-${kind}`, query })
  const renderer = Vue.createRenderer({ createComment: () => ({}), createText: () => ({}), createElement: () => ({}), insert() {}, remove() {}, setText() {}, setElementText() {}, parentNode: () => null, nextSibling: () => null, patchProp() {} })
  const app = renderer.createApp({ render: () => Vue.h(RouterView) })
  app.use(router); app.mount({})
  let mounted = true
  const unmount = () => { if (mounted) { mounted = false; app.unmount() } }
  t.after(unmount)
  await flushWelding()
  return { state, calls, router, selection, downloads, analyses, unmount, hold() { held = true } }
}

for (const kind of ['dashboard', 'daily', 'parts', 'jobs', 'equipment']) {
  test(`welding ${kind}: copied links restore supported filters and pagination with exactly one request`, async t => {
    const query = { start: '2026-09-01', end: '2026-09-02', device_no: '0', robot_no: '0', weld_type: '2', state: '3', part_state: '4', serial_number: 'S', project_no: 'P', section_no: 'SEC', part_no: 'PART', anomalies_only: 'true', sort_by: 'length', descending: 'false', page: '3', page_size: '50', unrelated: 'keep' }
    const { state, calls, router } = await mountWelding(t, kind, query)
    const expected = parseWeldingRouteQuery(query, kind, '2026-10-26')
    assert.equal(calls.length, 1)
    assert.deepEqual(calls[0].query, expected)
    assert.deepEqual({ ...state.draft }, expected)
    assert.deepEqual(state.activeQuery.value, kind === 'parts' ? { ...expected, state: null, part_state: expected.part_state === '' ? null : expected.part_state } : expected)
    assert.deepEqual(router.currentRoute.value.query, weldingRouteQuery(query, expected))
    await flushWelding()
    assert.equal(calls.length, 1, 'normalizing the URL does not replay the query')
  })
}

for (const [query, expected] of [
  [{ part_state: '0' }, 0], [{ part_state: '3' }, 3], [{ state: '0' }, 0], [{ state: '3' }, 3],
  [{ state: '4', part_state: '0' }, 0], [{ state: '4', part_state: '3' }, 3],
  [{ state: '4', part_state: '' }, ''], [{ state: '4', part_state: 'invalid' }, ''],
]) {
  test(`parts state links normalize ${JSON.stringify(query)} without hidden job state`, async t => {
    const { state, calls, router } = await mountWelding(t, 'parts', { ...period, ...query })
    assert.equal(state.draft.part_state, expected)
    assert.equal(state.draft.state, '')
    assert.equal(calls[0].query.part_state, expected)
    assert.equal(calls[0].query.state, '')
    assert.equal(state.activeQuery.value.part_state, expected === '' ? null : expected)
    assert.equal(router.currentRoute.value.query.part_state, expected === '' ? undefined : String(expected))
    assert.equal('state' in router.currentRoute.value.query, false)
    assert.equal(calls.length, 1)
  })
}

test('parts state editing, clearing, failure and history keep one applied state for queries, exports and analysis', async t => {
  const { state, calls, router, downloads, analyses, hold } = await mountWelding(t, 'parts', { ...period, part_state: '3', unrelated: 'keep' })
  state.draft.part_state = 0
  assert.equal(state.activeQuery.value.part_state, 3)
  assert.equal(router.currentRoute.value.query.part_state, '3')
  await state.submit(); await flushWelding()
  assert.equal(state.activeQuery.value.part_state, 0)
  assert.equal(router.currentRoute.value.query.part_state, '0')
  state.draft.part_state = 4
  for (const format of ['csv', 'html', 'pdf']) await state.exportReport(format)
  await state.runAnalysis()
  assert.ok(downloads.every(({ url }) => url.searchParams.get('part_state') === '0' && !url.searchParams.has('state')))
  assert.equal(analyses[0].query.part_state, 0)
  assert.equal(analyses[0].query.state, null)
  assert.equal(analyses[0].version, state.report.value.data_version)
  await state.submit(); await flushWelding()
  assert.equal(state.activeQuery.value.part_state, 4)
  state.draft.part_state = ''
  await state.submit(); await flushWelding()
  assert.equal(state.activeQuery.value.part_state, null)
  assert.equal('part_state' in router.currentRoute.value.query, false)
  assert.equal('state' in router.currentRoute.value.query, false)
  router.back(); await flushWelding()
  assert.equal(state.draft.part_state, 4)
  assert.equal(state.activeQuery.value.part_state, 4)
  router.back(); await flushWelding()
  assert.equal(state.draft.part_state, 0)
  assert.equal(state.activeQuery.value.part_state, 0)
  router.forward(); await flushWelding()
  assert.equal(state.draft.part_state, 4)
  const version = state.report.value.data_version
  const previousQuery = { ...router.currentRoute.value.query }
  hold()
  state.draft.part_state = 5
  const failed = state.submit()
  calls.at(-1).reject(new Error('state read failed')); await failed
  assert.equal(state.draft.part_state, 5)
  assert.equal(state.activeQuery.value.part_state, 4)
  assert.equal(state.report.value.data_version, version)
  assert.deepEqual(router.currentRoute.value.query, previousQuery)
  assert.equal(previousQuery.unrelated, 'keep')
  await state.exportReport('csv')
  await state.runAnalysis()
  assert.equal(downloads.at(-1).url.searchParams.get('part_state'), '4')
  assert.equal(analyses.at(-1).query.part_state, 4)
})

test('jobs state keeps its meaning and ignores a parts state from a copied link', async t => {
  const { state, calls, router } = await mountWelding(t, 'jobs', { ...period, state: '0', part_state: '3', robot_no: '0', device_no: '0' })
  assert.equal(state.draft.state, 0)
  assert.equal(state.draft.part_state, '')
  assert.equal(state.activeQuery.value.robot_no, 0)
  assert.equal(state.activeQuery.value.device_no, 0)
  state.draft.state = 3
  await state.submit(); await flushWelding()
  assert.equal(calls.at(-1).query.state, 3)
  assert.equal(state.activeQuery.value.state, 3)
  assert.equal(router.currentRoute.value.query.state, '3')
  assert.equal('part_state' in router.currentRoute.value.query, false)
})

test('welding queries: successful submit, sorting and pagination sync URL while preserving unsubmitted drafts', async t => {
  const page = await mountWelding(t, 'jobs', { ...period, unrelated: 'keep' })
  const { state, calls, router, hold } = page
  Object.assign(state.draft, { device_no: 0, robot_no: 0, weld_type: 2, state: 3, serial_number: 'S', anomalies_only: true, sort_by: 'length', descending: false })
  assert.equal(router.currentRoute.value.query.serial_number, undefined)
  hold()
  const pending = state.submit()
  assert.equal(router.currentRoute.value.query.serial_number, undefined, 'only successful queries change the URL')
  state.draft.serial_number = 'unsaved'
  calls[1].resolve(); await pending; await flushWelding()
  assert.equal(calls.length, 2)
  assert.equal(state.draft.serial_number, 'unsaved')
  for (const [key, value] of Object.entries({ device_no: '0', robot_no: '0', weld_type: '2', state: '3', serial_number: 'S', anomalies_only: 'true', sort_by: 'length', descending: 'false', unrelated: 'keep' })) assert.equal(router.currentRoute.value.query[key], value)
  const second = state.changePage(3)
  assert.equal(calls[2].query.serial_number, 'S')
  calls[2].resolve(); await second; await flushWelding()
  assert.equal(router.currentRoute.value.query.page, '3')
  const resized = state.changePageSize(50)
  assert.equal(calls[3].query.page, 1)
  assert.equal(calls[3].query.serial_number, 'S')
  calls[3].resolve(); await resized; await flushWelding()
  assert.equal(router.currentRoute.value.query.page_size, '10')
  assert.equal(state.draft.serial_number, 'unsaved')
  const failed = state.changePage(2)
  calls[4].reject(new Error('query failed')); await failed
  assert.equal(router.currentRoute.value.query.page, '1')
  assert.equal(state.activeQuery.value.page, 1)
  assert.match(state.error.value, /query failed/)
})

test('welding history: back and forward restore filters; late requests cannot replace the navigation target', async t => {
  const { state, calls, router, hold } = await mountWelding(t, 'jobs', { ...period, device_no: '0' })
  state.draft.device_no = 2
  await state.submit(); await flushWelding()
  assert.equal(calls.length, 2)
  hold()
  router.back(); await flushWelding()
  assert.equal(calls[2].query.device_no, 0)
  assert.equal(state.draft.device_no, 0)
  router.forward(); await flushWelding()
  assert.equal(calls[2].signal.aborted, true)
  assert.equal(state.loading.value, false)
  assert.equal(state.draft.device_no, 2)
  calls[2].resolve(); await flushWelding()
  assert.equal(state.activeQuery.value.device_no, 2)
  router.back(); await flushWelding()
  calls[3].resolve(); await flushWelding()
  assert.equal(state.activeQuery.value.device_no, 0)
  router.forward(); await flushWelding()
  assert.equal(calls[4].query.device_no, 2)
  await router.push({ query: { ...router.currentRoute.value.query, device_no: '3' } }); await flushWelding()
  calls[5].resolve(); await flushWelding()
  calls[4].resolve(); await flushWelding()
  assert.equal(calls[4].signal.aborted, true)
  assert.equal(state.activeQuery.value.device_no, 3)
  assert.equal(router.currentRoute.value.query.device_no, '3')
  assert.equal(calls.length, 6)
})

test('welding exports, analysis and part drilldown use the successful snapshot after draft edits', async t => {
  const { state, downloads, analyses, calls, router } = await mountWelding(t, 'jobs', { start: '2026-09-01', end: '2026-09-02', device_no: '0', robot_no: '0', serial_number: 'S', page: '2', sort_by: 'id' })
  Object.assign(state.draft, { start: '2026-10-26', end: '2026-10-26', serial_number: 'unsaved', device_no: 7 })
  for (const format of ['csv', 'html', 'pdf']) await state.exportReport(format)
  await state.runAnalysis()
  for (const { url, filename } of downloads) {
    assert.equal(url.searchParams.get('start'), '2026-09-01')
    assert.equal(url.searchParams.get('device_no'), '0')
    assert.equal(url.searchParams.get('serial_number'), 'S')
    assert.equal(url.searchParams.get('page'), '2')
    assert.match(filename, /2026-09-01-2026-09-02/)
  }
  assert.deepEqual(analyses[0], { kind: 'jobs', query: { ...state.activeQuery.value }, version: 'version-1' })
  await state.openDetail({ id: 1, serial_number: 'S', weld_type_label: 'weld' })
  state.goToPart(); await flushWelding()
  assert.equal(router.currentRoute.value.name, 'welding-parts')
  assert.equal(calls.at(-1).query.serial_number, 'S')
  assert.equal(calls.at(-1).query.start, '2026-09-01')
  await state.openDetail({ id: 9 })
  assert.equal(state.details.value.part.id, 9)
})

test('welding scope changes: report kinds and production lines cannot reuse obsolete responses', async t => {
  const { state, calls, router, selection, hold, unmount } = await mountWelding(t, 'jobs', { ...period, robot_no: '0', anomalies_only: 'true' })
  hold()
  const pending = state.changePage(2)
  await router.push({ name: 'welding-parts', query: { ...period, project_no: 'P', robot_no: '0' } }); await flushWelding()
  assert.equal(calls[1].signal.aborted, true)
  assert.equal(state.report.value, null)
  assert.equal(calls[2].query.robot_no, '')
  selection.id = 'line-b'; await flushWelding()
  assert.equal(calls[2].signal.aborted, true)
  assert.equal(calls[3].line, 'line-b')
  calls[3].resolve(); await flushWelding()
  calls[2].resolve(); calls[1].resolve(); await pending; await flushWelding()
  assert.equal(state.report.value.data_version, 'version-4')
  assert.equal(state.activeQuery.value.project_no, 'P')
  const final = state.changePage(2)
  unmount()
  assert.equal(calls[4].signal.aborted, true)
  calls[4].resolve(); await final
  assert.equal(state.report.value.data_version, 'version-4')
})

test('one welding request stays pending while the server queues it', async t => {
  let finish
  const fetchMock = t.mock.method(globalThis, 'fetch', () => new Promise(resolve => { finish = resolve }))
  const app = view(getWeldingReport)
  const pending = app.read({ ...period, device_no: 0 })
  await settle()
  assert.equal(app.loading.value, true)
  assert.equal(app.error.value, '')
  assert.equal(fetchMock.mock.callCount(), 1)
  finish(Response.json({ success: true, data: { query: { ...period, device_no: 0 }, data_version: 'ok' } }))
  await pending
  assert.equal(app.report.value.data_version, 'ok')
  assert.equal(app.loading.value, false)
})

test('leaving a welding page cancels the pending request without replay', async t => {
  let reads = 0
  t.mock.method(globalThis, 'fetch', (url, options) => {
    if (url.includes('/requests/')) return Promise.resolve(Response.json({ success: true }))
    reads += 1
    return new Promise((resolve, reject) => options.signal.addEventListener('abort', () => reject(new DOMException('cancelled', 'AbortError')), { once: true }))
  })
  const app = view(getWeldingReport)
  const pending = app.read({ ...period,})
  await settle()
  app.cleanup.forEach(fn => fn())
  await pending
  assert.equal(reads, 1)
  assert.equal(app.error.value, '')
  assert.equal(app.report.value, null)
})

test('queue overflow is shown once and retains the last successful report', async t => {
  let calls = 0
  t.mock.method(globalThis, 'fetch', async () => ++calls === 1 ? Response.json({ success: true, data: { query: { ...period, device_no: 0 }, data_version: 'previous' } }) : busyResponse())
  const app = view(getWeldingReport)
  await app.read({ ...period, device_no: 0 })
  await app.read({ ...period, device_no: 1 })
  assert.equal(calls, 2)
  assert.match(app.error.value, /请求繁忙/)
  assert.equal(app.loading.value, false)
  assert.equal(app.report.value.data_version, 'previous')
  assert.equal(app.activeQuery.value.device_no, 0)
})

test('source, authentication and network failures remain visible without replay', async t => {
  const responses = [
    () => Response.json({ detail: '数据源超时' }, { status: 503 }),
    () => Response.json({ detail: '后台刷新队列已满' }, { status: 503, headers: { 'X-Welding-Error': 'snapshot_queue_full' } }),
    () => Response.json({ detail: '权限不足' }, { status: 403, headers: { 'Retry-After': '1' } }),
    () => busyResponse('invalid'),
    () => busyResponse('0'),
    () => busyResponse('-1'),
    () => { throw new TypeError('network failure') },
  ]
  for (const response of responses) {
    const fetchMock = t.mock.method(globalThis, 'fetch', async () => response())
    await assert.rejects(getWeldingReport('jobs', period))
    assert.equal(fetchMock.mock.callCount(), 1)
    fetchMock.mock.restore()
  }
})

test('reports, details, downloads and analysis never replay a capacity rejection', async t => {
  for (const read of [
    ...['dashboard', 'daily', 'parts', 'jobs', 'equipment'].map(kind => () => getWeldingReport(kind, period)),
    () => getWeldingPart(1, 1),
    () => downloadWelding('/api/v1/welding/export', 'report.pdf'),
    () => analyzeWelding('jobs', period, 'version'),
  ]) {
    const fetchMock = t.mock.method(globalThis, 'fetch', async () => busyResponse())
    await assert.rejects(read(), /请求繁忙/)
    assert.equal(fetchMock.mock.callCount(), 1)
    fetchMock.mock.restore()
  }
})

test('job to part drilldown retains the applied historical production period', () => {
  const destinations = []
  const app = view(async () => ({}), { route: { query: { start: '2026-09-01', end: '2026-09-02' } }, router: { push: value => destinations.push(value) } })
  assert.equal(app.draft.start, '2026-09-01')
  assert.equal(app.draft.end, '2026-09-02')
  app.activeQuery.value = { start: '2026-09-01', end: '2026-09-02' }
  app.detailJob.value = { serial_number: 'part-A' }
  app.goToPart()
  assert.deepEqual(destinations[0].query, { serial_number: 'part-A', start: '2026-09-01', end: '2026-09-02' })
})

test('late responses cannot overwrite newer filters even if fetch ignores abort', async () => {
  const pending = []
  const app = view((kind, values, signal) => new Promise(resolve => pending.push({ values, signal, resolve })))
  const first = app.read({ ...period, device_no: 0 })
  const next = app.read({ ...period, device_no: 1 })
  assert.equal(pending[0].signal.aborted, true)
  pending[1].resolve({ query: { ...period, device_no: 1 }, data_version: 'new' })
  await next
  pending[0].resolve({ query: { ...period, device_no: 0 }, data_version: 'old' })
  await first
  assert.equal(app.report.value.data_version, 'new')
  assert.equal(app.activeQuery.value.device_no, 1)
  const last = app.read({ ...period, device_no: 2 })
  app.cleanup.forEach(fn => fn())
  pending[2].resolve({ query: { ...period, device_no: 2 }, data_version: 'unmounted' })
  await last
  assert.equal(app.report.value.data_version, 'new')
})

test('query failure preserves the last successful data and export filter snapshot', async () => {
  let fail = false
  const app = view(async (kind, values) => { if (fail) throw new Error('source unavailable'); return { query: values, data_version: 'old' } })
  await app.read({ ...period, device_no: 0 })
  fail = true
  await app.read({ ...period, device_no: 1 })
  assert.equal(app.error.value, 'source unavailable')
  assert.equal(app.activeQuery.value.device_no, 0)
  assert.equal(app.report.value.data_version, 'old')
})

test('a disabled welding line remains navigable without querying an unconfigured source', async () => {
  let calls = 0
  const app = view(async () => { calls += 1 }, { enabled: false })
  await app.read({ ...period, start: '2026-10-26', end: '2026-10-26' })
  assert.equal(calls, 0)
  assert.equal(app.loading.value, false)
  assert.equal(app.report.value, null)
  assert.equal(app.error.value, '')
  assert.match(source('views/WeldingReportView.vue'), /焊接数据库尚未启用[\s\S]*action-label="配置数据库"/)
})

test('welding templates compile with loading, error, pending, detail and responsive states', () => {
  for (const path of ['views/WeldingHomeView.vue', 'views/WeldingReportView.vue', 'components/WeldingChart.vue', 'components/WeldingKpiCards.vue', 'components/WeldingRecord.vue', 'components/WeldingCharts.vue', 'components/ReportAnalysis.vue', 'components/AppReportTable.vue', 'components/WeldingTable.vue', 'components/DeviceSwitcher.vue', 'views/DatabaseView.vue', 'layouts/modules/ThemeTabs.vue', 'layouts/AdminLayout.vue', 'layouts/modules/global-menu/modules/vertical-mix-menu.vue']) {
    const { descriptor, errors } = parse(source(path))
    assert.deepEqual(errors, [])
    assert.doesNotThrow(() => compileScript(descriptor, { id: path }), path)
    const result = compileTemplate({ source: descriptor.template.content, filename: path, id: path, compilerOptions: { bindingMetadata: {} } })
    assert.deepEqual(result.errors, [], path)
  }
  const page = source('views/WeldingReportView.vue')
  assert.match(page, /上次成功查询结果/)
  assert.match(page, /max-width:600px/)
  assert.match(page, /NModal/)
  assert.match(page, /data_version/)
  assert.match(page, /ReportPanelState v-else-if="loading" type="loading"/)
  assert.match(source('views/DatabaseView.vue'), /weld_password[\s\S]*type="password"/)
  assert.match(page, /<NCheckbox v-model:checked="draft.descending"/)
})

test('identical in-flight filters reuse the query and a later explicit refresh still runs', async () => {
  const pending = []
  const app = view((kind, values, signal) => new Promise(resolve => pending.push({ values, signal, resolve })))
  const first = app.read({ ...period, device_no: 0, page: 1 })
  const same = app.read({ ...period, page: 1, device_no: 0, serial_number: '' })
  assert.equal(same, first)
  assert.equal(pending.length, 1)
  assert.equal(pending[0].signal.aborted, false)
  pending[0].resolve({ query: pending[0].values, data_version: 'first' })
  await first
  const refresh = app.read({ ...period, device_no: 0, page: 1 })
  assert.equal(pending.length, 2)
  pending[1].resolve({ query: pending[1].values, data_version: 'refreshed' })
  await refresh
  assert.equal(app.report.value.data_version, 'refreshed')
})

test('initial watcher and repeated submit share one active request; pagination sends the new page once', async () => {
  const pending = []
  const watchers = []
  const app = view((kind, values, signal) => new Promise(resolve => pending.push({ values, signal, resolve })), { watchers })
  assert.equal(pending.length, 1)
  const initial = app.read({ ...period, ...app.draft, page: 1, page_size: 20 })
  app.submit()
  assert.equal(pending.length, 1)
  assert.equal(pending[0].signal.aborted, false)
  pending[0].resolve({ query: pending[0].values, data_version: 'first' })
  await initial
  app.changePage(2)
  const next = app.read({ ...period, ...app.activeQuery.value, page: 2 })
  assert.equal(pending.length, 2)
  assert.equal(pending[1].values.page, 2)
  pending[1].resolve({ query: pending[1].values, data_version: 'second' })
  await next
  assert.equal(app.activeQuery.value.page, 2)
})

test('welding table keys and detail events respect aggregate, job, part and seam shapes', () => {
  const script = parse(source('components/WeldingTable.vue')).descriptor.scriptSetup.content.replace(/^import .*$/gm, '')
  for (const [kind, row, key] of [
    ['daily', { production_date: '2026-10-01' }, '2026-10-01'],
    ['equipment', { device_no: 0, robot_no: null }, '0:null'],
    ['jobs', { id: 1, serial_number: null, weld_type_label: '平焊' }, 1],
    ['parts', { id: 2, project_no: null }, 2],
    ['seams', { id: 3, robot_no: null }, 3],
  ]) {
    const events = []
    const bindings = { ...Vue, onMounted() {}, onUpdated() {}, onBeforeUnmount() {}, onActivated() {}, onDeactivated() {}, computed, h, NButton: 'button', weldingColumns, weldingVisibleColumns, weldingColumnLayout, weldingDisplay,
      defineSlots: () => ({}), defineProps: () => ({ kind }), defineEmits: () => (...args) => events.push(args) }
    const table = scriptFunction(...Object.keys(bindings), `${script}; return { columns, rowKey };`)(...Object.values(bindings))
    assert.equal(table.rowKey(row), key)
    for (const column of table.columns.value) {
      assert.equal(column.titleAlign, column.align || 'left')
      if (/length|hours|count|output|speed|duration|seconds|consumption|rate/.test(column.key)) assert.equal(column.align, 'right')
      if (['state_label', 'weld_type_label', 'actions'].includes(column.key)) assert.equal(column.align, 'center')
      if (['robot_no', 'welding_seam_no', 'serial_number', 'id'].includes(column.key)) assert.equal(column.align, 'left')
    }
    const action = table.columns.value.find(column => column.key === 'actions')
    if (kind === 'jobs' || kind === 'parts') {
      action.render(row).props.onClick()
      assert.deepEqual(events, [['detail', row]])
      assert.equal(action.render({ production_date: '2026-10-01' }), null)
    } else assert.equal(action, undefined)
  }
})

test('unsubmitted filters and pagination cannot send a request without a valid period', () => {
  let calls = 0
  const app = view(async () => { calls += 1 })
  app.changePage(2)
  app.changePageSize(50)
  assert.equal(calls, 0)
  for (const range of [
    { start: '', end: period.end }, { start: period.start, end: '' },
    { start: '2026-02-30', end: period.end },
    { start: '2026-10-27', end: '2026-10-26' },
    { start: '2025-01-01', end: '2026-10-26' },
  ]) {
    Object.assign(app.draft, range)
    app.submit()
    assert.match(app.actionError.value, /有效日期区间/)
  }
  assert.equal(calls, 0)
  Object.assign(app.draft, period)
  app.submit()
  assert.equal(calls, 1)
  assert.equal(app.actionError.value, '')
})

test('router resolves registered line types without requiring a fixed welding ID', async () => {
  const sourceCode = source('router/index.ts')
  const guard = sourceCode.slice(sourceCode.indexOf('router.beforeEach('), sourceCode.indexOf('\nrouter.afterEach('))
  const selection = { id: 'Cutter01', items: [{ id: 'Cutter01', type: 'cutting' }, { id: 'weld-2', type: 'welding' }] }
  let beforeEach
  const bindings = { router: { beforeEach: fn => { beforeEach = fn } }, loadSession: async () => {},
    session: { user: { role: 'admin' } }, roleHome: () => '/worker/tasks', loadDevices: async () => {},
    deviceSelection: selection, changeDevice: id => { selection.id = id }, selectedLineType, lineHome }
  scriptFunction(...Object.keys(bindings), guard)(...Object.values(bindings))
  const route = line => ({ meta: {}, matched: [{ meta: { role: 'admin' } }, { meta: { line } }] })
  assert.equal(await beforeEach(route('welding')), true)
  assert.equal(selection.id, 'weld-2')
  assert.equal(await beforeEach(route('welding')), true)
  assert.equal(selection.id, 'weld-2')
  assert.equal(await beforeEach(route('coating')), '/admin/welding/home')
  assert.equal(selection.id, 'weld-2')
  assert.equal(await beforeEach(route('cutting')), true)
  assert.equal(selection.id, 'Cutter01')
  bindings.session.user.role = 'worker'
  assert.equal(await beforeEach(route('welding')), '/worker/tasks')
  assert.equal(selection.id, 'Cutter01')
})

test('new line types keep registered labels and destinations', () => {
  const items = [{ id: 'paint-1', type: 'coating', type_name: '涂装', name: '涂装一线', home_path: '/admin/coating/home' }]
  assert.equal(productionGroups(items)[0].name, '涂装')
  assert.equal(selectedLineType(items, 'paint-1'), 'coating')
  assert.equal(lineHome(items, 'paint-1'), '/admin/coating/home')
  assert.equal(productionGroups([{ ...items[0], type_name: '' }])[0].name, 'coating')
})

test('explicit refresh requests fresh statistics while initial reads and pagination reuse snapshots', async t => {
  const clock = snapshotWaitTimers(t)
  const urls = []
  t.mock.method(globalThis, 'fetch', async url => { urls.push(new URL(url, 'http://test')); return Response.json({ success: true, data: {} }) })
  await getWeldingReport('daily', period)
  await getWeldingReport('daily', period, undefined, true)
  assert.equal(urls[0].searchParams.has('refresh'), false)
  assert.equal(urls[1].searchParams.get('refresh'), 'true')
  assert.equal(urls[1].searchParams.get('start'), period.start)
  const calls = []
  const meta = { source: 'snapshot', stale: true, refreshing: true, data_as_of: '2026-10-26T08:00:00Z' }
  const app = view(async (kind, values, signal, force) => { calls.push(force); return { query: values, data_version: 'same', meta: { report_data: meta } } })
  t.after(() => app.cleanup.forEach(cleanup => cleanup()))
  await app.read(period)
  assert.equal(calls[0], false)
  assert.equal(app.report.value.meta.report_data.stale, true)
  app.submit(true)
  await settle()
  assert.equal(calls[1], true)
  app.changePage(2)
  await settle()
  assert.equal(calls[2], false)
  assert.equal(app.report.value.meta.report_data.data_as_of, meta.data_as_of)
  assert.equal(calls.length, 3, 'only one background check should be scheduled after each read')
  assert.equal(clock.timers.size, 1)
  assert.match(source('views/WeldingReportView.vue'), /统计截止/)
  assert.match(source('views/WeldingReportView.vue'), /统计已过期/)
  assert.match(source('views/WeldingReportView.vue'), /后台刷新排队中/)
  assert.match(source('views/WeldingReportView.vue'), /刷新失败，保留上次统计/)
})


test('shared cutting table renders escaped cells, header controls, summary and empty state', async () => {
  const props = { columns: [{ key: 'c0', title: '任务', width: 160, align: 'left' }, { key: 'c1', title: '长度', width: 120, align: 'right' }],
    items: [{ id: 'task-1', program: '<script>bad</script>', length: 12 }], rowKey: row => row.id, paginate: false, summary: true }
  const slots = { 'header-c0': () => h('input', { type: 'checkbox', 'aria-label': '选择当前页' }),
    c0: ({ row }) => row.program, c1: ({ row }) => String(row.length),
    'summary-c0': () => '汇总', 'summary-c1': () => '12', empty: () => '当前无任务' }
  const render = async items => {
    const app = Vue.createSSRApp({ render: () => h(SharedDataTable, { ...props, items }, slots) })
    setupCssr(app)
    return renderToString(app)
  }
  const html = await render(props.items)
  assert.match(html, /n-data-table/)
  assert.equal((html.match(/<th\b[^>]*text-align:\s*left/g) || []).length, 1)
  assert.equal((html.match(/<th\b[^>]*text-align:\s*right/g) || []).length, 1)
  assert.match(html, /n-data-table-td--right-align/)
  assert.match(parse(source('components/AppReportTable.vue')).descriptor.styles[0].content, /\.app-report-table\{width:100%;min-width:0\}/)
  assert.match(html, /aria-label="选择当前页"/)
  assert.match(html, /&lt;script&gt;bad&lt;\/script&gt;/)
  assert.match(html, /汇总/)
  assert.doesNotMatch(html, /<script>bad/)
  assert.match(await render([]), /当前无任务/)
})

test('shared pagination forwards page and size changes once and respects loading', async () => {
  const Pagination = Vue.defineComponent({
    props: ['page', 'pageSize', 'itemCount', 'disabled', 'showSizePicker'], emits: ['update:page', 'update:page-size'],
    setup(props, { emit }) {
      if (!props.disabled) { emit('update:page', props.page + 1); if (props.showSizePicker) emit('update:page-size', 50) }
      return () => null
    },
  })
  const Table = createSharedDataTable({ NPagination: Pagination })
  for (const loading of [false, true]) {
    const events = []
    const app = Vue.createSSRApp({ render: () => h(Table, { columns: [{ key: 'c0', title: '任务', width: 160 }],
      items: [], rowKey: row => row.id, total: 100, page: 2, pageSize: 20, loading,
      onPage: value => events.push(['page', value]), 'onPage-size': value => events.push(['size', value]) }) })
    setupCssr(app)
    await renderToString(app)
    assert.deepEqual(events, loading ? [] : [['page', 3], ['size', 50]])
  }
})

test('welding measurements keep fixed decimals while counts and identifiers retain their meaning', () => {
  for (const key of ['weld_length_m', 'working_hours', 'weld_speed', 'arc_rate', 'duration_seconds', 'wire_consumption']) {
    assert.equal(weldingDisplay(12.5, key), '12.500')
    assert.equal(weldingDisplay(0, key), '0.000')
  }
  assert.equal(weldingDisplay(1240, 'job_count'), '1,240')
  assert.equal(weldingDisplay(1240, 'robot_no'), '1240')
  assert.equal(weldingDisplay('0012', 'welding_seam_no'), '0012')
  assert.equal(weldingDisplay(null, 'arc_rate'), '待核实')
  const styles = parse(source('components/AppReportTable.vue')).descriptor.styles[0].content
  assert.match(styles, /n-data-table-td\)\{[^}]*font-variant-numeric:tabular-nums;[^}]*vertical-align:middle/)
})




test('shared report table preserves declared Naive widths across short, long and empty page data', async () => {
  const snapshots = []
  const DataTable = Vue.defineComponent({ props: ['columns', 'data', 'scrollX', 'tableLayout', 'rowKey'],
    setup(props) { snapshots.push({ columns: props.columns, scrollX: props.scrollX, layout: props.tableLayout }); return () => null } })
  const Table = createSharedDataTable({ NDataTable: DataTable })
  const columns = [
    { key: 'c0', title: '姓名', width: 96, align: 'left' },
    { key: 'c1', title: '文件名', minWidth: 220, align: 'left' },
    { key: 'c2', title: '状态', width: 104, align: 'center' },
  ]
  for (const items of [[], [{ id: 1, name: '张三', file: 'a.nc' }], [{ id: 2, name: '姓名'.repeat(100), file: 'file'.repeat(100) }]]) {
    const app = Vue.createSSRApp({ render: () => h(Table, { columns, items, rowKey: row => row.id, paginate: false }) })
    await renderToString(app)
  }
  for (const snapshot of snapshots) {
    assert.equal(snapshot.layout, 'fixed')
    assert.equal(snapshot.scrollX, 420)
    assert.deepEqual(snapshot.columns.map(column => [column.width, column.minWidth]), [[96, undefined], [undefined, 220], [104, undefined]])
    assert.ok(snapshot.columns.every(column => column.titleAlign === column.align && column.ellipsis.tooltip))
  }
  assert.equal(snapshots.length, 3)
  assert.doesNotMatch(source('components/AppReportTable.vue'), /ResizeObserver|getComputedStyle|measureText|querySelector|onUpdated|tableColumnWeight|weldingColumns/)
})

test('welding adapter has explicit layouts for every field and only delegates presentation to AppReportTable', () => {
  for (const [kind, fields] of Object.entries(weldingColumns)) {
    for (const [key] of fields) {
      const layout = weldingColumnLayout[key]
      assert.ok(layout, `${kind}.${key}`)
      assert.ok(Number(layout.width ?? layout.minWidth) > 0, `${kind}.${key} must have a valid native width`)
    }
  }
  assert.equal(weldingColumnLayout.quality_reasons.width, 200)
  assert.equal(weldingColumnLayout.project_name.minWidth, 180)
  assert.match(source('components/WeldingTable.vue'), /<AppReportTable/)
  assert.doesNotMatch(source('components/WeldingTable.vue'), /ResizeObserver|measureText|NDataTable|NPagination/)
})

test('workshop columns keep short names and program codes bounded and reserve confirmation controls explicitly', () => {
  const script = parse(source('views/WorkshopView.vue')).descriptor.scriptSetup.content
  const result = Vue.ref({ items: [{ program_no: 'OP-027', confirmed_by: null }] })
  const table = scriptFunction('computed', 'isWorker', 'result', `${script.slice(script.indexOf('const taskTableColumns ='), script.indexOf('</script>'))}; return { taskTableColumns, workerTableColumns }`)(computed, { value: false }, result)
  assert.equal(table.taskTableColumns.value.find(column => column.key === 'c5').width, 160)
  assert.equal(table.workerTableColumns.find(column => column.key === 'c2').width, 96)
  assert.equal(table.taskTableColumns.value.find(column => column.key === 'c10').width, 116)
  result.value.items = [{ program_no: 'long'.repeat(100), confirmed_by: '张三' }]
  assert.equal(table.taskTableColumns.value.find(column => column.key === 'c5').width, 160)
  assert.equal(table.taskTableColumns.value.find(column => column.key === 'c10').width, 240)
})

test('independent welding home waits for its catalog, keeps successful data on failure and ignores late disabled-source results', async () => {
  const selection = reactive({ id: 'welding', items: [] })
  const calls = [], cleanup = [], scope = Vue.effectScope()
  const bindings = { ...Vue, deviceSelection: selection, selectedLineType, parseWeldingRouteQuery,
    useRoute: () => ({ query: {} }), useRouter: () => ({ push() {} }), reportToday: () => '2026-10-26',
    useReportChartTheme: () => ({ colors: ref({}), vars: ref({}) }), onBeforeUnmount: fn => cleanup.push(fn),
    getWeldingReport: (kind, query, signal) => new Promise((resolve, reject) => calls.push({ kind, query, signal, resolve, reject })),
  }
  const script = parse(source('views/WeldingHomeView.vue')).descriptor.scriptSetup.content.replace(/^import .*$/gm, '')
  const setup = scriptFunction(...Object.keys(bindings), `${script}; return { load, report, error };`)
  const home = scope.run(() => setup(...Object.values(bindings)))
  assert.equal(calls.length, 0)
  selection.items = [{ id: 'welding', type: 'welding', enabled: true }]
  await Vue.nextTick()
  assert.equal(calls.length, 1)
  assert.equal(calls[0].kind, 'dashboard')
  assert.equal(calls[0].query.start, '2026-10-20')
  assert.equal(calls[0].query.page_size, 10)
  calls[0].resolve({ today: { job_count: 0 }, meta: {} })
  await settle()
  const failed = home.load(true)
  calls[1].reject(new Error('source unavailable'))
  await failed
  assert.equal(home.report.value.today.job_count, 0)
  assert.equal(home.error.value, 'source unavailable')
  const late = home.load(true)
  selection.items[0].enabled = false
  await Vue.nextTick()
  assert.equal(calls[2].signal.aborted, true)
  calls[2].resolve({ today: { job_count: 999 }, meta: {} })
  await late
  assert.equal(home.report.value, null)
  cleanup.forEach(fn => fn()); scope.stop()
})


test('welding paginated details use ten rows without an internal height limit or a query-only button palette', async t => {
  for (const kind of ['daily', 'parts', 'jobs', 'equipment']) {
    for (const size of [undefined, '20', '50', 'bad']) {
      assert.equal(parseWeldingRouteQuery({ page_size: size }, kind, '2026-10-30').page_size, 10)
    }
  }
  const table = source('components/WeldingTable.vue')
  assert.match(table, /:page-sizes="\[\]"/)
  assert.match(table, /:max-height="paginate \? null : undefined"/)
  const styles = parse(source('views/WeldingReportView.vue')).descriptor.styles[0].content
  assert.doesNotMatch(styles, /--n-color(?:-hover|-focus|-pressed)?:/)
  assert.match(styles, /\.welding-filters>\.welding-query-submit\{padding:0 16px;gap:0;justify-content:center;/)
  const fetchMock = t.mock.method(globalThis, 'fetch', async () => Response.json({ part: { id: 2 }, items: [], total: 0 }))
  await getWeldingPart(2, 3)
  const url = new URL(fetchMock.mock.calls[0].arguments[0], 'http://localhost')
  assert.equal(url.searchParams.get('page'), '3')
  assert.equal(url.searchParams.get('page_size'), '10')
})
