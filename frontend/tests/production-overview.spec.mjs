// @vitest-environment jsdom

import assert from 'node:assert/strict'
import { afterEach, test, vi } from 'vitest'
import { enableAutoUnmount, mount } from '@vue/test-utils'
import * as Vue from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { getProductionOverview, overviewPeriod } from '../src/api/productionOverview.ts'
import { getDeviceId, resetDeviceContext, setDeviceCatalog } from '../src/api/deviceContext.ts'
import { createReportCache, reportCache } from '../src/api/reportCache.ts'
import { REPORT_POLL_INTERVAL } from '../src/api/reportRequests.ts'
import { renderComponent } from './vue-component-helpers.mjs'
import { useProductionOverview } from '../src/composables/useProductionOverview'

vi.mock('../src/api/productionOverview.ts', async importOriginal => {
  const actual = await importOriginal()
  return { ...actual, getProductionOverview: vi.fn(actual.getProductionOverview) }
})

enableAutoUnmount(afterEach)

const lines = [
  { id: 'c1', type: 'cutting', name: '切割一线', enabled: true, source_key: 'sql' },
  { id: 'c2', type: 'cutting', name: '切割二线', enabled: true, source_key: 'sql' },
  { id: 'w1', type: 'welding', name: '焊接一线', enabled: true, source_key: 'mysql' },
  { id: 'w2', type: 'welding', name: '焊接二线', enabled: true, source_key: 'mysql' },
]
const data = (changes = {}) => ({ metrics: [{ title: '任务数', value: 0, unit: '项', precision: 0 }], dataAsOf: '2026-11-17T08:00:00+08:00', timezone: 'Asia/Shanghai', stale: false, refreshing: false, refreshFailed: false, empty: true, preparing: false, ...changes })
const response = payload => new Response(JSON.stringify({ success: true, data: payload }), { headers: { 'Content-Type': 'application/json' } })
const flush = async () => { await Vue.nextTick(); await new Promise(setImmediate) }
afterEach(() => {
  vi.useRealTimers()
  vi.mocked(getProductionOverview).mockRestore()
  reportCache.clear()
  resetDeviceContext()
})

test('month bounds cover leap years and year changes', () => {
  assert.deepEqual(overviewPeriod('2024-02'), { start: '2024-02-01', end: '2024-02-29' })
  assert.deepEqual(overviewPeriod('2026-12'), { start: '2026-12-01', end: '2026-12-31' })
})

test('overview reads explicit lines independently of selection and isolates same-source caches', async t => {
  setDeviceCatalog({ default_device_id: 'w1', items: lines })
  let now = 0
  vi.spyOn(reportCache, 'read').mockImplementation(createReportCache({ now: () => now, scope: getDeviceId }).read)
  const calls = []
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (url, options) => {
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

test('a missing cutting snapshot is preparing rather than a month with zero production', async t => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () => response({ current_month_total: { task_count: 0, cut_length_m: 0, work_time_seconds: 0 }, snapshot: { data_as_of: null, stale: true, refreshing: true } }))
  const value = await getProductionOverview(lines[0], '2026-11', new AbortController().signal)
  assert.equal(value.empty, false)
  assert.equal(value.preparing, true)
  assert.ok(value.metrics.every(metric => metric.value === null))
})

test('overview never presents welding generation time as the snapshot cutoff', async t => {
  let meta = { timezone: 'Asia/Tokyo', calculated_at: '2026-11-17T08:00:00+09:00', report_data: { stale: true } }
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () => response({ summary: { job_count: 0, weld_length_m: null }, meta }))
  const signal = new AbortController().signal
  const historical = await getProductionOverview(lines[2], '2026-11', signal, true)
  assert.equal(historical.dataAsOf, null)
  assert.equal(historical.calculatedAt, null)
  assert.equal(historical.empty, true)
  assert.deepEqual(historical.metrics.slice(0, 2).map(metric => metric.value), [0, null])
  meta = { ...meta, report_data: null }
  const generated = await getProductionOverview(lines[2], '2026-11', signal, true)
  assert.equal(generated.dataAsOf, null)
  assert.equal(generated.calculatedAt, meta.calculated_at)
})

function controller(read) {
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] })
  vi.setSystemTime(new Date('2026-11-17T01:00:00Z'))
  vi.mocked(getProductionOverview).mockImplementation(read)
  const items = Vue.ref(lines)
  let api
  const wrapper = mount({ setup() { api = useProductionOverview(() => items.value); return () => null } })
  return { ...api, items, close: () => wrapper.unmount() }
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
    state.refresh('c1')
    assert.equal(pending.length, 5, 'rapid re-clicks do not enqueue another refresh')
    pending[4].reject(Error('重试失败')); await flush()
    assert.equal(state.rows.value[0].data.dataAsOf, '2026-11-17T08:00:00+08:00')
    assert.equal(state.rows.value[0].error, '重试失败')
    state.refresh('c1')
    pending[5].reject(Object.assign(new Error('请求已取消'), { name: 'AbortError' })); await flush()
    assert.equal(state.rows.value[0].error, '')
    assert.ok(state.rows.value[0].data)
    state.items.value = [{ ...lines[0], enabled: false }]; await flush()
    assert.equal(state.rows.value[0].loading, false)
    assert.equal(pending.length, 6)
  } finally { state.close() }
})

