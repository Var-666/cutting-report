import { reportModuleBindings } from './report-module-helpers.mjs'
import { useWorkshopConfirmation } from '../src/composables/useWorkshopConfirmation.ts'
import { useWorkshopCorrection } from '../src/composables/useWorkshopCorrection.ts'
import { renderComponent } from './vue-component-helpers.mjs'
import * as Vue from 'vue'
import { createRouter, createMemoryHistory, RouterView, useRoute, useRouter } from 'vue-router'
import { scriptFunction } from './typescript-helpers.mjs'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { parse, compileScript } from '@vue/compiler-sfc'
import {
  mergeOwnedQuery,
  queryChoice,
  queryDate,
  queryDateRange,
  queryPositiveInteger,
  queryText,
  sameRouteQuery,
} from '../src/utils/reportRouteState.ts'

const leadershipQuerySource = readFileSync(new URL('../src/composables/useLeadershipReportQuery.ts', import.meta.url), 'utf8')

function viewSource(name) {
  const source = readFileSync(new URL(`../src/views/${name}`, import.meta.url), 'utf8')
  return parse(source).descriptor.scriptSetup.content
}

test('shared report query parsing rejects links that cannot restore a valid report state', () => {
  assert.equal(queryDate('2024-02-29'), '2024-02-29')
  assert.equal(queryDate('2023-02-29'), '')
  assert.equal(queryDate(['2026-09-01', '2026-09-02']), '2026-09-01')
  assert.deepEqual(queryDateRange({ start: '2026-09-01', end: '2026-09-30' }), {
    start: '2026-09-01',
    end: '2026-09-30',
  })
  assert.equal(queryDateRange({ start: '2026-09-30', end: '2026-09-01' }), null)
  assert.equal(queryDateRange({ start: '2026-01-01', end: '2026-02-01' }, { maxDays: 31 }), null)
  assert.equal(queryChoice('line', ['bar', 'line'], 'bar'), 'line')
  assert.equal(queryChoice('pie', ['bar', 'line'], 'bar'), 'bar')
  assert.equal(queryPositiveInteger('3'), 3)
  assert.equal(queryPositiveInteger('0'), 1)
})

test('report-owned query updates preserve unrelated deep-link state and omit defaults', () => {
  const query = mergeOwnedQuery(
    { job: 'report-7', page: '4', page_size: '20', source: 'notice' },
    ['page', 'page_size'],
    { page: '', page_size: 50 },
  )

  assert.deepEqual(query, { job: 'report-7', source: 'notice', page_size: '50' })
  assert.equal(sameRouteQuery(query, { source: 'notice', page_size: '50', job: 'report-7' }), true)
  assert.equal(sameRouteQuery(query, { job: 'report-7', source: 'notice' }), false)
})

