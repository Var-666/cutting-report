import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'

const source = readFileSync(new URL('../src/components/ReportJobStatus.vue', import.meta.url), 'utf8')

test('report job status gives unavailable AI output a user-facing label', () => {
  assert.match(source, /unavailable: '暂不可用'/)
  assert.match(source, /aiStatusText\[job\.ai_status \|\| ''\]/)
})

import * as Vue from 'vue'
import { parse } from '@vue/compiler-sfc'
import { scriptFunction } from './typescript-helpers.mjs'
import { mergeOwnedQuery, queryChoice, queryPositiveInteger, sameRouteQuery } from '../src/utils/reportRouteState.ts'

const flush = async () => { await new Promise(resolve => setImmediate(resolve)); await Vue.nextTick() }
function mountSource(t, file, bindings, exports) {
  const code = parse(readFileSync(new URL(`../src/${file}.vue`, import.meta.url), 'utf8')).descriptor.scriptSetup.content.replace(/^import .*$/gm, '')
  const values = { ...Vue, ...bindings }
  const setup = scriptFunction(...Object.keys(values), `${code}; return { ${exports} }`)
  let state
  const renderer = Vue.createRenderer({ createElement: () => ({}), createText: () => ({}), createComment: () => ({}), insert() {}, remove() {}, setText() {}, setElementText() {}, parentNode: () => null, nextSibling: () => null, patchProp() {} })
  const app = renderer.createApp({ setup() { state = setup(...Object.values(values)); return () => null } })
  app.mount({})
  let mounted = true
  const unmount = () => { if (mounted) { mounted = false; app.unmount() } }
  t.after(unmount)
  return { state, unmount }
}
function mountOptions(t) {
  const calls = []
  const pending = (type, date, project, options) => new Promise((resolve, reject) => calls.push({ type, date, project, signal: options.signal, resolve: items => resolve({ items }), reject }))
  const page = mountSource(t, 'views/ReportCenterView', {
    window: { clearTimeout() {} },
    defineProps() {}, useRoute: () => ({ query: {} }), useRouter: () => ({ push: async () => {} }),
    mergeOwnedQuery, queryChoice, queryPositiveInteger, sameRouteQuery,
    listProjects: (date, options) => pending('projects', date, '', options),
    listShips: (date, project, options) => pending('ships', date, project, options),
    listReports: async () => ({ items: [], page: 1, page_size: 10, total: 0 }), listPublishedDefinitions: async () => ({ items: [] }),
  }, 'projects, ships, projectsLoading, shipsLoading, loadProjects, loadShips, error')
  return { ...page, calls }
}

test('report options: date B wins over date A even when cancellation is ignored', async t => {
  const { state, calls } = mountOptions(t)
  const first = state.loadProjects('2026-09-01')
  const next = state.loadProjects('2026-09-02')
  assert.deepEqual(calls.map(call => [call.type, call.date]), [['ships', '2026-09-01'], ['projects', '2026-09-01'], ['ships', '2026-09-02'], ['projects', '2026-09-02']])
  assert.ok(calls.slice(0, 2).every(call => call.signal.aborted))
  calls[2].resolve(['B-ship']); calls[3].resolve(['B-project'])
  await next
  calls[0].resolve(['A-ship']); calls[1].resolve(['A-project'])
  await first
  assert.deepEqual(state.projects.value, ['B-project'])
  assert.deepEqual(state.ships.value, ['B-ship'])
  assert.equal(state.projectsLoading.value, false)
  assert.equal(state.shipsLoading.value, false)
})

test('report options: an all-project ship read and project A cannot overwrite project B', async t => {
  const { state, calls } = mountOptions(t)
  const date = '2026-09-01'
  const initial = state.loadProjects(date)
  calls[1].resolve(['A', 'B'])
  await flush()
  const first = state.loadShips({ reportDate: date, projectCode: 'A' })
  const next = state.loadShips({ reportDate: date, projectCode: 'B' })
  assert.deepEqual(state.ships.value, [])
  calls[3].resolve(['B-ship']); await next
  calls[2].reject(new Error('old project failure')); await first
  calls[0].resolve(['all-ships']); await initial
  assert.deepEqual(state.ships.value, ['B-ship'])
  assert.equal(state.error.value, '')
})

