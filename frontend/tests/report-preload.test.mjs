import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'
import { startReportPreload, stopReportPreload, reportPreload } from '../src/state/reportPreload.js'
import { reportToday } from '../src/api/reportDates.js'
import * as api from '../src/api/reports.js'
import { session, loadSession, completeAuthentication, endSession } from '../src/state/session.js'

const originalFetch = globalThis.fetch
const admin = { user_id: 'admin-1', role: 'admin' }
const today = reportToday()
const month = today.slice(0, 7)
const response = (data) => ({ ok: true, json: async () => ({ success: true, data }) })
function mockApi(override = () => undefined) {
  const calls = []
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options })
    const custom = override(url, options)
    if (custom !== undefined) return custom
    if (url.includes('latest-date')) return response({ report_date: today })
    if (url === '/api/v1/auth/status') return response({ user: admin, setup_required: false })
    return response({ report_date: today, items: [], total: 0 })
  }
  return calls
}
afterEach(() => { stopReportPreload(); globalThis.fetch = originalFetch })

test('admin login warms all default reports; opening pages reuses those requests', async () => {
  const calls = mockApi()
  const completion = startReportPreload(admin)
  await Promise.all([
    completion,
    api.getLeadershipReport(month),
    api.getEnergyDay(),
    api.getTaskEnergyDay(),
    api.listReports({ page: 1, pageSize: 10 }),
  ])
  await api.listWorkshopTasks({ reportDate: today })
  await api.getEnergyMonth(month)
  assert.equal(calls.length, 7)
  assert.equal(new Set(calls.map((c) => c.url)).size, 7)
  assert.ok(calls.some((c) => c.url.includes('warmup=true')))
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
  await startReportPreload(admin)
  assert.equal(reportPreload.items.find((i) => i.key === 'leadership').status, 'failed')
  assert.equal(reportPreload.items.filter((i) => i.status === 'ready').length, 3)
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
  assert.equal(calls.length, 1)
  assert.deepEqual(reportPreload.items, [])
})

test('different accounts do not reuse the previous account report cache', async () => {
  const calls = mockApi()
  await startReportPreload(admin)
  await startReportPreload({ ...admin, user_id: 'admin-2' })
  assert.equal(calls.filter((c) => c.url.includes('leadership-report')).length, 2)
})

test('explicit refresh bypasses preload; daily snapshots invalidate monthly totals', async () => {
  const calls = mockApi()
  await startReportPreload(admin)
  await api.getLeadershipReport(month, { force: true })
  await api.getEnergyDay(today, { force: true })
  await api.getEnergyMonth(month)
  assert.equal(calls.filter((c) => c.url.includes('leadership-report')).length, 2)
  assert.equal(calls.filter((c) => c.url.includes('energy-report/month')).length, 2)
  assert.equal(calls.filter((c) => c.url.includes('refresh=true')).length, 2)
})

test('new confirmations invalidate cached workshop rows', async () => {
  const calls = mockApi()
  await startReportPreload({ user_id: 'worker-1', role: 'worker' })
  await api.confirmWorkshopTask('task-1', today)
  await api.listWorkshopTasks({ reportDate: today, sortMode: 'worker' })
  assert.equal(calls.filter((c) => c.url.includes('/workshop/tasks?')).length, 2)
})

test('login and restored sessions start background loading without waiting for report or health', async () => {
  let finishHealth
  const calls = mockApi((url) => {
    if (url.includes('/health')) return new Promise((done) => { finishHealth = done })
  })
  await completeAuthentication(admin)
  assert.equal(session.user.user_id, admin.user_id)
  assert.equal(session.ready, true)
  assert.equal(session.systemInfo, null)
  await startReportPreload(admin)
  assert.ok(calls.some((c) => c.url.includes('energy-report')))
  await endSession()
  finishHealth(response({ version: 'stale' }))
  await new Promise((done) => setImmediate(done))
  assert.equal(session.systemInfo, null)
  assert.equal(reportPreload.identity, '')
  await loadSession({ force: true })
  assert.equal(session.user.user_id, admin.user_id)
  assert.equal(reportPreload.identity, `admin:${admin.user_id}`)
  await startReportPreload(admin)
  finishHealth(response({ version: 'current' }))
  await new Promise((done) => setImmediate(done))
  assert.equal(session.systemInfo.version, 'current')
})