test('report views restore only applied state and keep worker task routing unchanged', () => {
  const leadership = viewSource('LeadershipReportView.vue')
  const equipment = viewSource('EnergyPeriodReportView.vue')
  const workshop = viewSource('WorkshopView.vue')
  const center = viewSource('ReportCenterView.vue')

  assert.match(leadershipQuerySource, /if \(syncRoute\) await syncRouteState\(\)/)
  assert.match(leadership, /watch\(\(\) => route\.query/)

  assert.match(workshop, /const appliedRouteState = reactive/)
  assert.match(workshop, /if \(isWorker\.value \|\| route\.name !== 'workshop-report'\) return/)
  assert.match(workshop, /load\(1, \{ force: true, syncRoute: !isWorker\.value \}\)/)
  assert.match(workshop, /page: data\.page/)

  assert.match(center, /const routeQueryKeys = \['page', 'page_size'\]/)
  assert.match(center, /loadHistory\(target, false, \{ syncRoute: true \}\)/)
  assert.match(center, /pageSize: Number\(queryChoice\(route\.query\.page_size/)
})

test('standard reports distinguish edited dates from the last successfully applied range', () => {
  const querySource = readFileSync(new URL('../src/components/StandardReportQuery.vue', import.meta.url), 'utf8')
  const query = parse(querySource).descriptor
  const leadership = viewSource('LeadershipReportView.vue')
  const equipment = viewSource('EnergyPeriodReportView.vue')
  const workshop = viewSource('WorkshopView.vue')

  assert.match(query.scriptSetup.content, /dateDirty: Boolean/)
  assert.match(query.template.content, /v-if="dateDirty && !loading"/)
  assert.match(query.template.content, /role="status"/)
  assert.match(query.template.content, /所选区间尚未加载成功，可重新查询/)
  assert.match(leadershipQuerySource, /periodStart\.value !== appliedPeriodStart\.value \|\| periodEnd\.value !== appliedPeriodEnd\.value/)
  assert.match(workshop, /filters\.startDate !== appliedRouteState\.startDate \|\| filters\.endDate !== appliedRouteState\.endDate/)
  for (const name of ['LeadershipReportView.vue', 'EnergyPeriodReportView.vue', 'WorkshopView.vue']) {
    const source = readFileSync(new URL(`../src/views/${name}`, import.meta.url), 'utf8')
    assert.match(source, /:date-dirty="dateQueryDirty"/)
  }
})

test('cached report query controls remain busy without showing a loading spinner', async () => {
  const props = { startDate: '2026-11-05', endDate: '2026-11-05', chartType: 'bar_line', loading: true, hasData: true }
  const warm = await renderComponent('components/StandardReportQuery', { ...props, showLoading: false })
  assert.ok(warm.includes('更新报表'))
  assert.ok(!warm.includes('查询中'))
  assert.ok(!warm.includes('class="spinner"'))
  assert.match(warm, /<button[^>]*disabled/)
  const cold = await renderComponent('components/StandardReportQuery', props)
  assert.ok(cold.includes('查询中') && cold.includes('class="spinner"'))
})

const flushReport = async () => { await new Promise(resolve => setImmediate(resolve)); await Vue.nextTick() }
const rangeA = { start: '2026-09-01', end: '2026-09-02' }
const rangeB = { start: '2026-09-03', end: '2026-09-04' }
function reportResult(start = '2026-10-01', end = '2026-10-27', id = 'initial', refreshing = false) {
  return { id, period_start: start, period_end: end, snapshot: { data_as_of: '2026-10-27', refreshing }, daily_records: [], monthly_records: [], rows: [], records: [], summary: {}, syncing: false }
}

// Run the page's real setup/watch/lifecycle code with memory-history navigation.
function emptyWorkshop(start, end) {
  return { report: { period_start: start, period_end: end, item_count: 0 }, items: [], total: 0,
    page: 1, page_size: 10, project_names: [], worker_summaries: [], duration_distribution: [], status_distribution: [], top_cut_length_tasks: [] }
}
async function mountReport(t, kind, { query = {}, refreshing = false, syncing = false, mode = 'manager', hold = {} } = {}) {
  const name = `${kind}-report`
  const pending = []
  const requests = []
  const exports = []
  const timers = new Map()
  const held = { range: false, monthly: false, summary: false, tasks: false, workshop: false, ...hold }
  const clock = { today: '2026-10-27', latest: '2026-10-26' }
  const chartUpdates = []
  let timerId = 0
  let state
  let mounts = 0
  const previousWindow = globalThis.window
  globalThis.window = { setTimeout: callback => { timers.set(++timerId, callback); return timerId }, clearTimeout: id => timers.delete(id), addEventListener() {}, removeEventListener() {} }
  const read = (type, start, end, options = {}) => {
    requests.push({ type, start, end, force: options.force })
    if (!held[type]) return Promise.resolve(type === 'workshop' ? emptyWorkshop(start, end)
      : { ...reportResult(start, end, 'initial', refreshing), ...(type === 'summary' ? { syncing, missing_day_count: syncing ? 2 : 0 } : {}) })
    return new Promise((resolve, reject) => pending.push({ type, start, end, signal: options.signal, resolve: data => resolve(type === 'range' && kind === 'equipment' ? { task_report: data } : data), reject }))
  }
  const bindings = {
    ...Vue, useRoute, useRouter, defineProps: () => ({ mode, user: { user_id: 'admin-1', role: 'admin' } }), defineEmits: () => (...args) => exports.push(args),
    useWorkshopConfirmation, useWorkshopCorrection,
    listWorkshopRangeTasks: (query, options) => read('workshop', query.startDate, query.endDate, options),
    listWorkshopTasks: (query, options) => read('workshop', query.reportDate, query.reportDate, options),
    generateWorkshopDailyReport: async () => ({}),
    ResizeObserver: class { observe() {} disconnect() {} },
    use() {}, BarChart: {}, LineChart: {}, GridComponent: {}, LegendComponent: {}, TooltipComponent: {}, CanvasRenderer: {},
    useDeviceSwitchQuery() {}, useReportChartTheme: () => ({ darkMode: Vue.ref(false), vars: Vue.ref({}), colors: Vue.ref({}) }),
    getLeadershipReport: (month, options) => read('monthly', `${month}-01`, clock.today, options),
    getLeadershipRange: (start, end, options) => read('range', start, end, options),
    getEnergyRange: async (start, end, options) => held.range ? read('range', start, end, options) : (requests.push({ type: 'range', start, end, force: options?.force }), { task_report: reportResult(start, end) }),
    getLatestWorkshopProductionDate: async () => { requests.push({ type: 'latest' }); return { report_date: clock.latest } },
    getEnergyDay: async () => ({}), getTaskEnergyDay: (date, options) => read('tasks', date, date, options),
    getEnergyMonthlyRange: (start, end, options) => read('summary', start, end, options),
    init: () => ({ setOption: option => chartUpdates.push(option), dispose() {}, resize() {} }), createReportChartGrid: () => ({}),
    reportToday: () => clock.today, recentTwelveMonthStart: () => '2025-11-01',
    formatChineseDate: String, formatChineseMonth: String, formatChineseDateTime: String, formatDeviceDisplay: String,
    sortReportPeriods: rows => rows, taskProductionDate: () => '2026-10-27', deviceSelection: { id: 'Cutter01' },
    mergeOwnedQuery, queryChoice, queryDateRange, queryPositiveInteger, queryText, sameRouteQuery,
  }
  Object.assign(bindings, reportModuleBindings(bindings))
  const source = viewSource(kind === 'leadership' ? 'LeadershipReportView.vue' : kind === 'workshop' ? 'WorkshopView.vue' : 'EnergyPeriodReportView.vue').replace(/^import .*$/gm, '')
  const data = kind === 'leadership' ? 'report' : 'taskDay'
  const load = kind === 'leadership' ? 'loadReport' : 'loadRange'
  const exposed = kind === 'workshop' ? 'data: result, filters, appliedRouteState, loading, error, initializeReport'
    : `data: ${data}, load: ${load}, periodStart, periodEnd, appliedPeriodStart, appliedPeriodEnd, chartType, changeChartType, loading, error, pageActive, dateQueryDirty, exportReport${kind === 'leadership' ? ', refreshLoading, monthlyLoading, retryReport, monthlyPeriodStart, monthlyPeriodEnd, refreshAnalysisReport' : ', monthlyReport, summaryPeriodStart, summaryPeriodEnd, loadDefaultRanges, periodPanel, refreshAnalysisReport'}`
  const setup = scriptFunction(...Object.keys(bindings), `${source}\nreturn { ${exposed} }`)
  const page = { setup() { mounts++; state = setup(...Object.values(bindings)); return () => Vue.h('div') } }
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/report', name, component: page }, { path: '/other', component: { render: () => Vue.h('div') } }] })
  await router.push({ name, query })
  const renderer = Vue.createRenderer({ createComment: () => ({}), createText: () => ({}), createElement: () => ({}), insert() {}, remove() {}, setText() {}, setElementText() {}, parentNode: () => null, nextSibling: () => null, patchProp() {} })
  const app = renderer.createApp({ render: () => Vue.h(RouterView, null, { default: ({ Component }) => Vue.h(Vue.KeepAlive, { max: 2 }, [Component]) }) })
  app.use(router)
  app.mount({})
  let mounted = true
  const unmount = () => { if (mounted) { app.unmount(); mounted = false } }
  t.after(() => { unmount(); if (previousWindow === undefined) delete globalThis.window; else globalThis.window = previousWindow })
  await flushReport()
  return { state, router, pending, requests, held, timers, chartUpdates, exports, clock, unmount, mounts: () => mounts, fire() { const [id, callback] = timers.entries().next().value; timers.delete(id); return callback() } }
}

for (const kind of ['workshop', 'equipment']) {
  test(`${kind}: an empty current day never falls back to the latest historical production date`, async t => {
    const page = await mountReport(t, kind)
    assert.ok(!page.requests.some(request => request.type === 'latest'))
    const read = page.requests.find(request => request.type === (kind === 'workshop' ? 'workshop' : 'tasks'))
    assert.equal(read.start, page.clock.today)
    assert.equal(read.end, page.clock.today)
    const rows = kind === 'workshop' ? page.state.data.value.items : page.state.data.value.records
    assert.deepEqual(rows, [])
  })

  test(`${kind}: changing the default day clears cached historical rows before loading the empty new day`, async t => {
    const page = await mountReport(t, kind)
    if (kind === 'workshop') page.state.data.value.items = [{ task_id: 'previous-day' }]
    else page.state.data.value.records = [{ task_id: 'previous-day' }]
    await page.router.push('/other'); await flushReport()
    page.clock.today = '2026-11-01'
    page.held.workshop = page.held.tasks = page.held.summary = true
    await page.router.push('/report'); await flushReport()
    assert.equal(page.state.loading.value, true)
    if (kind === 'workshop') assert.deepEqual(page.state.data.value.items, [])
    else assert.equal(page.state.data.value, null)
    for (const call of page.pending) call.resolve(call.type === 'workshop'
      ? emptyWorkshop(call.start, call.end) : reportResult(call.start, call.end))
    await flushReport(); await flushReport()
    assert.equal(kind === 'workshop' ? page.state.filters.startDate : page.state.appliedPeriodStart.value, page.clock.today)
    assert.deepEqual(kind === 'workshop' ? page.state.data.value.items : page.state.data.value.records, [])
    assert.ok(!page.requests.some(request => request.type === 'latest'))
  })
}

test('worker task confirmation keeps its existing latest production day selection', async t => {
  const page = await mountReport(t, 'workshop', { mode: 'worker' })
  assert.equal(page.state.filters.reportDate, page.clock.latest)
  assert.ok(page.requests.some(request => request.type === 'latest'))
})

for (const kind of ['leadership', 'equipment']) {
  test(`${kind}: a retained default report follows the current day after month rollover`, async t => {
    const page = await mountReport(t, kind)
    await page.router.push('/other'); await flushReport()
    page.clock.today = '2026-11-01'
    await page.router.push('/report'); await flushReport(); await flushReport()
    assert.equal(page.mounts(), 1)
    assert.equal(page.state.appliedPeriodStart.value, '2026-11-01')
    assert.equal(page.state.appliedPeriodEnd.value, '2026-11-01')
    assert.equal(page.state.data.value.period_end, '2026-11-01')
    if (kind === 'leadership') {
      assert.equal(page.state.monthlyPeriodStart.value, '2025-11-01')
      assert.equal(page.state.monthlyPeriodEnd.value, '2026-11-01')
      assert.equal(page.requests.at(-1).start, '2026-11-01')
    }
  })

  test(`${kind}: returning immediately retains data while a delayed refresh runs and failure keeps that data`, async t => {
    const page = await mountReport(t, kind)
    const { state, router, held, pending } = page
    held.monthly = held.summary = held.tasks = true
    const previous = state.data.value
    await router.push('/other'); await flushReport()
    await router.push('/report'); await flushReport()
    assert.equal(page.mounts(), 1)
    assert.equal(state.data.value, previous, 'cached data remains available before the API returns')
    assert.equal(state.loading.value, true, 'the update still protects export and analysis from stale inputs')
    assert.ok(pending.length)
    pending.forEach(item => item.reject(new Error('后台读取失败')))
    await flushReport(); await flushReport()
    assert.equal(state.data.value, previous)
    assert.equal(state.loading.value, false)
    assert.match(state.error.value, /后台读取失败/)
  })
}

test('leadership analysis refresh keeps the applied range and forces current data reads', async t => {
  const page = await mountReport(t, 'leadership', { query: rangeA })
  await page.state.refreshAnalysisReport()
  const rangeRead = page.requests.findLast(request => request.type === 'range')
  assert.deepEqual(rangeRead, { type: 'range', start: rangeA.start, end: rangeA.end, force: true })
  assert.equal(page.requests.at(-1).force, true)
})

function compiledLayout(name) {
  const descriptor = parse(readFileSync(new URL(`../src/layouts/${name}.vue`, import.meta.url), 'utf8')).descriptor
  const bindings = { RouterView, useRoute, useRouter }
  const script = compileScript(descriptor, { id: `cache-${name}`, inlineTemplate: true }).content
    .replace(/import\s*\{([^}]+)\}\s*from\s*['"]vue['"]/g, (_, names) => {
      for (const name of names.split(',')) {
        const [original, alias = original] = name.trim().split(/\s+as\s+/)
        bindings[alias] = original === 'Transition' ? { inheritAttrs: false, setup: (_props, { slots }) => () => Vue.h(Vue.Fragment, slots.default?.()) } : Vue[original]
      }
      return ''
    }).replace(/^import .*$/gm, '').replace('export default', 'return')
  return scriptFunction(...Object.keys(bindings), script)(...Object.values(bindings))
}

test('admin navigation retains the three report pages across other modules and discards them on device change', async t => {
  const Outlet = compiledLayout('AdminPageView')
  const Reports = compiledLayout('ReportsLayout')
  const device = Vue.ref('Cutter01'), mounted = [], destroyed = [], active = new Set()
  let settingsReads = 0
  const report = kind => Vue.defineComponent({ setup() {
    const identity = `${device.value}:${kind}`
    mounted.push(identity)
    Vue.onActivated(() => active.add(identity))
    Vue.onDeactivated(() => active.delete(identity))
    Vue.onBeforeUnmount(() => { destroyed.push(identity); active.delete(identity) })
    return () => Vue.h('p', identity)
  } })
  const other = { render: () => Vue.h('p', '其他功能') }
  const router = createRouter({ history: createMemoryHistory(), routes: [{
    path: '/admin', component: { setup: () => {
      const route = useRoute()
      return () => Vue.h(Outlet, { ready: true, deviceId: device.value,
        devicePage: route.path.startsWith('/admin/reports') || route.path === '/admin/home', transitionName: '' })
    } }, children: [{ path: 'home', component: other }, { path: 'settings', component: RouterView,
      children: [{ path: 'database', component: { setup() { settingsReads++; return () => Vue.h('p', '数据库设置') } } }],
    }, {
      path: 'reports', component: Reports, children: [...['leadership', 'workshop', 'equipment'].map(kind => ({
        path: kind, name: kind, component: report(kind), meta: { keepAlive: true },
      })), { path: 'center', name: 'center', component: report('center') }],
    }],
  }] })
  const node = () => ({ children: [], parent: null })
  const remove = child => {
    if (child.parent) child.parent.children.splice(child.parent.children.indexOf(child), 1)
    child.parent = null
  }
  const renderer = Vue.createRenderer({ createComment: node, createText: node, createElement: node,
    insert(child, parent, anchor) {
      remove(child)
      const index = anchor ? parent.children.indexOf(anchor) : -1
      parent.children.splice(index < 0 ? parent.children.length : index, 0, child)
      child.parent = parent
    }, remove, setText() {}, setElementText() {}, parentNode: child => child.parent,
    nextSibling: child => child.parent?.children[child.parent.children.indexOf(child) + 1], patchProp() {} })
  await router.push('/admin/reports/workshop')
  const app = renderer.createApp({ render: () => Vue.h(RouterView) }); app.use(router); app.mount(node())
  t.after(() => app.unmount())
  for (const kind of ['workshop', 'equipment', 'leadership']) {
    await router.push(`/admin/reports/${kind}`); await flushReport()
    assert.deepEqual([...active], [`Cutter01:${kind}`])
    await router.push('/admin/home'); await flushReport()
    assert.equal(active.size, 0, 'cached report work is paused outside the report module')
    assert.equal(destroyed.length, 0)
    await router.push(`/admin/reports/${kind}`); await flushReport()
    assert.equal(mounted.filter(item => item === `Cutter01:${kind}`).length, 1)
  }
  await router.push('/admin/reports/center'); await flushReport()
  await router.push('/admin/home'); await flushReport()
  assert.ok(destroyed.includes('Cutter01:center'), 'noncached report jobs leave the page and stop their own work')
  await router.push('/admin/settings/database'); await flushReport()
  assert.equal(settingsReads, 1, 'the inactive reports outlet must not mount a second settings page')
  await router.push('/admin/reports/leadership'); await flushReport()
  device.value = 'Cutter02'
  await flushReport()
  assert.deepEqual([...active], ['Cutter02:leadership'])
  assert.equal(destroyed.filter(item => item.startsWith('Cutter01:') && item !== 'Cutter01:center').length, 3)
  assert.equal(mounted.filter(item => item === 'Cutter02:leadership').length, 1)
})

test('equipment summary: KeepAlive resumes one missing-data poll and completion updates the chart', async t => {
  const page = await mountReport(t, 'equipment', { syncing: true })
  const { state, router, held, pending, timers, chartUpdates } = page
  assert.equal(timers.size, 1)
  await router.push('/other'); await flushReport()
  assert.equal(timers.size, 0)
  await router.push('/report'); await flushReport()
  assert.equal(page.mounts(), 1)
  assert.equal(timers.size, 1)
  state.chartType.value = 'line'; await flushReport()
  assert.equal(timers.size, 1)
  held.summary = true
  state.periodPanel.value = { getChartElement: () => ({}) }
  const poll = page.fire()
  state.chartType.value = 'bar'; await flushReport()
  assert.equal(timers.size, 0, 'a running poll must not create a second timer')
  assert.equal(pending.length, 1)
  pending[0].resolve({ ...reportResult(), id: 'complete', syncing: false, missing_day_count: 0, rows: [{ report_date: '2026-10', electricity: { value: 42 }, gas: { value: 8 } }] })
  await poll; await flushReport()
  assert.equal(state.monthlyReport.value.id, 'complete')
  assert.deepEqual(chartUpdates.at(-1).series[0].data, [42])
  assert.equal(timers.size, 0)
  await router.push('/other'); await router.push('/report'); await flushReport()
  assert.equal(timers.size, 0, 'completed summaries do not resume polling')
})

test('equipment summary: failures retain data and the attempt cap survives reactivation', async t => {
  const page = await mountReport(t, 'equipment', { syncing: true })
  const { state, router, held, pending, timers, requests } = page
  const previous = state.monthlyReport.value
  held.summary = true
  for (let attempt = 0; attempt < 600; attempt++) {
    const poll = page.fire()
    pending.at(-1).reject(new Error('meter unavailable'))
    await poll
  }
  assert.equal(state.monthlyReport.value, previous)
  assert.equal(requests.filter(call => call.type === 'summary' && call.force).length, 600)
  assert.equal(timers.size, 0)
  for (let index = 0; index < 2; index++) { await router.push('/other'); await router.push('/report'); await flushReport() }
  assert.equal(requests.filter(call => call.type === 'summary' && call.force).length, 600)
  assert.equal(timers.size, 0)
  held.summary = false
  state.periodStart.value = '2026-09-15'
  await state.loadDefaultRanges(); await flushReport()
  assert.equal(timers.size, 1, 'a new summary scope has its own replenishment budget')
})

test('equipment summary: a new summary interval and unmount invalidate old responses and timers', async t => {
  const page = await mountReport(t, 'equipment', { syncing: true })
  const { state, held, pending, timers } = page
  held.summary = true
  const poll = page.fire()
  state.periodStart.value = '2026-09-15'
  const load = state.loadDefaultRanges()
  await flushReport()
  assert.equal(pending[0].signal.aborted, true)
  for (const call of pending.slice(1)) call.resolve({ ...reportResult(call.start, call.end, 'new-scope'), syncing: true, missing_day_count: 1 })
  await load; await flushReport()
  pending[0].resolve({ ...reportResult(), id: 'obsolete-scope', syncing: false })
  await poll
  assert.equal(state.monthlyReport.value.id, 'new-scope')
  assert.equal(state.summaryPeriodEnd.value, '2026-09-15')
  assert.equal(timers.size, 1)
  const next = page.fire()
  const last = pending.at(-1)
  page.unmount()
  assert.equal(last.signal.aborted, true)
  last.resolve({ ...reportResult(), id: 'after-unmount' }); await next
  assert.equal(timers.size, 0)
  assert.equal(state.monthlyReport.value.id, 'new-scope')
})

for (const kind of ['leadership', 'equipment']) {
  test(`${kind}: date/chart history restores without writing old applied dates, including late responses`, async t => {
    const page = await mountReport(t, kind, { query: { ...rangeA, chart: 'bar' } })
    const { state, router, held, pending } = page
    assert.equal(state.appliedPeriodStart.value, rangeA.start, 'deep-link refresh applies the requested range')
    held.range = true
    await router.push({ query: { ...rangeB, chart: 'line' } })
    await flushReport()
    assert.equal(pending.length, 1)
    assert.equal(state.loading.value, true)
    assert.deepEqual(router.currentRoute.value.query, { ...rangeB, chart: 'line' })
    assert.equal(state.chartType.value, 'line')
    assert.equal(state.appliedPeriodStart.value, rangeA.start, 'applied data stays separate while waiting')
    router.back()
    await flushReport()
    assert.equal(pending[0].signal.aborted, true)
    assert.equal(state.loading.value, false)
    assert.equal(router.currentRoute.value.query.start, rangeA.start)
    pending[0].resolve(reportResult(rangeB.start, rangeB.end, 'obsolete'))
    await flushReport()
    assert.notEqual(state.data.value.id, 'obsolete')
    assert.equal(state.appliedPeriodStart.value, rangeA.start)
    router.forward()
    await flushReport()
    assert.equal(pending.length, 2)
    pending[1].resolve(reportResult(rangeB.start, rangeB.end, 'latest'))
    await flushReport()
    assert.equal(state.data.value.id, 'latest')
    assert.equal(state.appliedPeriodStart.value, rangeB.start)
    assert.deepEqual(router.currentRoute.value.query, { ...rangeB, chart: 'line' })
    await router.push({ query: { ...rangeB, chart: 'bar' } })
    await flushReport()
    assert.equal(pending.length, 2, 'chart-only history does not repeat the data query')
    assert.equal(state.chartType.value, 'bar')
    state.periodStart.value = '2026-09-10'
    await state.changeChartType('line')
    await flushReport()
    assert.equal(router.currentRoute.value.query.start, rangeB.start, 'chart selection must not apply the date draft')
    assert.equal(pending.length, 2)
    assert.equal(state.periodStart.value, '2026-09-10', 'chart changes preserve the unsubmitted date draft')
    assert.equal(state.dateQueryDirty.value, true)
    await router.push('/other')
    await flushReport()
    assert.equal(state.pageActive.value, false)
    await router.push({ name: router.getRoutes().find(route => route.path === '/report').name, query: { ...rangeA, chart: 'line' } })
    await flushReport()
    assert.equal(page.mounts(), 1, 'returning reactivates the cached page')
    assert.equal(pending.length, 3, 'date-only restoration loads its target range')
    pending[2].resolve(reportResult(rangeA.start, rangeA.end, 'reactivated'))
    await flushReport()
    assert.equal(state.data.value.id, 'reactivated')
    assert.equal(state.loading.value, false)
    assert.equal(router.currentRoute.value.query.start, rangeA.start)
  })

  test(`${kind}: a newer target wins when two unresolved ranges complete in reverse order`, async t => {
    const { state, router, held, pending } = await mountReport(t, kind, { query: rangeA })
    held.range = true
    await router.push({ query: rangeB })
    await flushReport()
    const rangeC = { start: '2026-09-05', end: '2026-09-06' }
    await router.push({ query: { ...rangeC, chart: 'line' } })
    await flushReport()
    assert.equal(pending.length, 2)
    assert.equal(pending[0].signal.aborted, true)
    pending[1].resolve(reportResult(rangeC.start, rangeC.end, 'new'))
    await flushReport()
    pending[0].resolve(reportResult(rangeB.start, rangeB.end, 'old'))
    await flushReport()
    assert.equal(state.data.value.id, 'new')
    assert.equal(state.appliedPeriodStart.value, rangeC.start)
    assert.equal(router.currentRoute.value.query.start, rangeC.start)
    assert.equal(state.loading.value, false)
  })
}

test('leadership: cancelled snapshot timers cannot take over a foreground query, even for the same dates', async t => {
  const page = await mountReport(t, 'leadership', { refreshing: true })
  const { state, held, pending, timers } = page
  assert.equal(timers.size, 1)
  const oldTimer = timers.values().next().value
  held.range = true
  const query = state.load()
  assert.equal(timers.size, 0)
  await oldTimer()
  assert.equal(pending.length, 1, 'an already-queued old timer cannot start another request')
  assert.equal(pending[0].signal.aborted, false)
  pending[0].resolve(reportResult(state.periodStart.value, state.periodEnd.value, 'user'))
  await query
  await flushReport()
  assert.equal(state.data.value.id, 'user')
  assert.equal(state.loading.value, false)
  assert.equal(state.refreshLoading.value, false)
})

test('leadership: an in-flight poll cannot overwrite a newer user result or its loading state', async t => {
  const page = await mountReport(t, 'leadership', { refreshing: true })
  const { state, held, pending } = page
  held.monthly = true
  const poll = page.fire()
  assert.equal(pending.length, 1)
  held.range = true
  state.periodStart.value = rangeB.start
  state.periodEnd.value = rangeB.end
  const query = state.load()
  assert.equal(pending[0].signal.aborted, true)
  pending[1].resolve(reportResult(rangeB.start, rangeB.end, 'user'))
  await query
  pending[0].resolve(reportResult(undefined, undefined, 'old-poll', true))
  await poll
  await flushReport()
  assert.equal(state.data.value.id, 'user')
  assert.equal(state.loading.value, false)
  assert.equal(state.monthlyLoading.value, false)
  assert.equal(state.refreshLoading.value, false)
})

test('leadership: poll failures retain retry feedback and deactivation clears pending work', async t => {
  const page = await mountReport(t, 'leadership', { refreshing: true })
  const { state, held, pending, timers, router } = page
  held.monthly = true
  const poll = page.fire()
  pending[0].reject(new Error('refresh unavailable'))
  await poll
  assert.equal(state.error.value, 'refresh unavailable')
  assert.equal(state.refreshLoading.value, false)
  held.range = true
  const retry = state.retryReport()
  pending[1].resolve(reportResult(state.periodStart.value, state.periodEnd.value, 'retry', true))
  await retry
  await flushReport()
  assert.equal(state.error.value, '')
  assert.equal(timers.size, 1)
  const staleTimer = timers.values().next().value
  await router.push('/other')
  await flushReport()
  assert.equal(timers.size, 0)
  await staleTimer()
  assert.equal(pending.length, 2)
  assert.equal(state.loading.value, false)
})

for (const kind of ['leadership', 'equipment']) test(`${kind}: chart changes during a submitted query keep that query's dates`, async t => {
  const { state, held, pending, router } = await mountReport(t, kind, { query: rangeA })
  held.range = true
  state.periodStart.value = rangeB.start
  state.periodEnd.value = rangeB.end
  const old = state.load()
  await state.changeChartType('line')
  await flushReport()
  assert.deepEqual(router.currentRoute.value.query, { ...rangeB, chart: 'line' })
  assert.equal(pending[0].signal.aborted, true)
  pending[1].resolve(reportResult(rangeB.start, rangeB.end, 'with-chart'))
  await flushReport()
  pending[0].resolve(reportResult(rangeB.start, rangeB.end, 'before-chart'))
  await old
  assert.equal(state.data.value.id, 'with-chart')
  assert.equal(state.loading.value, false)
})

test('leadership: unmount aborts the active poll and rejects its late result', async t => {
  const page = await mountReport(t, 'leadership', { refreshing: true })
  page.held.monthly = true
  const poll = page.fire()
  page.unmount()
  assert.equal(page.pending[0].signal.aborted, true)
  assert.equal(page.timers.size, 0)
  page.pending[0].resolve(reportResult(undefined, undefined, 'after-unmount', true))
  await poll
  assert.notEqual(page.state.data.value.id, 'after-unmount')
  assert.equal(page.state.loading.value, false)
  assert.equal(page.state.refreshLoading.value, false)
})

test('leadership export retains the applied range, monthly summary and chosen chart despite edited draft dates', async t => {
  const page = await mountReport(t, 'leadership', { query: rangeA })
  const { state } = page
  state.periodStart.value = '2026-09-15'
  state.periodEnd.value = '2026-09-16'
  await state.changeChartType('area')
  await flushReport()
  state.exportReport('pdf')
  assert.deepEqual(page.exports.at(-1), ['export', {
    report_type: 'leadership', report_date: rangeA.start, period_start: rangeA.start, period_end: rangeA.end,
    project_code: null, ship_no: null,
    parameters: { chart_type: 'area', summary_period_start: '2025-11-01', summary_period_end: '2026-10-27' },
  }, 'pdf'])
  assert.equal(state.dateQueryDirty.value, true)
})


test('equipment first paint accepts task and monthly results independently of a slow peer', async t => {
  const page = await mountReport(t, 'equipment', { hold: { tasks: true } })
  assert.equal(page.state.data.value, null)
  assert.ok(page.state.monthlyReport.value)
  assert.equal(page.state.loading.value, true)
  page.pending[0].resolve(reportResult('2026-10-27', '2026-10-27', 'task-ready'))
  await flushReport()
  assert.equal(page.state.data.value.id, 'task-ready')
  assert.equal(page.state.loading.value, false)
})

test('equipment range/export and analysis refresh retain the independent monthly scope and applied dates', async t => {
  const page = await mountReport(t, 'equipment')
  const { state, requests } = page
  const summaryReads = requests.filter(call => call.type === 'summary').length
  state.periodStart.value = '2026-10-01'; state.periodEnd.value = '2026-10-15'
  await state.load(); await flushReport()
  assert.equal(requests.filter(call => call.type === 'summary').length, summaryReads)
  assert.equal(state.summaryPeriodStart.value, '2025-11-01')
  assert.equal(state.summaryPeriodEnd.value, '2026-10-27')
  await state.changeChartType('line'); await flushReport()
  state.periodStart.value = '2026-10-20'
  state.exportReport('pdf')
  assert.deepEqual(page.exports.at(-1), ['export', {
    report_type: 'equipment', report_date: '2026-10-01', period_start: '2026-10-01', period_end: '2026-10-15', project_code: null, ship_no: null,
    parameters: { chart_type: 'line', summary_period_start: '2025-11-01', summary_period_end: '2026-10-27' },
  }, 'pdf'])
  assert.equal(state.dateQueryDirty.value, true)
  requests.length = 0
  await state.refreshAnalysisReport(); await flushReport()
  assert.deepEqual(requests.map(call => [call.type, call.start, call.end, call.force]), [
    ['range', '2026-10-01', '2026-10-15', true],
    ['summary', '2025-11-01', '2026-10-27', true],
  ])
  assert.equal(state.appliedPeriodStart.value, '2026-10-01')
})
