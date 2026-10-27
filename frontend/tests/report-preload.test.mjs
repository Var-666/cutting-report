import { scriptFunction } from './typescript-helpers.mjs'
import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'
import { readFileSync } from 'node:fs'
import { nextTick, ref, watch } from 'vue'
import { startReportPreload, stopReportPreload, reportPreload } from '../src/state/reportPreload.ts'
import { recentTwelveMonthStart, reportToday } from '../src/api/reportDates.ts'
import * as api from '../src/api/reports.ts'
import { session, loadSession, completeAuthentication, endSession } from '../src/state/session.ts'
import { resetDeviceContext, setDeviceCatalog, selectDevice, getDeviceId } from '../src/api/deviceContext.ts'
import { deviceSelection } from '../src/state/devices.ts'

const originalFetch = globalThis.fetch
const admin = { user_id: 'admin-1', role: 'admin' }
const today = reportToday()
const month = today.slice(0, 7)
const response = (data) => ({ ok: true, json: async () => ({ success: true, data }) })
function mockApi(override = () => undefined) {
  const calls = []
  if (!getDeviceId()) setDeviceCatalog({ default_device_id: 'Cutter01', items: [{ id: 'Cutter01' }] })
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options })
    const custom = override(url, options)
    if (custom !== undefined) return custom
    if (url.includes('latest-date')) return response({ report_date: today })
    if (url === '/api/v1/auth/status') return response({ user: admin, setup_required: false })
    if (url === '/api/v1/devices' || url === '/api/v1/production-lines') return response({ default_device_id: 'Cutter01', items: [{ id: 'Cutter01' }] })
    return response({ report_date: today, items: [], total: 0 })
  }
  return calls
}
afterEach(() => { stopReportPreload(); resetDeviceContext(); globalThis.fetch = originalFetch })

test('worker load syncs a nonempty snapshot while background polling remains read-only', async () => {
  const source = readFileSync(new URL('../src/views/WorkshopView.vue', import.meta.url), 'utf8')
  const loadSource = source.slice(source.indexOf('async function load('), source.indexOf('\nfunction refreshAnalysisReport('))
  const signal = new AbortController().signal
  const calls = []
  const rows = (count) => ({ report: { item_count: count }, items: [], page: 1, page_size: 10 })
  const bindings = {
    isWorker: ref(true), filters: { reportDate: today }, props: { mode: 'worker' },
    beginRequest: () => ({ sequence: 1, signal }), requestSequence: 1, requestController: null,
    pageActive: ref(true), loading: ref(false), error: ref(''), result: ref(rows(1)),
    listWorkshopTasks: async (_query, options) => { calls.push(['read', options]); return rows(calls.length > 1 ? 2 : 1) },
    generateWorkshopDailyReport: async (_date, options) => { calls.push(['sync', options]); return rows(2) },
    reconcileVisibleTaskSelection: () => {},
  }
  const load = scriptFunction(...Object.keys(bindings), `${loadSource}; return load`)(...Object.values(bindings))
  await load()
  assert.deepEqual(calls.map(([kind]) => kind), ['read', 'sync', 'read'])
  assert.equal(calls[1][1].signal, signal)
  assert.equal(bindings.result.value.report.item_count, 2)
  calls.length = 0
  await load(1, { silent: true, ensureReport: false, force: true })
  assert.deepEqual(calls.map(([kind]) => kind), ['read'])
})

test('snapshot synchronization forwards cancellation and invalidates the task cache', async () => {
  const calls = mockApi()
  await api.listWorkshopTasks({ reportDate: today })
  const controller = new AbortController()
  await api.generateWorkshopDailyReport(today, { signal: controller.signal })
  await api.listWorkshopTasks({ reportDate: today })
  const sync = calls.find((call) => call.url === '/api/v1/workshop/reports?device_id=Cutter01')
  assert.equal(sync.options.signal, controller.signal)
  assert.equal(sync.options.method, 'POST')
  assert.equal(calls.filter((call) => call.url.includes('/workshop/tasks?')).length, 2)
})

test('admin home warms only its own overview and history', async () => {
  const calls = mockApi()
  const completion = startReportPreload(admin, { pathname: '/admin/home' })
  await completion
  assert.equal(calls.length, 2)
  await Promise.all([api.getLeadershipReport(month), api.listReports({ page: 1, pageSize: 10 })])
  await Promise.all([api.getEnergyDay(today), api.getTaskEnergyDay(today)])
  await api.getEnergyMonthlyRange(recentTwelveMonthStart(today), today)
  await api.listWorkshopRangeTasks({ startDate: today, endDate: today })
  assert.equal(calls.length, 6)
  assert.equal(new Set(calls.map((c) => c.url)).size, 6)
  assert.equal(calls.filter((c) => c.url.includes('/energy-report/monthly-range?')).length, 1)
  assert.equal(calls.filter((c) => c.url.includes('/energy-report/range?')).length, 0)
  assert.equal(calls.filter((c) => c.url.includes('/workshop/range?')).length, 1)
  assert.ok(calls.every((c) => !c.options.method || c.options.method === 'GET'))
  assert.ok(calls.every((c) => !/cutting|analysis|confirm/.test(c.url.split('?')[0])))
  assert.ok(reportPreload.items.every((i) => i.status === 'ready'))
  assert.equal(startReportPreload(admin), completion)
})

