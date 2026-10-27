import * as Vue from 'vue'
import { createRouter, createMemoryHistory, RouterView, useRoute, useRouter } from 'vue-router'
import { scriptFunction } from './typescript-helpers.mjs'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { parse } from '@vue/compiler-sfc'
import {
  mergeOwnedQuery,
  queryChoice,
  queryDate,
  queryDateRange,
  queryPositiveInteger,
  sameRouteQuery,
} from '../src/utils/reportRouteState.ts'

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

  assert.match(leadership, /if \(syncRoute\) await syncRouteState\(\)/)
  assert.match(leadership, /watch\(\(\) => route\.query/)
  assert.match(equipment, /queryDateRange\(route\.query, \{ maxDays: 31 \}\)/)
  assert.match(equipment, /if \(syncRoute\) await syncRouteState\(\)/)

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
  assert.match(query.template.content, /条件已修改，尚未更新报表/)
  assert.match(leadership, /periodStart\.value !== appliedPeriodStart\.value \|\| periodEnd\.value !== appliedPeriodEnd\.value/)
  assert.match(equipment, /periodStart\.value !== appliedPeriodStart\.value \|\| periodEnd\.value !== appliedPeriodEnd\.value/)
  assert.match(workshop, /filters\.startDate !== appliedRouteState\.startDate \|\| filters\.endDate !== appliedRouteState\.endDate/)
  for (const name of ['LeadershipReportView.vue', 'EnergyPeriodReportView.vue', 'WorkshopView.vue']) {
    const source = readFileSync(new URL(`../src/views/${name}`, import.meta.url), 'utf8')
    assert.match(source, /:date-dirty="dateQueryDirty"/)
  }
})

const flushReport = async () => { await new Promise(resolve => setImmediate(resolve)); await Vue.nextTick() }
const rangeA = { start: '2026-09-01', end: '2026-09-02' }
const rangeB = { start: '2026-09-03', end: '2026-09-04' }
function reportResult(start = '2026-10-01', end = '2026-10-27', id = 'initial', refreshing = false) {
  return { id, period_start: start, period_end: end, snapshot: { data_as_of: '2026-10-27', refreshing }, daily_records: [], monthly_records: [], rows: [], records: [], summary: {}, syncing: false }
}

// Run the page's real setup/watch/lifecycle code with memory-history navigation.
async function mountReport(t, kind, { query = {}, refreshing = false } = {}) {
  const name = kind === 'leadership' ? 'leadership-report' : 'equipment-report'
  const pending = []
  const timers = new Map()
  const held = { range: false, monthly: false }
  let timerId = 0
  let state
  let mounts = 0
  const previousWindow = globalThis.window
  globalThis.window = { setTimeout: callback => { timers.set(++timerId, callback); return timerId }, clearTimeout: id => timers.delete(id), addEventListener() {}, removeEventListener() {} }
  const read = (type, start, end, options = {}) => {
    if (!held[type]) return Promise.resolve(reportResult(start, end, 'initial', refreshing))
    return new Promise((resolve, reject) => pending.push({ type, start, end, signal: options.signal, resolve: data => resolve(type === 'range' && kind === 'equipment' ? { task_report: data } : data), reject }))
  }
  const bindings = {
    ...Vue, useRoute, useRouter, defineProps: () => ({}), defineEmits: () => () => {},
    use() {}, BarChart: {}, LineChart: {}, GridComponent: {}, LegendComponent: {}, TooltipComponent: {}, CanvasRenderer: {},
    useDeviceSwitchQuery() {}, useReportChartTheme: () => ({ darkMode: Vue.ref(false), vars: Vue.ref({}), colors: Vue.ref({}) }),
    getLeadershipReport: (_month, options) => read('monthly', '2026-10-01', '2026-10-27', options),
    getLeadershipRange: (start, end, options) => read('range', start, end, options),
    getEnergyRange: async (start, end, options) => held.range ? read('range', start, end, options) : { task_report: reportResult(start, end) },
    getLatestWorkshopProductionDate: async () => ({ report_date: '2026-10-27' }),
    getEnergyDay: async () => ({}), getTaskEnergyDay: async () => reportResult('2026-10-27', '2026-10-27'),
    getEnergyMonthlyRange: async () => reportResult(),
    reportToday: () => '2026-10-27', recentTwelveMonthStart: () => '2025-11-01',
    formatChineseDate: String, formatChineseMonth: String, formatChineseDateTime: String, formatDeviceDisplay: String,
    sortReportPeriods: rows => rows, taskProductionDate: () => '2026-10-27', deviceSelection: { id: 'Cutter01' },
    mergeOwnedQuery, queryChoice, queryDateRange, sameRouteQuery,
  }
  const source = viewSource(kind === 'leadership' ? 'LeadershipReportView.vue' : 'EnergyPeriodReportView.vue').replace(/^import .*$/gm, '')
  const data = kind === 'leadership' ? 'report' : 'taskDay'
  const load = kind === 'leadership' ? 'loadReport' : 'loadRange'
  const setup = scriptFunction(...Object.keys(bindings), `${source}\nreturn { data: ${data}, load: ${load}, periodStart, periodEnd, appliedPeriodStart, appliedPeriodEnd, chartType, changeChartType, loading, error, pageActive, dateQueryDirty${kind === 'leadership' ? ', refreshLoading, monthlyLoading, retryReport' : ''} }`)
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
  return { state, router, pending, held, timers, unmount, mounts: () => mounts, fire() { const [id, callback] = timers.entries().next().value; timers.delete(id); return callback() } }
}

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
