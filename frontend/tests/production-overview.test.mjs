import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { getProductionOverview, overviewPeriod } from '../src/api/productionOverview.ts'
import { getDeviceId, resetDeviceContext, setDeviceCatalog } from '../src/api/deviceContext.ts'
import { createReportCache, reportCache } from '../src/api/reportCache.ts'
import { REPORT_POLL_INTERVAL, REPORT_POLL_LIMIT } from '../src/api/reportRequests.ts'
import { renderComponent } from './vue-component-helpers.mjs'
import { scriptFunction } from './typescript-helpers.mjs'

const lines = [
  { id: 'c1', type: 'cutting', name: '切割一线', enabled: true, source_key: 'sql' },
  { id: 'c2', type: 'cutting', name: '切割二线', enabled: true, source_key: 'sql' },
  { id: 'w1', type: 'welding', name: '焊接一线', enabled: true, source_key: 'mysql' },
  { id: 'w2', type: 'welding', name: '焊接二线', enabled: true, source_key: 'mysql' },
]
const data = (changes = {}) => ({ metrics: [{ title: '任务数', value: 0, unit: '项', precision: 0 }], dataAsOf: '2026-11-17T08:00:00+08:00', timezone: 'Asia/Shanghai', stale: false, refreshing: false, refreshFailed: false, empty: true, preparing: false, ...changes })
const response = payload => new Response(JSON.stringify({ success: true, data: payload }), { headers: { 'Content-Type': 'application/json' } })
const flush = async () => { await Vue.nextTick(); await new Promise(setImmediate) }
test.afterEach(() => { reportCache.clear(); resetDeviceContext() })

test('month bounds cover leap years and year changes', () => {
  assert.deepEqual(overviewPeriod('2024-02'), { start: '2024-02-01', end: '2024-02-29' })
  assert.deepEqual(overviewPeriod('2026-12'), { start: '2026-12-01', end: '2026-12-31' })
})

test('overview reads explicit lines independently of selection and isolates same-source caches', async t => {
  setDeviceCatalog({ default_device_id: 'w1', items: lines })
  let now = 0
  t.mock.method(reportCache, 'read', createReportCache({ now: () => now, scope: getDeviceId }).read)
  const calls = []
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    const target = options.headers['X-Production-Line']
    calls.push({ url: new URL(url, 'http://test'), target })
    if (target.startsWith('c')) return response({ current_month_total: { task_count: target === 'c1' ? 11 : 22, cut_length_m: 12, work_time_seconds: 7200 }, snapshot: { data_as_of: '2026-11-17', stale: false, refreshing: false } })
    return response({ summary: { job_count: target === 'w1' ? 33 : 44, weld_length_m: 45, working_hours: 6 }, meta: { timezone: 'Asia/Tokyo', calculated_at: '2026-11-17', report_data: { data_as_of: '2026-11-16', stale: false, refreshing: false, refresh_failed: false } } })
  })
  const values = await Promise.all(lines.map(line => getProductionOverview(line, '2026-11', new AbortController().signal, true)))
  assert.deepEqual(values.map(value => value.metrics[0].value), [11, 22, 33, 44])
  assert.equal(values[0].metrics[2].value, 2)
  assert.equal(getDeviceId(), 'w1')
  for (const call of calls.filter(call => call.target.startsWith('c'))) assert.equal(call.url.searchParams.get('device_id'), call.target)
  for (const call of calls.filter(call => call.target.startsWith('w'))) {
    assert.equal(call.url.searchParams.get('start'), '2026-11-01')
    assert.equal(call.url.searchParams.get('end'), '2026-11-30')
    assert.equal(call.url.searchParams.has('refresh'), false, 'polling must not force another source refresh')
  }
  assert.equal(values[2].dataAsOf, '2026-11-16')
  assert.equal(values[2].timezone, 'Asia/Tokyo')
  assert.equal(values[2].refreshFailed, false)
  await getProductionOverview(lines[0], '2026-11', new AbortController().signal)
  assert.equal(calls.length, 4)
  now = 3001
  await Promise.all(lines.map(line => getProductionOverview(line, '2026-11', new AbortController().signal)))
  assert.equal(calls.length, 4, 'cutting and welding both retain the five-minute browser cache')
  now = 300000
  await Promise.all(lines.map(line => getProductionOverview(line, '2026-11', new AbortController().signal)))
  assert.equal(calls.length, 8, 'both line types expire at the same five-minute boundary')
})

test('overview timestamps display the same Beijing time for UTC and offset snapshots', async () => {
  setActivePinia(createPinia())
  for (const [line, timestamp] of [[lines[0], '2026-11-19T10:21:57+08:00'], [lines[2], '2026-11-19T02:21:57+00:00']]) {
    const html = await renderComponent('components/ProductionOverviewCard', {
      row: { line, loading: false, preparing: false, error: '', data: data({ dataAsOf: timestamp }) },
    })
    assert.ok(html.includes('2026年11月19日 10:21:57'))
    assert.ok(html.includes(`datetime="${timestamp}"`))
  }
})

test('a missing cutting snapshot is preparing rather than a month with zero production', async t => {
  t.mock.method(globalThis, 'fetch', async () => response({ current_month_total: { task_count: 0, cut_length_m: 0, work_time_seconds: 0 }, snapshot: { data_as_of: null, stale: true, refreshing: true } }))
  const value = await getProductionOverview(lines[0], '2026-11', new AbortController().signal)
  assert.equal(value.empty, false)
  assert.equal(value.preparing, true)
  assert.ok(value.metrics.every(metric => metric.value === null))
})

