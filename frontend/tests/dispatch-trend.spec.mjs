// @vitest-environment jsdom

import assert from 'node:assert/strict'
import { flushPromises, mount } from '@vue/test-utils'
import { init } from 'echarts/core'
import { afterEach, test, vi } from 'vitest'
import { ref } from 'vue'
import AppDatePicker from '../src/components/AppDatePicker.vue'
import DispatchHistoryTrend from '../src/components/dispatch/DispatchHistoryTrend.vue'
import { useCuttingDispatchHistory } from '../src/composables/useCuttingDispatchHistory.ts'
import { renderComponent } from './vue-component-helpers.mjs'

vi.mock('echarts/core', async importOriginal => ({
  ...await importOriginal(),
  init: vi.fn(),
}))

const originalFetch = globalThis.fetch
afterEach(() => {
  globalThis.fetch = originalFetch
  vi.useRealTimers()
})
const period = { start_date: '2026-11-01', end_date: '2026-11-03', machine_ip: '10.0.0.2' }
const days = [{ date: '2026-11-01', sent_count: 0, completed_count: 0 }, { date: '2026-11-02', sent_count: 12, completed_count: 0 }, { date: '2026-11-03', sent_count: 0, completed_count: 0 }]
const response = data => Response.json({ success: true, data })
function state() {
  const pageActive = ref(true)
  const busy = ref(false)
  return { ...useCuttingDispatchHistory({ pageActive, isBusy: () => busy.value }), pageActive, busy }
}
function dataFor(url) {
  const query = url.searchParams
  if (url.pathname.endsWith('/trend')) return { ...period, timezone: 'Asia/Shanghai', machine_ips: ['10.0.0.2'], items: days }
  return { page: Number(query.get('page')), total: 12, items: [{ dispatch_id: query.get('start_date') || 'all' }] }
}

function mountTrend(t, trend, overrides = {}) {
  const clicks = {}, canvasClicks = {}
  const chart = { on: (name, callback) => { clicks[name] = callback },
    getZr: () => ({ on: (name, callback) => { canvasClicks[name] = callback } }),
    setOption() {}, resize() {}, dispose() {}, containPixel: () => true, convertFromPixel: () => 0 }
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  init.mockReturnValue(chart)
  const wrapper = mount(DispatchHistoryTrend, { props: {
    trend, filters: period, selectedDay: '', loading: false, error: '',
    machineIps: [], machineName: () => '', disabled: false, ...overrides,
  }, global: { components: { AppDatePicker } } })
  t.onTestFinished(() => wrapper.unmount())
  return { clicks, canvasClicks, chart, wrapper }
}

test('completion-only chart bars, dates and blank column areas open completed records', async t => {
  const trend = { ...period, timezone: 'Asia/Shanghai', machine_ips: [],
    items: [{ date: '2026-11-02', sent_count: 0, completed_count: 3 }] }
  const page = state(), requests = []
  page.trend.value = trend
  globalThis.fetch = async value => {
    const url = new URL(value, 'http://localhost'); requests.push(url)
    return response({ page: 1, total: 1, items: [{ dispatch_id: 'previously-dispatched-task' }] })
  }
  const chart = mountTrend(t, trend, { onSelectDay: (...args) => page.selectHistoryDay(...args) })
  for (const click of [
    () => chart.clicks.click({ componentType: 'series', seriesType: 'bar', seriesName: '加工完成', name: '2026-11-02' }),
    () => chart.clicks.click({ componentType: 'xAxis', value: '2026-11-02' }),
    () => chart.canvasClicks.click({ offsetX: 100, offsetY: 100 }),
  ]) {
    click()
    await flushPromises()
    assert.equal(requests.at(-1).searchParams.get('date_type'), 'completed')
    assert.equal(requests.at(-1).searchParams.get('start_date'), '2026-11-02')
    assert.equal(page.histories.value[0].dispatch_id, 'previously-dispatched-task')
  }
  assert.deepEqual(chart.wrapper.emitted('select-day'), Array(3).fill(['2026-11-02', 'completed']))
})