test('worker preloads only workshop and uses worker sorting', async () => {
  const calls = mockApi()
  await startReportPreload({ user_id: 'worker-1', role: 'worker' })
  assert.equal(calls.length, 2)
  assert.ok(calls.every((c) => c.url.startsWith('/api/v1/workshop/')))
  assert.ok(calls.some((c) => c.url.includes('sort_mode=worker')))
})

test('one unavailable report does not stop others; failed pages can retry', async () => {
  let fail = true
  mockApi((url) => {
    if (fail && url.includes('leadership-report')) return Promise.reject(new Error('offline'))
  })
  await startReportPreload(admin, { pathname: '/admin/home' })
  assert.equal(reportPreload.items.find((i) => i.key === 'leadership').status, 'failed')
  assert.equal(reportPreload.items.filter((i) => i.status === 'ready').length, 1)
  fail = false
  assert.ok(await api.getLeadershipReport(month))
})

test('logout during latest-date request prevents follow-on warmup even if fetch ignores abort', async () => {
  let finish
  const calls = mockApi((url) => {
    if (url.includes('latest-date')) return new Promise((done) => { finish = done })
  })
  const pending = startReportPreload({ user_id: 'worker-1', role: 'worker' })
  await new Promise((done) => setImmediate(done))
  stopReportPreload()
  finish(response({ report_date: today }))
  await pending
  assert.equal(calls.filter((call) => call.url.includes('latest-date')).length, 1)
  assert.equal(calls.filter((call) => call.url.includes('/requests/') && call.url.endsWith('/cancel')).length, 1)
  assert.deepEqual(reportPreload.items, [])
})

test('different accounts do not reuse the previous account report cache', async () => {
  const calls = mockApi()
  await startReportPreload(admin, { pathname: '/admin/home' })
  await startReportPreload({ ...admin, user_id: 'admin-2' }, { pathname: '/admin/home' })
  assert.equal(calls.filter((c) => c.url.includes('leadership-report')).length, 2)
})

test('forced leadership reads bypass preload cache; daily snapshots invalidate monthly totals', async () => {
  const calls = mockApi()
  await startReportPreload(admin, { pathname: '/admin/home' })
  await api.getLeadershipReport(month, { force: true })
  await api.getLeadershipReport(month)
  await api.getEnergyMonth(month)
  await api.getEnergyDay(today, { force: true })
  await api.getEnergyMonth(month)
  assert.equal(calls.filter((c) => c.url.includes('leadership-report')).length, 2)
  assert.ok(calls.filter((c) => c.url.includes('leadership-report')).every((c) => (c.options.method || 'GET') === 'GET'))
  assert.equal(calls.filter((c) => c.url.includes('energy-report/month?')).length, 2)
  assert.equal(calls.filter((c) => c.url.includes('refresh=true')).length, 1)
})

test('a forced energy range refresh replaces the normal cached view', async () => {
  let version = 0
  const calls = mockApi((url) => {
    if (url.includes('/energy-report/range?')) return response({ version: ++version })
  })

  const first = await api.getEnergyRange(today, today)
  const refreshed = await api.getEnergyRange(today, today, { force: true })
  const reused = await api.getEnergyRange(today, today)

  assert.equal(first.version, 1)
  assert.equal(refreshed.version, 2)
  assert.equal(reused.version, 2)
  assert.equal(calls.filter((c) => c.url.includes('/energy-report/range?')).length, 2)
})

test('confirmation, revocation and correction invalidate cached workshop rows', async () => {
  const calls = mockApi()
  await startReportPreload({ user_id: 'worker-1', role: 'worker' })
  await api.confirmWorkshopTasks(['task-1'], today, { 'task-1': 1 })
  await api.listWorkshopTasks({ reportDate: today, sortMode: 'worker' })
  await api.revokeWorkshopTask('task-1', today, '误确认')
  await api.listWorkshopTasks({ reportDate: today, sortMode: 'worker' })
  await api.correctWorkshopTask('task-1', { reportDate: today, action: 'reassign', workerId: 'worker-2', reason: '现场更正' })
  await api.listWorkshopTasks({ reportDate: today, sortMode: 'worker' })
  assert.equal(calls.filter((c) => c.url.includes('/workshop/tasks?')).length, 4)
  const revoke = calls.find((c) => c.url.includes('/workshop/tasks/task-1/revoke?'))
  const correction = calls.find((c) => c.url.includes('/workshop/tasks/task-1/correct?'))
  assert.equal(JSON.parse(revoke.options.body).reason, '误确认')
  assert.equal(JSON.parse(correction.options.body).worker_id, 'worker-2')
})