function controller(read) {
  let today = '2026-11-17'
  const timers = new Map(), intervals = new Map()
  let timerId = 0, mounted, dispose
  const items = Vue.ref(lines)
  const script = readFileSync(new URL('../src/composables/useProductionOverview.ts', import.meta.url), 'utf8').replace(/^import .*$/gm, '').replace(/^export /gm, '')
  const bindings = { REPORT_POLL_INTERVAL, REPORT_POLL_LIMIT, ref: Vue.ref, watch: Vue.watch, onMounted: callback => { mounted = callback }, onBeforeUnmount: callback => { dispose = callback }, getProductionOverview: read,
    reportToday: () => today, toError: value => value, setTimeout: callback => { timers.set(++timerId, callback); return timerId }, clearTimeout: id => timers.delete(id),
    setInterval: callback => { intervals.set(++timerId, callback); return timerId }, clearInterval: id => intervals.delete(id) }
  const scope = Vue.effectScope()
  const api = scope.run(() => scriptFunction(...Object.keys(bindings), `${script}; return useProductionOverview`)(...Object.values(bindings))(() => items.value))
  mounted()
  return { ...api, items, timers, tickMonth: value => { today = value; for (const callback of intervals.values()) callback() }, close: () => { dispose(); scope.stop() } }
}

test('overview limits concurrency, skips disabled lines and retains results after a failed retry', async () => {
  let running = 0, maximum = 0
  const pending = []
  const state = controller((line, month, signal, force, preparing) => new Promise((resolve, reject) => {
    running++; maximum = Math.max(maximum, running)
    pending.push({ line, signal, preparing, resolve: value => { running--; resolve(value) }, reject: error => { running--; reject(error) } })
  }))
  try {
    assert.equal(pending.length, 2)
    pending[0].resolve(data()); await flush()
    assert.equal(pending.length, 3)
    pending[1].reject(Error('二线不可用')); await flush()
    pending[2].preparing()
    assert.equal(state.rows.value[2].preparing, true)
    pending[2].resolve(data()); await flush()
    pending[3].resolve(data()); await flush()
    assert.equal(maximum, 2)
    assert.equal(state.rows.value[1].error, '二线不可用')
    assert.equal(state.rows.value[0].data.metrics[0].value, 0)
    state.refresh('c1')
    pending[4].reject(Error('重试失败')); await flush()
    assert.equal(state.rows.value[0].data.dataAsOf, '2026-11-17T08:00:00+08:00')
    assert.equal(state.rows.value[0].error, '重试失败')
    state.refresh('c1')
    pending[5].reject(new DOMException('请求已取消', 'AbortError')); await flush()
    assert.equal(state.rows.value[0].error, '')
    assert.ok(state.rows.value[0].data)
    state.items.value = [{ ...lines[0], enabled: false }]; await flush()
    assert.equal(state.rows.value[0].loading, false)
    assert.equal(pending.length, 6)
  } finally { state.close() }
})

test('polling is bounded, stops on errors and cancels on leaving or a new month', async () => {
  const calls = []
  let fail = false
  const state = controller(async (line, month, signal) => {
    calls.push({ line, month, signal })
    if (fail) throw Error('源查询失败')
    return data({ refreshing: true, stale: true })
  })
  try {
    state.items.value = [lines[0]]; await flush()
    assert.equal(state.timers.size, 1)
    for (let index = 0; index < 75; index++) {
      const [id, callback] = [...state.timers.entries()][0]
      state.timers.delete(id); callback(); await flush()
    }
    assert.equal(state.timers.size, 0)
    fail = true; state.refresh('c1'); await flush()
    assert.equal(state.timers.size, 0)
    state.tickMonth('2026-12-01'); await flush()
    assert.equal(state.month.value, '2026-12')
    assert.equal(calls.at(-1).month, '2026-12')
    assert.equal(state.rows.value[0].data, null)
  } finally { state.close() }
  const pending = []
  const leaving = controller((line, month, signal) => new Promise(resolve => pending.push({ signal, resolve })))
  leaving.close()
  assert.ok(pending.every(call => call.signal.aborted))
  pending.forEach(call => call.resolve(data())); await flush()
  assert.ok(leaving.rows.value.every(row => row.data === null))
})

test('overview cards render disabled, loading, preparing, empty and retained failure states', async () => {
  setActivePinia(createPinia())
  const row = { line: lines[0], data: null, loading: false, preparing: false, error: '' }
  for (const [changes, expected] of [
    [{ line: { ...lines[0], enabled: false } }, '产线未启用'],
    [{ loading: true }, '正在读取本月生产数据'],
    [{ loading: true, preparing: true }, '生产数据准备中'],
    [{ error: '源库不可用' }, '生产数据读取失败'],
    [{ data: data() }, '本月暂无生产记录'],
    [{ data: data({ metrics: [{ title: '任务数', value: null, unit: '项', precision: 0 }], empty: false, stale: true }), error: '失败' }, '刷新失败，保留上次结果'],
  ]) {
    const html = await renderComponent('components/ProductionOverviewCard', { row: { ...row, ...changes } })
    assert.ok(html.includes(expected), expected)
    if (changes.error && !changes.data) assert.ok(!html.includes('任务数：0'))
    if (changes.data?.stale) assert.ok(html.includes('数据待更新') && html.includes('任务数：暂无数据'))
  }
})