test('chart selection preserves sent bars and skips busy, out-of-grid and duplicate canvas clicks', async t => {
  const chart = mountTrend(t, { ...period, timezone: 'Asia/Shanghai', machine_ips: [],
    items: [{ date: '2026-11-02', sent_count: 5, completed_count: 3 }] })
  chart.clicks.click({ componentType: 'series', seriesType: 'bar', seriesName: '成功派发', name: '2026-11-02' })
  chart.clicks.click({ componentType: 'xAxis', value: '2026-11-02' })
  assert.deepEqual(chart.wrapper.emitted('select-day'), [['2026-11-02', 'sent'], ['2026-11-02', 'sent']])
  chart.canvasClicks.click({ target: {}, offsetX: 100, offsetY: 100 })
  chart.chart.containPixel = () => false
  chart.canvasClicks.click({ offsetX: 100, offsetY: 100 })
  await chart.wrapper.setProps({ disabled: true })
  chart.clicks.click({ componentType: 'xAxis', value: '2026-11-02' })
  assert.equal(chart.wrapper.emitted('select-day').length, 2)
})

test('default trend requests seven inclusive Shanghai days and clearing restores that range', async t => {
  vi.useFakeTimers({ toFake: ['Date'], now: new Date('2026-10-31T16:30:00Z') })
  const calls = []
  globalThis.fetch = async value => {
    const url = new URL(value, 'http://localhost')
    calls.push(url)
    return response(dataFor(url))
  }
  const page = state()
  const expected = { start_date: '2026-10-26', end_date: '2026-11-01' }
  assert.deepEqual(page.trendFilters.value, expected)
  await page.refreshHistory()
  const query = calls.find(url => url.pathname.endsWith('/trend')).searchParams
  assert.equal(query.get('start_date'), expected.start_date)
  assert.equal(query.get('end_date'), expected.end_date)
  await page.applyHistoryFilters(period)
  assert.deepEqual(page.trendFilters.value, period)
  await page.clearHistoryFilters()
  assert.deepEqual(page.trendFilters.value, expected)
  const reset = calls.findLast(url => url.pathname.endsWith('/trend')).searchParams
  assert.equal(reset.get('start_date'), expected.start_date)
  assert.equal(reset.get('end_date'), expected.end_date)
})

test('trend click and machine/date filters reuse history pagination, and clearing restores all records', async () => {
  const calls = []
  globalThis.fetch = async value => {
    const url = new URL(value, 'http://localhost')
    calls.push(url)
    return response(dataFor(url))
  }
  const page = state()
  await page.refreshHistory()
  assert.equal(page.histories.value[0].dispatch_id, 'all')
  assert.match(page.historySelectionLabel.value, /全部派发记录/)
  page.historyPage.value = 2
  await page.applyHistoryFilters(period)
  assert.equal(page.historyPage.value, 1)
  assert.equal(page.histories.value[0].dispatch_id, '2026-11-01')
  await page.selectHistoryDay('2026-11-02')
  assert.equal(page.selectedHistoryDay.value, '2026-11-02')
  assert.match(page.historySelectionLabel.value, /2026-11-02 成功派发记录/)
  await page.changeHistoryPage(2)
  const last = calls.at(-1).searchParams
  assert.equal(last.get('page'), '2')
  assert.equal(last.get('start_date'), '2026-11-02')
  assert.equal(last.get('end_date'), '2026-11-02')
  assert.equal(last.get('machine_ip'), '10.0.0.2')
  const count = calls.length
  await page.selectHistoryDay('2099-01-01')
  assert.equal(calls.length, count)
  await page.clearHistoryFilters()
  assert.equal(page.selectedHistoryDay.value, '')
  assert.equal(page.histories.value[0].dispatch_id, 'all')
  const unfiltered = calls.findLast(url => !url.pathname.endsWith('/trend')).searchParams
  assert.equal(unfiltered.has('start_date'), false)
  assert.equal(unfiltered.has('machine_ip'), false)
})

test('zero-count days can be selected and trend failure cannot block the history table', async () => {
  let failTrend = false
  globalThis.fetch = async value => {
    const url = new URL(value, 'http://localhost')
    if (url.pathname.endsWith('/trend') && failTrend) throw Error('趋势暂不可用')
    const data = dataFor(url)
    if (!url.pathname.endsWith('/trend') && url.searchParams.get('end_date') === '2026-11-01') {
      data.items = []; data.total = 0
    }
    return response(data)
  }
  const page = state()
  await page.applyHistoryFilters(period)
  await page.selectHistoryDay('2026-11-01')
  assert.equal(page.selectedHistoryDay.value, '2026-11-01')
  assert.equal(page.historyTotal.value, 0)
  failTrend = true
  await page.applyHistoryFilters(period)
  assert.equal(page.trendError.value, '趋势暂不可用')
  assert.equal(page.historyTotal.value, 12)
  assert.equal(page.historyError.value, '')
})