test('login and restored sessions only warm the active route without waiting for health', async () => {
  let finishHealth
  const calls = mockApi((url) => {
    if (url.includes('/health')) return new Promise((done) => { finishHealth = done })
  })
  await completeAuthentication(admin)
  assert.equal(session.user.user_id, admin.user_id)
  assert.equal(session.ready, true)
  assert.equal(session.systemInfo, null)
  await startReportPreload(admin)
  assert.ok(calls.some((c) => c.url.includes('leadership-report')))
  assert.ok(calls.every((c) => !c.url.includes('energy-report')))
  await endSession()
  finishHealth(response({ version: 'stale' }))
  await new Promise((done) => setImmediate(done))
  assert.equal(session.systemInfo, null)
  assert.equal(reportPreload.identity, '')
  await loadSession({ force: true })
  assert.equal(session.user.user_id, admin.user_id)
  await new Promise((done) => setImmediate(done))
  assert.equal(reportPreload.identity, `admin:${admin.user_id}`)
  await startReportPreload(admin)
  finishHealth(response({ version: 'current' }))
  await new Promise((done) => setImmediate(done))
  assert.equal(session.systemInfo.version, 'current')
})

test('session restoration validates the saved device before any report warmup', async () => {
  setDeviceCatalog({ default_device_id: 'Cutter01', items: [{ id: 'Cutter01' }, { id: 'Cutter02' }] })
  selectDevice('Cutter02')
  let finishCatalog
  const calls = mockApi((url) => {
    if (url === '/api/v1/production-lines') return new Promise((done) => { finishCatalog = done })
  })
  await loadSession({ force: true })
  assert.equal(session.ready, true)
  assert.equal(reportPreload.identity, '')
  assert.ok(calls.every((call) => !/leadership-report|\/reports\?/.test(call.url)))
  finishCatalog(response({ default_device_id: 'Cutter01', items: [{ id: 'Cutter01' }, { id: 'Cutter02' }] }))
  await new Promise((done) => setImmediate(done))
  await startReportPreload(admin)
  const reports = calls.filter((call) => /leadership-report|\/reports\?/.test(call.url))
  assert.equal(reports.length, 2)
  assert.ok(reports.every((call) => new URL(call.url, 'http://test').searchParams.get('device_id') === 'Cutter02'))
})

test('restored-session preload shares home queries already started when the device becomes ready', async () => {
  let finishCatalog
  const pending = []
  const calls = mockApi((url, options) => {
    if (url === '/api/v1/production-lines') return new Promise((done) => { finishCatalog = done })
    if (/leadership-report|\/reports\?|energy-report\/month\?/.test(url)) {
      return new Promise((resolve, reject) => {
        pending.push({ url, resolve, signal: options.signal })
        options.signal.addEventListener('abort', () => reject(new DOMException('请求已取消', 'AbortError')), { once: true })
      })
    }
  })
  let home
  const stop = watch(() => deviceSelection.ready, (ready) => {
    if (ready) home = Promise.allSettled([
      api.getLeadershipReport(month),
      api.listReports({ page: 1, pageSize: 10 }),
      api.getEnergyMonth(month),
    ])
  })
  try {
    await loadSession({ force: true })
    finishCatalog(response({ default_device_id: 'Cutter01', items: [{ id: 'Cutter01' }] }))
    await new Promise((done) => setImmediate(done))
    await nextTick()
    assert.ok(home, 'the home page must start loading after device readiness')
    assert.equal(pending.length, 3, 'preload must reuse the three pending home queries')
    assert.ok(pending.every((item) => !item.signal.aborted), 'starting preload must not cancel home data reads')
    for (const item of pending) item.resolve(response({ items: [], total: 0 }))
    assert.ok((await home).every((result) => result.status === 'fulfilled'))
    await startReportPreload(admin)
    assert.ok(reportPreload.items.every((item) => item.status === 'ready'))
    assert.equal(calls.filter((call) => /leadership-report|\/reports\?|energy-report\/month\?/.test(call.url)).length, 3)
  } finally {
    stop()
    for (const item of pending) item.resolve(response({ items: [], total: 0 }))
    await home
  }
})

test('a device removed from configuration is never used for restored report warmup', async () => {
  setDeviceCatalog({ default_device_id: 'Cutter01', items: [{ id: 'Cutter01' }, { id: 'Cutter02' }] })
  selectDevice('Cutter02')
  const calls = mockApi()
  await loadSession({ force: true })
  await new Promise((done) => setImmediate(done))
  await startReportPreload(admin)
  const reports = calls.filter((call) => /leadership-report|\/reports\?/.test(call.url))
  assert.equal(reports.length, 2)
  assert.ok(reports.every((call) => !call.url.includes('device_id=Cutter02')))
})

test('file pages do not start unrelated report preload jobs', async () => {
  const calls = mockApi()
  const result = await startReportPreload(admin, { pathname: '/admin/cutting-dispatch' })
  assert.deepEqual(result, [])
  assert.deepEqual(reportPreload.items, [])
  assert.equal(calls.length, 0)
})
