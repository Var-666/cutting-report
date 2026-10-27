import { scriptFunction } from './typescript-helpers.mjs'
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { lineHome, selectedLineType } from '../src/utils/productionLines.ts'
import { ref } from 'vue'
import { getDeviceId, deviceUrl, selectDevice, setDeviceCatalog, resetDeviceContext, registerDeviceSwitchQuery, deviceSwitchQuery } from '../src/api/deviceContext.ts'
import { createReportCache, reportCache } from '../src/api/reportCache.ts'
import { listWorkshopTasks, createReport, getDeviceCatalog } from '../src/api/reports.ts'
import { listWorkshopRangeTasks } from '../src/api/cutting.ts'
import { changeDevice, deviceSelection, loadDevices } from '../src/state/devices.ts'
import { formatDeviceDisplay } from '../src/utils/deviceDisplay.ts'

const catalog = { default_device_id: 'Cutter01', items: [
  { id: 'Cutter01', machine_no: 'Cutter01', name: '20KW切割机' },
  { id: 'Cutter02', machine_no: 'Cutter02', name: '30KW切割机' },
] }
const response = (data) => new Response(JSON.stringify({ success: true, data }), { headers: { 'Content-Type': 'application/json' } })
const source = (path) => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8')

test.afterEach(() => { reportCache.clear(); resetDeviceContext() })

function mockSessionStorage(t, initial = {}) {
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'sessionStorage')
  const values = new Map(Object.entries(initial))
  const storage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  }
  Object.defineProperty(globalThis, 'sessionStorage', { configurable: true, value: storage })
  t.after(() => {
    if (previous) Object.defineProperty(globalThis, 'sessionStorage', previous)
    else delete globalThis.sessionStorage
  })
  return storage
}

test('refresh restores the chosen equipment before the first report request', async (t) => {
  const storage = mockSessionStorage(t)
  const before = await import('../src/api/deviceContext.ts?before-refresh')
  before.setDeviceCatalog(catalog)
  before.selectDevice('Cutter02')
  assert.equal(storage.getItem('cutting-report.device-id'), 'Cutter02')
  const refreshed = await import('../src/api/deviceContext.ts?after-refresh')
  assert.equal(refreshed.getDeviceId(), 'Cutter02')
  assert.match(refreshed.deviceUrl('/api/v1/workshop/latest-date'), /device_id=Cutter02/)
  refreshed.setDeviceCatalog(catalog)
  assert.equal(refreshed.getDeviceId(), 'Cutter02')
})

test('a removed saved device falls back to the catalog default and session reset forgets it', async (t) => {
  const storage = mockSessionStorage(t, { 'cutting-report.device-id': 'RemovedDevice' })
  const context = await import('../src/api/deviceContext.ts?removed-device')
  context.setDeviceCatalog(catalog)
  assert.equal(context.getDeviceId(), 'Cutter01')
  assert.equal(storage.getItem('cutting-report.device-id'), 'Cutter01')
  context.selectDevice('Cutter02')
  context.resetDeviceContext()
  assert.equal(context.getDeviceId(), '')
  assert.equal(storage.getItem('cutting-report.device-id'), null)
})

test('blocked browser storage does not prevent loading and switching equipment', async (t) => {
  const storage = mockSessionStorage(t)
  for (const method of ['getItem', 'setItem', 'removeItem']) storage[method] = () => { throw new Error('storage blocked') }
  const context = await import('../src/api/deviceContext.ts?blocked-storage')
  assert.equal(context.getDeviceId(), '')
  context.setDeviceCatalog(catalog)
  context.selectDevice('Cutter02')
  assert.equal(context.getDeviceId(), 'Cutter02')
  context.resetDeviceContext()
  assert.equal(context.getDeviceId(), '')
})