test('catalog changes cancel only the affected row and ignore a late result after rebinding', async () => {
  const pending = []
  const state = controller((line, month, signal, force, preparing, mode) => new Promise(resolve => pending.push({ line, month, signal, mode, resolve })))
  try {
    state.items.value = lines.slice(0, 2)
    await flush()
    pending[0].resolve(data())
    await flush()
    const retained = state.rows.value[0].data
    const old = pending[1]
    state.items.value = [{ ...lines[0], name: '切割更名' }, { ...lines[1], source_key: 'replacement' }]
    await flush()
    assert.equal(state.rows.value[0].data, retained)
    assert.equal(state.rows.value[0].line.name, '切割更名')
    assert.equal(old.signal.aborted, true)
    const newest = pending.at(-1)
    assert.equal(newest.line.source_key, 'replacement')
    assert.equal(newest.mode, 'reload')
    newest.resolve(data({ dataAsOf: '2026-11-18T08:00:00+08:00' }))
    await flush()
    old.resolve(data({ dataAsOf: '2026-11-01T08:00:00+08:00' }))
    await flush()
    assert.equal(state.rows.value[1].data.dataAsOf, '2026-11-18T08:00:00+08:00')
    assert.equal(pending.filter(call => call.line.id === 'c1').length, 1)
  } finally { state.close() }
})

test('a second global refresh cannot restart a completed line while the first batch is active', async () => {
  const pending = []
  const state = controller((line, month, signal, force, preparing, mode) => mode === 'refresh'
    ? new Promise(resolve => pending.push({ id: line.id, resolve })) : Promise.resolve(data()))
  try {
    await flush()
    state.refresh()
    assert.equal(state.refreshingAll.value, true)
    pending[0].resolve(data())
    await flush()
    state.refresh()
    pending[1].resolve(data()); await flush()
    pending[2].resolve(data()); pending[3].resolve(data()); await flush()
    assert.deepEqual(pending.map(call => call.id), lines.map(line => line.id))
    assert.equal(state.refreshingAll.value, false)
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
    const reads = calls.length
    await vi.advanceTimersByTimeAsync(REPORT_POLL_INTERVAL * 75)
    assert.equal(calls.length, reads + 75)
    assert.match(state.rows.value[0].error, /已停止自动检查/)
    fail = true; state.refresh('c1'); await flush()
    const failedReads = calls.length
    await vi.advanceTimersByTimeAsync(REPORT_POLL_INTERVAL)
    assert.equal(calls.length, failedReads)
    vi.setSystemTime(new Date('2026-12-01T01:00:00Z'))
    await vi.advanceTimersByTimeAsync(60_000)
    assert.equal(state.month.value, '2026-12')
    assert.equal(calls.at(-1).month, '2026-12')
    assert.equal(state.rows.value[0].data, null)
  } finally { state.close() }
  vi.useRealTimers()
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
    [{}, '尚未读取生产数据'],
    [{ line: { ...lines[0], enabled: false } }, '产线未启用'],
    [{ loading: true }, '正在读取本月生产数据'],
    [{ loading: true, preparing: true }, '生产数据准备中'],
    [{ error: '源库不可用' }, '生产数据读取失败'],
    [{ data: data() }, '本月暂无生产记录'],
    [{ data: data({ preparing: true, empty: false, refreshing: true }) }, '生产数据准备中'],
    [{ data: data({ stale: true }) }, '历史结果待更新'],
    [{ data: data({ metrics: [{ title: '任务数', value: null, unit: '项', precision: 0 }], empty: false, stale: true, refreshFailed: true, refreshing: true }), error: '失败' }, '刷新失败'],
  ]) {
    const html = await renderComponent('components/ProductionOverviewCard', { row: { ...row, ...changes } })
    assert.ok(html.includes(expected), expected)
    assert.doesNotMatch(html, /统计截止|统计生成|统计时区|保留上次结果/)
    if (changes.error && !changes.data) assert.ok(!html.includes('任务数：0'))
    if (changes.data?.preparing) assert.ok(!html.includes('任务数：0'))
    if (changes.error && changes.data) {
      assert.ok(html.includes('任务数：暂无数据'))
      assert.doesNotMatch(html, /历史结果待更新|后台更新中|生产数据读取失败/)
    }
  }
  const healthy = await renderComponent('components/ProductionOverviewCard', { row: {
    ...row, data: data({ empty: false, calculatedAt: '2026-11-17T08:00:00+08:00' }),
  } })
  assert.doesNotMatch(healthy, /<footer\b|统计生成|统计截止/)
})
