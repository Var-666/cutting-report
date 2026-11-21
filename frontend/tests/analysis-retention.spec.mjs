// @vitest-environment jsdom

import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { KeepAlive, h, nextTick, ref } from 'vue'
import { resetDeviceContext, selectDevice, setDeviceCatalog } from '../src/api/deviceContext'
import AnalysisPanel from '../src/components/AnalysisPanel.vue'
import ReportAnalysis from '../src/components/ReportAnalysis.vue'
import ReportJobStatus from '../src/components/ReportJobStatus.vue'
import AutomationRunProgress from '../src/components/automation/AutomationRunProgress.vue'

const result = (time, overview = time) => ({
  status: 'success', mode: 'model', overview, facts: [], data_version: 'v1.v1', cached: true,
  generated_at: `2026-10-21T${time}:00+08:00`,
})
const data = value => Response.json({ success: true, data: value })
const failure = (detail, status = 502, headers) => Response.json({ detail }, { status, headers })

enableAutoUnmount(afterEach)
beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date', 'setInterval', 'clearInterval'] })
  setDeviceCatalog({ default_device_id: 'Cutter01', items: ['Cutter01', 'Cutter02'].map(id => ({ id })) })
})
afterEach(() => {
  vi.useRealTimers()
  resetDeviceContext()
})

function openPanel({ props: initial = {}, latest = () => data(null), generate = () => data(result('10:00')),
  version = () => data({ data_version: 'v1.v1' }) } = {}) {
  const props = ref({ reportType: 'workshop', reportDate: '2026-10-21', ready: true, dataVersion: 'v1', versionCheckEnabled: true, ...initial })
  const shown = ref(true)
  const requests = { latest: [], generate: [], version: [] }
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = new URL(String(input), 'http://localhost')
    if (url.pathname.endsWith('/cancel')) return data({})
    const request = { query: Object.fromEntries(url.searchParams), signal: init.signal }
    if (url.pathname === '/api/v1/analysis/latest') {
      requests.latest.push(request)
      return latest(request)
    }
    if (url.pathname === '/api/v1/analysis/version') {
      requests.version.push(request)
      return version(request)
    }
    if (url.pathname === '/api/v1/analysis' && init.method === 'POST') {
      request.body = JSON.parse(init.body)
      requests.generate.push(request)
      return generate(request)
    }
    throw new Error(`Unexpected request ${url.pathname}`)
  })
  const refreshed = vi.fn()
  const wrapper = mount({
    render: () => h(KeepAlive, {}, { default: () => shown.value ? h(AnalysisPanel, { ...props.value, onRefreshReport: refreshed }) : null }),
  })
  function button(label) {
    const found = wrapper.findAll('button').find(control => control.text() === label)
    if (!found) throw new Error(`Missing analysis action ${label}`)
    return found
  }
  return { wrapper, requests, refreshed, shown, button,
    change: async values => { props.value = { ...props.value, ...values }; await nextTick() },
    tick: async (ms = 10_000) => { await vi.advanceTimersByTimeAsync(ms); await flushPromises() },
  }
}

test('historical analysis keeps its reason, timestamp and update error visible', () => {
  const wrapper = mount(ReportAnalysis, {
    props: { analysis: { ...result('09:55', '已有成功分析'), cache_note: '沿用历史成功分析，原生成时间保持不变。' },
      error: '本次更新正在等待模型', ready: true, loading: false },
  })
  expect(wrapper.get('[title="沿用历史成功分析，原生成时间保持不变。"]').text()).toBe('历史统计')
  expect(wrapper.text()).toContain('09:55:00')
  expect(wrapper.get('[role="alert"]').text()).toContain('本次更新正在等待模型')
  expect(wrapper.text()).toContain('已有成功分析')
  expect(wrapper.text()).not.toContain('智能分析暂不可用')
})

test('visible analysis checks source versions every five minutes and stops when deactivated', async () => {
  let current = 'v1.v1'
  const page = openPanel({ latest: () => data(result('10:00')), version: () => data({ data_version: current }) })
  await flushPromises()
  expect(page.requests.latest).toHaveLength(1)
  await page.tick(299_999)
  expect(page.requests.version).toHaveLength(0)
  await page.tick(1)
  expect(page.requests.version[0].query).toMatchObject({ report_type: 'workshop', report_date: '2026-10-21' })
  expect(page.refreshed).not.toHaveBeenCalled()
  current = 'v2.v2'
  await page.tick(300_000)
  expect(page.requests.version).toHaveLength(2)
  expect(page.refreshed).toHaveBeenCalledOnce()
  expect(page.requests.generate).toHaveLength(0)
  const latestCount = page.requests.latest.length
  page.shown.value = false
  await nextTick()
  await page.tick(300_000)
  expect(page.requests.version).toHaveLength(2)
  expect(page.requests.latest).toHaveLength(latestCount)
})