test('late chart and detail responses cannot overwrite changed filters or a paused page', async () => {
  const pending = []
  globalThis.fetch = (value, options) => {
    const url = new URL(value, 'http://localhost')
    if (url.pathname.includes('/requests/')) return Promise.resolve(response({}))
    if (url.searchParams.get('machine_ip') !== '10.0.0.2') {
      return new Promise(resolve => pending.push({ url, signal: options.signal, resolve }))
    }
    return Promise.resolve(response(dataFor(url)))
  }
  const page = state()
  const initial = page.refreshHistory()
  assert.equal(pending.length, 2)
  await page.applyHistoryFilters(period)
  assert.ok(pending.every(item => item.signal.aborted))
  pending.forEach(item => item.resolve(response(item.url.pathname.endsWith('/trend')
    ? { ...dataFor(item.url), items: [{ date: '1999-01-01', sent_count: 99, completed_count: 0 }] }
    : { page: 9, items: [{ dispatch_id: 'late' }], total: 99 })))
  await initial
  assert.equal(page.trend.value.items[0].date, '2026-11-01')
  assert.equal(page.histories.value[0].dispatch_id, '2026-11-01')
  page.busy.value = true
  await page.clearHistoryFilters()
  assert.equal(page.trendFilters.value.machine_ip, '10.0.0.2')
  page.busy.value = false
  globalThis.fetch = (value, options) => {
    if (String(value).includes('/requests/')) return Promise.resolve(response({}))
    return new Promise(resolve => pending.push({ signal: options.signal, resolve }))
  }
  const refreshing = page.refreshHistory()
  page.pauseHistory()
  assert.ok(pending.slice(-2).every(item => item.signal.aborted))
  pending.at(-2).resolve(response({ page: 5, total: 50, items: [] }))
  pending.at(-1).resolve(response({ items: [] }))
  await refreshing
  assert.equal(page.historyPage.value, 1)
  assert.equal(page.trend.value.items[0].date, '2026-11-01')
})

test('a failed filter request clears old rows so they cannot be mistaken for the new selection', async () => {
  const page = state()
  page.histories.value = [{ dispatch_id: 'old-selection' }]
  globalThis.fetch = async () => { throw Error('网络中断') }
  await page.applyHistoryFilters(period)
  assert.deepEqual(page.histories.value, [])
  assert.equal(page.historyError.value, '网络中断')
  assert.equal(page.historyLoading.value, false)
})

test('actual trend component renders selection, zero, loading and failure states', async t => {
  const props = { trend: { ...period, items: days.map(item => ({ ...item, sent_count: 0, completed_count: 0 })), machine_ips: ['10.0.0.2'], timezone: 'Asia/Shanghai' },
    filters: period, loading: false, error: '', selectedDay: '2026-11-02', machineIps: ['10.0.0.2'], machineName: () => '测试机床', disabled: false }
  const { wrapper } = mountTrend(t, props.trend, props)
  for (const value of ['每日派发与完成趋势', '测试机床', '2026-11-02', '所选区间暂无派发或完成记录', '清除筛选 / 全部记录']) assert.ok(wrapper.text().includes(value), value)
  await wrapper.setProps({ trend: null, loading: true })
  assert.match(wrapper.text(), /正在读取派发趋势/)
  await wrapper.setProps({ loading: false, error: '离线测试错误' })
  assert.match(wrapper.text(), /趋势读取失败.*离线测试错误.*重试/)
  await wrapper.findAll('button').find(button => button.text() === '重试').trigger('click')
  assert.equal(wrapper.emitted('retry').length, 1)
})

test('history success time displays the same configured day as the trend and filters', async () => {
  const props = { histories: [{ dispatch_id: 'utc-success', project_id: 'P1', ship_no: 'S1', segment_name: '806C',
    document_no: 'one.GEN', quantity: 1, machine_ip: '10.0.0.1', status: 'sent', status_label: '已派发',
    stage_label: '完成', created_at: '2026-11-01T08:00:00+08:00', sent_at: '2026-11-01T16:30:00+00:00' }],
    historyTotal: 1, historyPage: 1, historyPageSize: 10, projectOptions: [], machineName: () => '测试机床', loading: false, timezone: 'Asia/Shanghai' }
  const html = await renderComponent('components/dispatch/DispatchHistoryTable', props)
  assert.ok(html.includes('成功派发时间'))
  assert.ok(html.includes('2026年11月02日 00:30:00'))
})