test('device context rejects unknown equipment and preserves existing report filters in scoped URLs', () => {
  setDeviceCatalog(catalog)
  selectDevice('Cutter02')
  assert.equal(getDeviceId(), 'Cutter02')
  const url = new URL(deviceUrl('/api/v1/workshop/range?start_date=2026-10-01&end_date=2026-10-12&project_name=P1'), 'http://test')
  assert.equal(url.searchParams.get('device_id'), 'Cutter02')
  assert.equal(url.searchParams.get('start_date'), '2026-10-01')
  assert.equal(url.searchParams.get('project_name'), 'P1')
  assert.throws(() => selectDevice('missing'), /设备不存在/)
  assert.equal(getDeviceId(), 'Cutter02')
})

test('a fresh session follows the catalog default instead of assuming Cutter01', async (t) => {
  mockSessionStorage(t)
  const context = await import('../src/api/deviceContext.ts?catalog-default')
  context.setDeviceCatalog({ ...catalog, default_device_id: 'Cutter02' })
  assert.equal(context.getDeviceId(), 'Cutter02')
  context.selectDevice('Cutter01')
  const url = new URL(context.deviceUrl('/api/v1/workshop/tasks?report_date=2026-10-19'), 'http://test')
  assert.equal(url.searchParams.get('device_id'), 'Cutter01', 'the backend default must not redirect an explicit selection')
})

for (const outcome of ['success', 'failure']) {
  test(`a late device catalog ${outcome} after logout cannot overwrite the next session`, async (t) => {
    const pending = []
    t.after(() => { for (const item of pending) item.resolve(response(catalog)) })
    t.mock.method(globalThis, 'fetch', () => new Promise((resolve, reject) => { pending.push({ resolve, reject }) }))
    const old = loadDevices()
    assert.equal(pending.length, 1)
    resetDeviceContext()
    const fresh = loadDevices()
    assert.equal(pending.length, 2, 'a new session must start its own catalog request')
    if (outcome === 'success') pending[0].resolve(response(catalog))
    else pending[0].reject(new Error('previous session failure'))
    await old
    assert.equal(deviceSelection.ready, false)
    assert.equal(deviceSelection.loading, true)
    assert.equal(deviceSelection.error, '')
    assert.deepEqual(deviceSelection.items, [])
    pending[1].resolve(response(catalog))
    await fresh
    assert.equal(deviceSelection.ready, true)
    assert.equal(deviceSelection.loading, false)
  })
}

test('account, settings, usage and file dispatch endpoints stay shared when viewing Cutter02', () => {
  setDeviceCatalog(catalog)
  selectDevice('Cutter02')
  for (const url of ['/api/v1/auth/status', '/api/v1/automation/schedules', '/api/v1/llm-usage/report', '/api/v1/cutting/dispatches']) {
    assert.equal(deviceUrl(url), url)
  }
  assert.equal(deviceUrl('/api/v1/reports/r2/status?device_id=Cutter02', 'Cutter01'), '/api/v1/reports/r2/status?device_id=Cutter02')
})

test('same date cache entries cannot return another device report', async () => {
  setDeviceCatalog(catalog)
  const cache = createReportCache({ scope: getDeviceId })
  assert.equal(await cache.read('workshop:2026-10-12', () => 'one'), 'one')
  selectDevice('Cutter02')
  assert.equal(await cache.read('workshop:2026-10-12', () => 'two'), 'two')
  selectDevice('Cutter01')
  assert.equal(await cache.read('workshop:2026-10-12', () => assert.fail('lost Cutter01 cache')), 'one')
})

test('switching devices aborts pending shared reads and rejects late results', async () => {
  setDeviceCatalog(catalog)
  let resolveOld
  let oldSignal
  const old = reportCache.read('workshop:tasks:day', (signal) => {
    oldSignal = signal
    return new Promise((resolve) => { resolveOld = resolve })
  })
  const rejected = assert.rejects(old, { name: 'AbortError' })
  await Promise.resolve()
  changeDevice('Cutter02')
  assert.equal(oldSignal.aborted, true)
  const fresh = await reportCache.read('workshop:tasks:day', () => 'Cutter02')
  resolveOld('Cutter01 late result')
  await rejected
  assert.equal(fresh, 'Cutter02')
  assert.equal(await reportCache.read('workshop:tasks:day', () => assert.fail('late result replaced cache')), 'Cutter02')
})