test('a conflict asks the page to refresh the source and shows synchronization feedback', async () => {
  const page = openPanel({ generate: () => failure('数据版本已变化', 409) })
  await flushPromises()
  expect(page.refreshed).toHaveBeenCalledOnce()
  expect(page.wrapper.text()).toContain('报表数据已更新，正在同步后重新分析')
  page.shown.value = false
  await nextTick()
  expect(page.requests.generate).toHaveLength(1)
})

test('a pending version check is aborted when hidden and its late response cannot trigger refresh', async () => {
  let finish
  const page = openPanel({ latest: () => data(result('10:00')), version: () => new Promise(resolve => { finish = resolve }) })
  await flushPromises()
  await page.tick(300_000)
  expect(page.requests.version).toHaveLength(1)
  page.shown.value = false
  await nextTick()
  expect(page.requests.version[0].signal.aborted).toBe(true)
  finish(data({ data_version: 'v3.v3' }))
  await flushPromises()
  expect(page.refreshed).not.toHaveBeenCalled()
})

test('cached analysis is visible before the report loads and remains visible through a failed read and manual refresh', async () => {
  let latest = data(result('09:55', '上一份成功分析'))
  let finish
  const page = openPanel({
    props: { ready: false, prefetchLatest: true, dataVersion: '' },
    latest: () => latest,
    generate: () => new Promise(resolve => { finish = resolve }),
  })
  await flushPromises()
  expect(page.wrapper.text()).toContain('上一份成功分析')
  expect(page.requests.generate).toHaveLength(0)
  latest = data({ ...result('09:57'), status: 'failed' })
  await page.change({ ready: true, dataVersion: 'v2' })
  await flushPromises()
  expect(page.wrapper.text()).toContain('上一份成功分析')
  await page.button('重新分析').trigger('click')
  expect(page.button('重新分析').element.disabled).toBe(true)
  expect(page.wrapper.text()).toContain('上一份成功分析')
  expect(page.wrapper.text()).not.toContain('正在整理报表指标')
  expect(page.requests.generate[0].body).toMatchObject({ force: true, data_version: 'v2.v2' })
  finish(data({ ...result('10:05', '新分析'), data_version: 'v2.v2' }))
  await flushPromises()
  expect(page.wrapper.text()).toContain('新分析')
})

test('an empty prefetched cache does not generate before the report is ready', async () => {
  const page = openPanel({ props: { ready: false, prefetchLatest: true } })
  await flushPromises()
  await page.tick(30_000)
  expect(page.requests.latest).toHaveLength(1)
  expect(page.requests.generate).toHaveLength(0)
  await page.change({ ready: true })
  await flushPromises()
  expect(page.requests.generate).toHaveLength(1)
  expect(page.wrapper.text()).toContain('10:00')
})

test.each([true, false])('a default analysis poll replaces the old success without invoking the model again (initial cache=%s)', async cached => {
  let latest = cached ? result('09:55', '初次成功') : null
  const page = openPanel({ latest: () => data(latest), generate: () => data(result('09:55', '初次成功')) })
  expect(page.wrapper.text()).toContain('正在读取最近一次已完成的分析')
  await flushPromises()
  expect(page.wrapper.text()).toContain('初次成功')
  latest = result('10:00', '后台新结果')
  await page.tick()
  expect(page.wrapper.text()).toContain('后台新结果')
  expect(page.requests.latest).toHaveLength(2)
  expect(page.requests.generate).toHaveLength(cached ? 0 : 1)
})

test('retrying a failed cache read retains the prior success and only fetches the cache again', async () => {
  let offline = false
  const page = openPanel({ latest: () => offline ? failure('分析源离线') : data(result('09:55', '原分析')) })
  await flushPromises()
  offline = true
  await page.tick()
  expect(page.wrapper.get('[role="alert"]').text()).toContain('分析源离线')
  expect(page.wrapper.text()).toContain('原分析')
  offline = false
  await page.button('重试').trigger('click')
  await flushPromises()
  expect(page.wrapper.find('[role="alert"]').exists()).toBe(false)
  expect(page.requests.latest).toHaveLength(3)
  expect(page.requests.generate).toHaveLength(0)
})

test('an older background snapshot cannot replace a manual model refresh', async () => {
  const page = openPanel({ latest: () => data(result('09:55', '旧结果')), generate: () => data(result('10:01', '最新模型结果')) })
  await flushPromises()
  await page.button('重新分析').trigger('click')
  await flushPromises()
  await page.tick()
  expect(page.requests.generate.map(call => call.body.force)).toEqual([true])
  expect(page.wrapper.text()).toContain('最新模型结果')
  expect(page.wrapper.text()).not.toContain('旧结果')
})

