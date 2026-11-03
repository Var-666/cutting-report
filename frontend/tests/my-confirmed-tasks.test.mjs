import assert from 'node:assert/strict'
import { beforeEach, afterEach, test } from 'node:test'
import { useMyConfirmedTasks } from '../src/composables/useMyConfirmedTasks.ts'
import { setDeviceCatalog, resetDeviceContext } from '../src/api/deviceContext.ts'
import { reportToday } from '../src/api/reportDates.ts'
import { renderComponent } from './vue-component-helpers.mjs'

const fetchBefore = globalThis.fetch
beforeEach(() => setDeviceCatalog({ default_device_id: 'Cutter01', items: [{ id: 'Cutter01' }, { id: 'Cutter02' }] }))
afterEach(() => { globalThis.fetch = fetchBefore; resetDeviceContext() })
const response = data => ({ ok: true, json: async () => ({ success: true, data }) })
const data = (page = 1, total = 12, task = 'mine') => ({ items: [{ task_id: task }], total, page, page_size: 10,
  start_date: '2026-11-01', end_date: '2026-11-02' })

test('personal query defaults to today, keeps its own period when paging and never accepts a worker identity', async () => {
  const calls = []
  globalThis.fetch = async value => {
    const url = new URL(value, 'http://localhost'); calls.push(url)
    return response(data(Number(url.searchParams.get('page'))))
  }
  const state = useMyConfirmedTasks()
  assert.deepEqual(state.period.value, { startDate: reportToday(), endDate: reportToday() })
  await state.load()
  await state.applyPeriod({ startDate: '2026-11-01', endDate: '2026-11-02' })
  await state.load(2)
  const query = calls.at(-1).searchParams
  assert.equal(query.get('page'), '2')
  assert.equal(query.get('start_date'), '2026-11-01')
  assert.equal(query.get('end_date'), '2026-11-02')
  assert.equal(query.get('device_id'), 'Cutter01')
  assert.ok(calls.every(url => !url.searchParams.has('worker_id') && url.pathname === '/api/v1/workshop/my-confirmed-tasks'))
  assert.equal(state.result.value.total, 12)
})

test('refresh reflects confirmation changes and clamps a page after revocation reduces its count', async () => {
  const pages = []
  let total = 12
  globalThis.fetch = async value => {
    const page = Number(new URL(value, 'http://localhost').searchParams.get('page')); pages.push(page)
    return response({ ...data(page, total), items: page === 2 && total <= 10 ? [] : [{ task_id: 'mine' }] })
  }
  const state = useMyConfirmedTasks()
  await state.load(2)
  total = 10
  await state.load(undefined, { silent: true })
  assert.deepEqual(pages, [2, 2, 1])
  assert.equal(state.result.value.total, 10)
  assert.equal(state.result.value.page, 1)
  total = 11
  await state.load(undefined, { silent: true })
  assert.equal(state.result.value.total, 11)
})

test('late or paused responses cannot replace a newer personal history period', async () => {
  const pending = []
  globalThis.fetch = (value, options) => {
    if (String(value).includes('/requests/')) return Promise.resolve(response({}))
    return new Promise(resolve => pending.push({ resolve, signal: options.signal }))
  }
  const state = useMyConfirmedTasks()
  const initial = state.load()
  const historic = state.applyPeriod({ startDate: '2026-10-01', endDate: '2026-10-31' })
  assert.equal(pending[0].signal.aborted, true)
  pending[1].resolve(response(data(1, 4, 'historic')))
  await historic
  pending[0].resolve(response(data(1, 99, 'stale')))
  await initial
  assert.equal(state.result.value.items[0].task_id, 'historic')
  const refresh = state.load()
  state.pause()
  assert.equal(pending[2].signal.aborted, true)
  pending[2].resolve(response(data(1, 99, 'paused')))
  await refresh
  assert.equal(state.result.value.total, 4)
  assert.equal(state.loading.value, false)
  const count = pending.length
  await state.load()
  assert.equal(pending.length, count)
})

test('failed historic query clears old results, exposes retry, and prevents overlapping background refresh', async () => {
  const state = useMyConfirmedTasks()
  state.result.value = data()
  globalThis.fetch = async () => { throw Error('读取失败') }
  await state.applyPeriod({ startDate: '2026-10-01', endDate: '2026-10-31' })
  assert.equal(state.result.value, null)
  assert.equal(state.error.value, '读取失败')
  let finish, requests = 0
  globalThis.fetch = () => { requests++; return new Promise(resolve => { finish = resolve }) }
  const retry = state.load()
  await state.load(undefined, { silent: true })
  assert.equal(requests, 1)
  finish(response({ ...data(1, 0), items: [] }))
  await retry
  assert.equal(state.error.value, '')
  assert.equal(state.result.value.total, 0)
})

test('personal panel renders its own title, production-date rule and unknown initial count', async () => {
  const html = await renderComponent('components/workshop/MyConfirmedTasksPanel')
  assert.ok(html.includes('我的已确认任务'))
  assert.ok(html.includes('按生产日期统计'))
  assert.ok(html.includes('--'))
  assert.ok(!html.includes('撤销确认') && !html.includes('改派'))
})
