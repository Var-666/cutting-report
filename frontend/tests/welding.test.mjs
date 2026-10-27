import { scriptFunction } from './typescript-helpers.mjs'
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { computed, h, reactive, ref } from 'vue'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'
import { lineHome, productionGroups, selectedLineType } from '../src/utils/productionLines.ts'
import { adminMenus } from '../src/router/menus.ts'
import { analyzeWelding, downloadWelding, getWeldingPart, getWeldingReport, weldingQuery } from '../src/api/welding.ts'
import { weldingColumns, weldingDisplay, weldingKpis, weldingTitles } from '../src/utils/weldingReport.ts'
import { weldingRoutes } from '../src/router/weldingRoutes.ts'
import { queryDateRange } from '../src/utils/reportRouteState.ts'

const source = path => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8')

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
  assert.match(source('views/WeldingReportView.vue'), /class="panel welding-analysis"[\s\S]*生成当前区间分析/)
})

test('query parameters retain zero device, robot and process codes; unknown is not zero', () => {
  assert.deepEqual(weldingQuery({ device_no: 0, robot_no: 0, weld_type: 0, state: '', project_no: null, anomalies_only: false }), { device_no: 0, robot_no: 0, weld_type: 0, anomalies_only: false })
  assert.equal(weldingDisplay(null, 'completed_part_count'), '待核实')
  assert.equal(weldingDisplay(0, 'weld_length_m'), '0')
  assert.equal(weldingDisplay(null, 'weld_length_m'), '未记录')
  const cards = weldingKpis('daily', { summary: { weld_length_m: 0, involved_part_count: 0 }, meta: { pending_metrics: { completed_part_count: 'pending' } } })
  assert.equal(cards[0].value, 0)
  assert.equal(cards[2].value, '待核实')
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
    defineProps: () => ({ kind: 'jobs' }), useRoute: () => options.route || ({ query: {} }), useRouter: () => options.router || ({ push() {} }), queryDateRange,
    reportToday: () => '2026-10-26', getWeldingReport: readReport,
    deviceSelection: { id: 'welding', items: [{ id: 'welding', type: 'welding', enabled: options.enabled ?? true }] }, weldingTitles, weldingKpis, weldingColumns, weldingDisplay, weldingQuery,
  }
  return { ...scriptFunction(...Object.keys(bindings), `${script}; return { read, submit, changePage, changePageSize, report, loading, error, activeQuery, draft, detailJob, goToPart, actionError };`)(...Object.values(bindings)), cleanup }
}

const period = { start: '2026-10-26', end: '2026-10-26' }
const busyResponse = (retryAfter = '1') => Response.json({ detail: '焊接数据查询请求繁忙，请稍后重试。' }, { status: 503, headers: { 'Retry-After': retryAfter } })
const settle = () => new Promise(resolve => setImmediate(resolve))

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
  for (const path of ['views/WeldingReportView.vue', 'components/WeldingCharts.vue', 'components/WeldingTable.vue', 'components/DeviceSwitcher.vue', 'views/DatabaseView.vue', 'layouts/modules/ThemeTabs.vue', 'layouts/AdminLayout.vue', 'layouts/modules/global-menu/modules/vertical-mix-menu.vue']) {
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
    const bindings = { computed, h, NButton: 'button', weldingColumns, weldingDisplay,
      defineProps: () => ({ kind }), defineEmits: () => (...args) => events.push(args) }
    const table = scriptFunction(...Object.keys(bindings), `${script}; return { columns, rowKey };`)(...Object.values(bindings))
    assert.equal(table.rowKey(row), key)
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