test('switching devices or report dates cancels an old snapshot and ignores its late result', async () => {
  const pending = []
  const page = openPanel({ latest: () => new Promise(resolve => pending.push(resolve)) })
  await flushPromises()
  selectDevice('Cutter02')
  await flushPromises()
  expect(page.requests.latest[0].signal.aborted).toBe(true)
  expect(page.requests.latest[1].query.device_id).toBe('Cutter02')
  pending[1](data(result('10:00', '二号机分析')))
  pending[0](data(result('10:01', '一号机分析')))
  await flushPromises()
  expect(page.wrapper.text()).toContain('二号机分析')
  expect(page.wrapper.text()).not.toContain('一号机分析')
  await page.change({ reportDate: '2026-10-20' })
  expect(page.requests.latest[2].query.report_date).toBe('2026-10-20')
  expect(page.wrapper.text()).not.toContain('二号机分析')
  pending[2](data(null))
  await flushPromises()
})

test('an explicit range requests its own analysis, passing detail and summary versions without reading the default cache', async () => {
  let answer = result('10:00', '指定区间')
  const page = openPanel({
    props: { versionCheckEnabled: false, periodStart: '2026-10-02', periodEnd: '2026-10-03',
      summaryPeriodStart: '2025-11-01', summaryPeriodEnd: '2026-10-21', summaryDataVersion: 'month' },
    generate: () => data(answer),
  })
  await flushPromises()
  await page.tick()
  expect(page.requests.latest).toHaveLength(0)
  expect(page.requests.generate).toHaveLength(1)
  expect(page.requests.generate[0].body).toMatchObject({ force: false, report_type: 'workshop',
    period_start: '2026-10-02', period_end: '2026-10-03',
    summary_period_start: '2025-11-01', summary_period_end: '2026-10-21', data_version: 'v1.month' })
  expect(page.wrapper.text()).toContain('指定区间')
  answer = result('09:55', '与更正输入匹配的分析')
  await page.change({ dataVersion: 'v2' })
  await flushPromises()
  expect(page.wrapper.text()).toContain('与更正输入匹配的分析')
})

test.each([true, false])('a temporarily unavailable analysis respects the server retry delay (default=%s)', async defaults => {
  let unavailable = true
  const page = openPanel({
    props: { versionCheckEnabled: defaults },
    generate: () => unavailable ? failure('正在等待成功的智能分析结果', 503, { 'Retry-After': '60' }) : data(result('10:00', '恢复后的分析')),
  })
  await flushPromises()
  expect(page.requests.generate).toHaveLength(1)
  await page.tick(50_000)
  expect(page.requests.generate).toHaveLength(1)
  unavailable = false
  await page.tick()
  expect(page.requests.generate).toHaveLength(2)
  expect(page.wrapper.text()).toContain('恢复后的分析')
  await page.tick()
  expect(page.requests.generate).toHaveLength(2)
})

test('same-query updates and failures preserve the visible result but a different range clears it', async () => {
  let first = true
  let finish
  const page = openPanel({
    props: { versionCheckEnabled: false },
    generate: () => {
      if (!first) return new Promise(resolve => { finish = resolve })
      first = false
      return data(result('10:00', '此前结果'))
    },
  })
  await flushPromises()
  await page.change({ dataVersion: 'v2' })
  expect(page.wrapper.text()).toContain('此前结果')
  finish(failure('模型暂时不可用'))
  await flushPromises()
  expect(page.wrapper.text()).toContain('此前结果')
  expect(page.wrapper.get('[role="alert"]').text()).toContain('模型暂时不可用')
  await page.change({ periodStart: '2026-09-01', periodEnd: '2026-09-02' })
  expect(page.wrapper.text()).not.toContain('此前结果')
})

test('waiting report and automation progress are visible without download or mail completion', () => {
  const job = mount(ReportJobStatus, { props: { job: { report_id: 'waiting', report_type: 'leadership',
    status: 'pending', stage_message: '等待智能分析完成，系统将自动重试', ai_status: 'waiting' } } })
  expect(job.text()).toContain('等待智能分析完成')
  expect(job.text()).not.toMatch(/打开报告|下载 HTML|暂不可用/)
  const run = mount(AutomationRunProgress, { props: { execution: { schedule_id: 'schedule',
    schedule_name: '每日生产报告', status: 'analysis_wait' } } })
  expect(run.text()).toContain('等待智能分析成功，后台将自动重试')
  expect(run.text()).not.toMatch(/邮件已发送|执行失败/)
})