for (const [value, timezone, expected] of [
  ['2026-11-06T00:47:03+08:00', 'Asia/Shanghai', '2026年11月06日 00:47:03'],
  ['2026-11-05T16:30:00Z', 'Asia/Shanghai', '2026年11月06日 00:30:00'],
  ['2026-11-06T00:47:03+08:00', 'America/Los_Angeles', '2026年11月05日 08:47:03'],
  [null, 'Asia/Shanghai', '--'],
  [undefined, 'Asia/Shanghai', '--'],
  ['bad-time', 'Asia/Shanghai', '--'],
]) test(`completion time column renders ${String(value)} in ${timezone}`, async () => {
  const html = await renderComponent('components/dispatch/DispatchHistoryTable', {
    histories: [{ dispatch_id: 'time-column', project_id: 'P1', document_no: 'S409P120A601.gen',
      quantity: 1, machine_ip: '10.147.1.40', status: 'sent', status_label: '已派发',
      created_at: '2026-11-05T08:00:00+08:00', processing_finished_at: value }],
    historyTotal: 1, historyPage: 1, historyPageSize: 10, projectOptions: [], machineName: () => '切割机', loading: false, timezone,
  })
  assert.ok(html.includes('加工完成时间'))
  const cells = [...html.matchAll(/<td\b[^>]*>([\s\S]*?)<\/td>/g)]
  assert.ok(cells.at(-1)?.[1].includes(expected), 'the completion timestamp is visible in its own table cell')
})

test('completed-day selection keeps its own date filter while the existing sent-day selection remains available', async () => {
  const calls = []
  globalThis.fetch = async value => {
    const url = new URL(value, 'http://localhost'); calls.push(url)
    return response(dataFor(url))
  }
  const page = state()
  await page.applyHistoryFilters(period)
  await page.selectHistoryDay('2026-11-02', 'completed')
  assert.equal(calls.at(-1).searchParams.get('date_type'), 'completed')
  assert.equal(page.historyDateType.value, 'completed')
  assert.match(page.historySelectionLabel.value, /2026-11-02 加工完成记录/)
  await page.changeHistoryPage(2)
  assert.equal(calls.at(-1).searchParams.get('date_type'), 'completed')
  await page.selectHistoryDay('2026-11-02')
  assert.equal(calls.at(-1).searchParams.get('date_type'), 'sent')
})

test('visible history and trend refresh silently once per tick, preserving filters and stopping on pause', async () => {
  vi.useFakeTimers()
  const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(false)
  const page = state(), calls = []
  globalThis.fetch = async value => {
    calls.push(new URL(value,'http://localhost'))
    assert.equal(page.historyLoading.value,false)
    assert.equal(page.trendLoading.value,false)
    return response(dataFor(calls.at(-1)))
  }
  page.historyPage.value = 2
  page.startHistoryPolling(); page.startHistoryPolling()
  assert.equal(vi.getTimerCount(),1)
  const tick = () => vi.advanceTimersByTimeAsync(5000)
  await tick()
  assert.equal(calls.length,2)
  assert.equal(calls.find(url=>!url.pathname.endsWith('/trend')).searchParams.get('page'),'2')
  assert.equal(vi.getTimerCount(),1)
  hidden.mockReturnValue(true); await tick(); assert.equal(calls.length,2)
  hidden.mockReturnValue(false); page.busy.value=true; await tick(); assert.equal(calls.length,2)
  page.busy.value=false
  page.pauseHistory(); assert.equal(vi.getTimerCount(),0)
})

test('trend date selector exposes separate completed counts including days with only completions', t => {
  const trend = { ...period, timezone: 'Asia/Shanghai', machine_ips: [],
    items: [{ date: '2026-11-02', sent_count: 0, completed_count: 3 }] }
  const { wrapper } = mountTrend(t, trend, { selectedDay: '2026-11-02', selectedDateType: 'completed' })
  assert.match(wrapper.text(), /完成 3 条/)
  assert.doesNotMatch(wrapper.text(), /所选区间暂无派发或完成记录/)
})

test('completed badge shows its finish time in the same configured timezone as the chart', async () => {
  const html = await renderComponent('components/dispatch/DispatchHistoryTable', {
    histories:[{dispatch_id:'finished',project_id:'P1',document_no:'A.gen',quantity:1,machine_ip:'10.0.0.1',
      status:'sent',status_label:'已派发',stage_label:'推送完成',created_at:'2026-11-01T08:00:00+08:00',
      processing_status:'completed',processing_finished_at:'2026-11-05T00:30:00+08:00',processing_finish_by:'切割机(10.0.0.1)'}],
    historyTotal:1,historyPage:1,historyPageSize:10,projectOptions:[],machineName:()=> '切割机',loading:false,timezone:'America/Los_Angeles',
  })
  assert.ok(html.includes('已派发') && html.includes('已完成'))
  assert.ok(html.includes('2026年11月04日 08:30:00'))
})
