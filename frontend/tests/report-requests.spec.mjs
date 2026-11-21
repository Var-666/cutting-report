import assert from 'node:assert/strict'
import { afterEach, test, vi } from 'vitest'
import { readReport, pollReportUpdates, REPORT_POLL_LIMIT } from '../src/api/reportRequests.ts'
import { reportCache } from '../src/api/reportCache.ts'
import { getPdmOverview } from '../src/api/pdmStatistics.ts'
import { getEnergyRange } from '../src/api/energy.ts'
import { resetDeviceContext, selectDevice, setDeviceCatalog } from '../src/api/deviceContext.ts'

const flush = () => new Promise(setImmediate)
const busy = () => Object.assign(new Error('准备中'), { status: 503, code: 'snapshot_refresh_busy' })
afterEach(() => { vi.useRealTimers(); reportCache.clear(); resetDeviceContext() })

test('a device switch before the HTTP loader starts cannot place another line in the original cache', async t => {
  setDeviceCatalog({ default_device_id: 'Cutter01', items: [
    { id: 'Cutter01', type: 'cutting', enabled: true }, { id: 'Cutter02', type: 'cutting', enabled: true },
  ] })
  const calls = []
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (url, options) => {
    const line = options.headers['X-Production-Line']
    assert.equal(new URL(url, 'http://test').searchParams.get('device_id'), line)
    calls.push(line)
    return Response.json({ success: true, data: { line } })
  })
  const first = getEnergyRange('2026-11-19', '2026-11-19')
  selectDevice('Cutter02')
  assert.equal((await first).line, 'Cutter01')
  assert.equal((await getEnergyRange('2026-11-19', '2026-11-19')).line, 'Cutter02')
  selectDevice('Cutter01')
  assert.equal((await getEnergyRange('2026-11-19', '2026-11-19')).line, 'Cutter01')
  assert.deepEqual(calls, ['Cutter01', 'Cutter02'])
})

test('PDM uses the shared HTTP preparation code and sends manual refresh only once', async t => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const urls = []
  vi.spyOn(globalThis, 'fetch').mockImplementation(async url => {
    urls.push(new URL(url, 'http://test'))
    return urls.length === 1 ? Response.json({ detail: '准备中' }, { status: 503, headers: { 'X-Report-Error': 'snapshot_refresh_busy' } })
      : Response.json({ success: true, data: { equipment: null, drawing: null } })
  })
  const pending = getPdmOverview(new AbortController().signal, { mode: 'refresh' })
  await flush()
  assert.equal(urls[0].searchParams.get('refresh'), 'true')
  vi.advanceTimersByTime(5000)
  assert.deepEqual(await pending, { equipment: null, drawing: null })
  assert.equal(urls.length, 2)
  assert.equal(urls[1].searchParams.has('refresh'), false)
})

test('background result polling continues through active retries and stops on a terminal failure', async t => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const old = { meta: { report_data: { refreshing: true } }, count: 1 }
  const updated = []
  const replies = [{ meta: { report_data: { refreshing: true, refresh_failed: true } }, count: 1 },
    { meta: { report_data: { refreshing: false } }, count: 2 }]
  const pending = pollReportUpdates(old, async () => replies.shift(), value => updated.push(value))
  assert.deepEqual(updated, [])
  vi.advanceTimersByTime(5000)
  await flush()
  assert.equal(updated[0].count, 1)
  vi.advanceTimersByTime(5000)
  await pending
  assert.equal(updated[1].count, 2)
  assert.equal(old.count, 1)
  await pollReportUpdates({ meta: { report_data: { refreshing: false, refresh_failed: true } } },
    () => assert.fail('terminal failures must not continue polling'), () => assert.fail())
})

test('only explicit refresh asks for source refresh and preparation polls never repeat it', async t => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const modes = []
  const pending = readReport('preparing', async (_signal, refresh) => {
    modes.push(refresh)
    if (modes.length === 1) throw busy()
    return { result: 7 }
  }, { mode: 'refresh' })
  await flush()
  vi.advanceTimersByTime(4999)
  assert.deepEqual(modes, [true])
  vi.advanceTimersByTime(1)
  assert.deepEqual(await pending, { result: 7 })
  assert.deepEqual(modes, [true, false])
  for (const mode of ['read', 'reload', 'poll']) {
    await readReport(mode, async (_signal, refresh) => { assert.equal(refresh, false); return {} }, { mode })
  }
})

test('preparation is bounded to 75 five-second waits', async t => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  let calls = 0
  const pending = readReport('bounded', async () => { calls++; throw busy() })
  const rejected = assert.rejects(pending, /准备中/)
  for (let attempt = 0; attempt < REPORT_POLL_LIMIT; attempt++) {
    await flush()
    vi.advanceTimersByTime(5000)
  }
  await rejected
  assert.equal(calls, 76)
})

test('cancellation clears the shared preparation wait and source failures do not retry', async t => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const controller = new AbortController()
  let calls = 0
  const pending = readReport('cancel', async () => { calls++; throw busy() }, { signal: controller.signal })
  const rejected = assert.rejects(pending, { name: 'AbortError' })
  await flush()
  controller.abort()
  await rejected
  vi.advanceTimersByTime(5000)
  await flush()
  assert.equal(calls, 1)
  await assert.rejects(readReport('source', async () => {
    calls++
    throw Object.assign(new Error('源库暂不可用'), { status: 503, code: 'source_unavailable' })
  }), /源库暂不可用/)
  assert.equal(calls, 2)
})

test('report reads wait for temporary HTTP capacity errors and cap retries', async t => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const calls = []
  const headers = ['X-SQL-Resource-Error', 'X-Workload-Error', 'X-Report-Error']
  const codes = ['queue_timeout', 'queue_full', 'snapshot_queue_full']
  vi.spyOn(globalThis, 'fetch').mockImplementation(async url => {
    const index = calls.length
    calls.push(new URL(url, 'http://test').searchParams.get('refresh'))
    return Response.json({ detail: '队列繁忙' }, { status: 503, headers: { [headers[index]]: codes[index], 'Retry-After': '1' } })
  })
  const pending = getPdmOverview(undefined, { mode: 'refresh' })
  const rejected = assert.rejects(pending, /队列繁忙/)
  for (let i = 0; i < 2; i++) {
    await flush()
    vi.advanceTimersByTime(5000)
  }
  await rejected
  assert.deepEqual(calls, ['true', 'true', 'true'], 'queue rejection has not accepted the refresh')
})