test('report options: latest failures retain no stale choices and late failures stay invisible', async t => {
  const { state, calls } = mountOptions(t)
  const first = state.loadProjects('2026-09-01')
  calls[0].resolve(['old-ship']); calls[1].resolve(['old-project']); await first
  const next = state.loadProjects('2026-09-02')
  assert.deepEqual(state.projects.value, [])
  assert.deepEqual(state.ships.value, [])
  calls[2].reject(new Error('ship unavailable')); calls[3].reject(new Error('project unavailable')); await next
  assert.match(state.error.value, /unavailable/)
  assert.deepEqual(state.projects.value, [])
  assert.deepEqual(state.ships.value, [])
  const retry = state.loadProjects('2026-09-03')
  calls[4].resolve(['new-ship']); calls[5].resolve(['new-project']); await retry
  assert.equal(state.error.value, '')
})

test('report options: unmount aborts both option requests and ignores late results', async t => {
  const { state, calls, unmount } = mountOptions(t)
  const pending = state.loadProjects('2026-09-01')
  unmount()
  assert.ok(calls.every(call => call.signal.aborted))
  calls.forEach(call => call.resolve(['late']))
  await pending
  assert.deepEqual(state.projects.value, [])
  assert.deepEqual(state.ships.value, [])
})

test('report form: changing date or month clears dependent choices once, while submission uses the current date', async t => {
  const events = []
  const props = Vue.reactive({ initialReportType: 'workshop', projects: ['B'], ships: ['B-ship'], customDefinitions: [], loading: false })
  const { state } = mountSource(t, 'components/ReportForm', { defineProps: () => props, defineEmits: () => (name, value) => events.push([name, value]) }, 'form, submit')
  state.form.report_date = '2026-09-01'
  state.form.project_code = 'A'
  state.form.ship_no = 'A-ship'
  events.length = 0
  state.form.report_date = '2026-09-02'
  await flush()
  assert.equal(state.form.project_code, '')
  assert.equal(state.form.ship_no, '')
  assert.deepEqual(events, [['date-change', '2026-09-02']])
  state.form.project_code = 'B'
  assert.equal(state.form.ship_no, '')
  state.form.ship_no = 'B-ship'
  state.submit()
  assert.deepEqual(events.at(-1)[1], { report_type: 'workshop', definition_id: null, report_date: '2026-09-02', project_code: 'B', ship_no: 'B-ship', parameters: { chart_type: 'bar_line' } })
  state.form.report_type = 'leadership'
  state.form.report_month = '2026-08'
  await flush()
  assert.deepEqual(events.at(-1), ['date-change', '2026-08-01'])
  state.submit()
  assert.equal(events.at(-1)[1].report_date, '2026-08-01')
})

test('report option APIs retain device parameters and forward optional cancellation', async t => {
  const { setDeviceCatalog, resetDeviceContext } = await import('../src/api/deviceContext.ts')
  const { listProjects, listShips } = await import('../src/api/cutting.ts')
  const calls = []
  setDeviceCatalog({ default_device_id: 'Cutter02', items: [{ id: 'Cutter02' }] })
  t.after(resetDeviceContext)
  t.mock.method(globalThis, 'fetch', async (url, options) => { calls.push({ url: new URL(url, 'http://test'), options }); return Response.json({ success: true, data: { items: [] } }) })
  const controller = new AbortController()
  await listProjects('2026-09-01', { signal: controller.signal })
  await listShips('2026-09-01', 'B', { signal: controller.signal })
  assert.ok(calls.every(call => call.url.searchParams.get('device_id') === 'Cutter02' && call.options.signal === controller.signal))
  assert.equal(calls[1].url.searchParams.get('project_code'), 'B')
})