test('selected device is sent for task queries and report creation', async (t) => {
  setDeviceCatalog(catalog)
  selectDevice('Cutter02')
  const requests = []
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    requests.push([url, options])
    return response({ report_id: 'r2', items: [] })
  })
  await listWorkshopTasks({ reportDate: '2026-10-12' })
  await createReport({ report_type: 'equipment', report_date: '2026-10-12' })
  assert.equal(requests.length, 2)
  for (const [url] of requests) assert.equal(new URL(url, 'http://test').searchParams.get('device_id'), 'Cutter02')
  assert.equal(JSON.parse(requests[1][1].body).report_date, '2026-10-12')
  assert.equal(requests[1][1].credentials, 'same-origin')
})

test('catalog remains authenticated and new device names come from configuration', async (t) => {
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(url, '/api/v1/devices')
    assert.equal(options.credentials, 'same-origin')
    return response({ ...catalog, items: [...catalog.items, { id: 'Cutter03', machine_no: 'Cutter03', name: '备用切割机' }] })
  })
  setDeviceCatalog(await getDeviceCatalog())
  assert.equal(formatDeviceDisplay('Cutter02 / Cutter03'), '30KW切割机 / 备用切割机')
  assert.equal(formatDeviceDisplay('OtherCutter02 Cutter020'), 'OtherCutter02 Cutter020')
})

test('shells use the selected device to discard cached views, while keeping route queries', () => {
  assert.match(source('layouts/AdminLayout.vue'), /devicePage \? deviceSelection.id : undefined/)
  assert.match(source('layouts/WorkerLayout.vue'), /:key="deviceSelection.id"/)
  assert.match(source('components/DeviceSwitcher.vue'), /max-width:600px/)
  assert.match(source('components/DeviceSwitcher.vue'), /role="alert"/)
  assert.match(source('components/DeviceSwitcher.vue'), /正在读取设备/)
  assert.doesNotMatch(source('state/devices.ts'), /router\.(push|replace)/)
})

test('both device selectors preserve displayed filters before switching and reject overlapping switches', async () => {
  const script = source('composables/useDeviceSelection.ts').replace(/^import .*$/gm, '').replace('export function', 'function')
  const selection = { id: 'Cutter01', items: catalog.items }
  const changes = []
  let completeNavigation
  let failNavigation = false
  const route = { name: 'workshop-report', query: { project: 'P1' } }
  const router = { replace: to => {
    changes.push(to)
    if (failNavigation) return Promise.reject(new Error('navigation failed'))
    return new Promise(resolve => { completeNavigation = resolve })
  } }
  const api = scriptFunction('ref', 'onMounted', 'useRoute', 'useRouter', 'deviceSwitchQuery', 'changeDevice', 'deviceSelection', 'loadDevices', 'lineHome', 'selectedLineType', `${script}\nreturn useDeviceSelection()`)(ref, () => {}, () => route, () => router, () => ({ start_date: '2026-10-12' }), id => { selection.id = id }, selection, () => {}, lineHome, selectedLineType)
  await api.switchDevice('Cutter01')
  assert.equal(changes.length, 0)
  const pending = api.switchDevice('Cutter02')
  await api.switchDevice('Cutter02')
  assert.equal(api.switching.value, true)
  assert.equal(selection.id, 'Cutter01')
  assert.deepEqual(changes, [{ query: { project: 'P1', start_date: '2026-10-12' } }])
  completeNavigation()
  await pending
  assert.equal(selection.id, 'Cutter02')
  assert.equal(api.switching.value, false)
  failNavigation = true
  await assert.rejects(api.switchDevice('Cutter01'), /navigation failed/)
  assert.equal(selection.id, 'Cutter02')
  assert.equal(api.switching.value, false)
  for (const path of ['components/DeviceSwitcher.vue', 'layouts/modules/global-menu/modules/vertical-mix-menu.vue']) assert.match(source(path), /useDeviceSelection\(\)/)
})

test('scheduled reports store a fixed device and disconnected energy is explicitly labelled', () => {
  const automation = source('views/AutomationView.vue')
  assert.match(automation, /device_id: form.device_id/)
  assert.match(automation, /device_id: item.device_id/)
  assert.match(automation, /计划固定使用此设备/)
  const energy = source('views/EnergyPeriodReportView.vue')
  assert.match(energy, /energy_connected !== false/)
  assert.match(energy, /return '暂未接入'/)
  assert.doesNotMatch(energy, /equipment_scope\?\.join\(' \/ '\) \|\| 'Cutter01'/)
})

test('device switch captures the displayed latest day so another machine cannot silently change the date', () => {
  const unregisterOld = registerDeviceSwitchQuery('workshop-report', () => ({ start: '2026-10-11' }))
  const unregisterNew = registerDeviceSwitchQuery('workshop-report', () => ({ start: '2026-10-12', end: '2026-10-12', project: 'P1' }))
  unregisterOld()
  assert.deepEqual(deviceSwitchQuery('workshop-report'), { start: '2026-10-12', end: '2026-10-12', project: 'P1' })
  assert.equal(deviceSwitchQuery('equipment-report'), undefined)
  unregisterNew()
  assert.equal(deviceSwitchQuery('workshop-report'), undefined)
})


test('every device including catalog default is explicit in previews exports and polling', () => {
  assert.throws(() => deviceUrl('/api/v1/workshop/tasks'), /设备目录尚未加载/)
  setDeviceCatalog(catalog)
  for (const id of ['Cutter01', 'Cutter02']) {
    selectDevice(id)
    for (const path of ['/api/v1/workshop/tasks', '/api/v1/reports/r1/status', '/api/v1/reports/r1/download?format=pdf', '/api/v1/database/preview', '/api/v1/designer/query-tasks/q1/preview']) {
      assert.equal(new URL(deviceUrl(path), 'http://test').searchParams.get('device_id'), id)
    }
  }
})

test('browsers without URLSearchParams.size retain device and report query parameters', async t => {
  const descriptor = Object.getOwnPropertyDescriptor(URLSearchParams.prototype, 'size')
  delete URLSearchParams.prototype.size
  t.after(() => Object.defineProperty(URLSearchParams.prototype, 'size', descriptor))
  assert.equal(new URLSearchParams('device_id=Cutter02').size, undefined)
  setDeviceCatalog(catalog)
  selectDevice('Cutter02')
  for (const path of ['/api/v1/workshop/range', '/api/v1/leadership-report', '/api/v1/energy-report/day', '/api/v1/analysis/version', '/api/v1/reports/r1/download']) {
    const url = new URL(deviceUrl(`${path}?start_date=2026-10-01&format=pdf`), 'http://test')
    assert.equal(url.searchParams.get('device_id'), 'Cutter02')
    assert.equal(url.searchParams.get('start_date'), '2026-10-01')
    assert.equal(url.searchParams.get('format'), 'pdf')
  }
  assert.equal(deviceUrl('/api/v1/reports/r1/status?device_id=Cutter01'), '/api/v1/reports/r1/status?device_id=Cutter01')
  const requests = []
  t.mock.method(globalThis, 'fetch', async url => { requests.push(url); return response({ items: [] }) })
  await listWorkshopRangeTasks({ startDate: '2026-10-01', endDate: '2026-10-27', projectName: 'P1', page: 2 })
  assert.equal(requests.length, 1)
  const params = new URL(requests[0], 'http://test').searchParams
  assert.equal(params.get('device_id'), 'Cutter02')
  assert.equal(params.get('start_date'), '2026-10-01')
  assert.equal(params.get('end_date'), '2026-10-27')
  assert.equal(params.get('project_name'), 'P1')
  assert.equal(params.get('page'), '2')
})
