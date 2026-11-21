// @vitest-environment jsdom

import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { h, KeepAlive } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { getEnergyDay, getEnergyMonthlyRange, getEnergyRange, getTaskEnergyDay } from '../src/api/energy'
import { REPORT_POLL_INTERVAL } from '../src/api/reportRequests'
import { useEnergyReportQuery } from '../src/composables/useEnergyReportQuery'

vi.mock('../src/api/energy')
const calendar = vi.hoisted(() => ({ today: '2026-10-27' }))
vi.mock('../src/api/reportDates', async importOriginal => ({
  ...await importOriginal(), reportToday: () => calendar.today,
}))

const range = { start: '2026-09-01', end: '2026-09-02' }
const task = (id, refreshing = false) => ({ id, records: [], meta: { report_data: { refreshing } } })
const summary = { id: 'monthly', rows: [] }
const tick = () => vi.advanceTimersByTimeAsync(REPORT_POLL_INTERVAL)

enableAutoUnmount(afterEach)
beforeEach(() => {
  vi.resetAllMocks()
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  calendar.today = '2026-10-27'
  getEnergyDay.mockResolvedValue({})
  getEnergyMonthlyRange.mockResolvedValue(summary)
  getTaskEnergyDay.mockResolvedValue(task('daily'))
  getEnergyRange.mockImplementation(async (start, end) => ({
    task_report: { ...task('range'), period_start: start, period_end: end },
  }))
})
afterEach(() => { vi.useRealTimers() })

async function open(query = {}) {
  let report
  const page = { setup() {
    report = useEnergyReportQuery()
    return () => null
  } }
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/equipment', name: 'equipment-report', component: page },
    { path: '/other', component: { render: () => null } },
  ] })
  await router.push({ name: 'equipment-report', query })
  const wrapper = mount({
    render: () => h(RouterView, {}, { default: ({ Component }) => h(KeepAlive, {}, { default: () => Component }) }),
  }, { global: { plugins: [router] } })
  await flushPromises()
  return { report, router, wrapper }
}

test('month summary can finish independently, and a failed daily read can be retried explicitly', async () => {
  let fail
  getTaskEnergyDay.mockImplementationOnce(() => new Promise((resolve, reject) => { fail = reject }))
  const { report } = await open()
  expect(report.taskDay.value).toBeNull()
  expect(report.monthlyReport.value).toEqual(summary)
  expect(report.loading.value).toBe(true)
  fail(new Error('source offline'))
  await flushPromises()
  expect(report.error.value).toBe('source offline')
  await report.retryReport()
  expect(getTaskEnergyDay.mock.lastCall).toEqual(['2026-10-27', { force: true, signal: expect.any(AbortSignal) }])
  expect(report.taskDay.value.id).toBe('daily')
  expect(report.error.value).toBe('')
})

test('the default production date is today and midnight clears cached rows while loading the new day', async () => {
  const { report, router } = await open()
  expect(report.taskDay.value.period_end).toBe('2026-10-27')
  expect(report.taskDay.value.records).toEqual([])
  await router.push('/other')
  calendar.today = '2026-11-01'
  let finish
  getTaskEnergyDay.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  await router.push('/equipment')
  await flushPromises()
  expect(report.loading.value).toBe(true)
  expect(report.taskDay.value).toBeNull()
  expect(getTaskEnergyDay.mock.lastCall[0]).toBe('2026-11-01')
  finish(task('new-day'))
  await flushPromises()
  expect(report.taskDay.value).toMatchObject({ id: 'new-day', period_end: '2026-11-01', records: [] })
  expect(report.loading.value).toBe(false)
})

test('daily polls retain edited dates and abort a late read when the component leaves', async () => {
  getTaskEnergyDay.mockResolvedValueOnce(task('stale', true)).mockResolvedValueOnce(task('refreshed', true))
  const { report, wrapper } = await open()
  report.periodStart.value = '2026-09-15'
  await tick()
  expect(getTaskEnergyDay.mock.lastCall).toEqual(['2026-10-27', { mode: 'poll', signal: expect.any(AbortSignal) }])
  expect(report.taskDay.value.id).toBe('refreshed')
  expect(report.periodStart.value).toBe('2026-09-15')
  expect(report.appliedPeriodStart.value).toBe('2026-10-27')
  let finish
  getTaskEnergyDay.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  await tick()
  const [, { signal }] = getTaskEnergyDay.mock.lastCall
  wrapper.unmount()
  expect(signal.aborted).toBe(true)
  finish(task('late'))
  await tick()
  expect(report.taskDay.value.id).toBe('refreshed')
  expect(getTaskEnergyDay).toHaveBeenCalledTimes(3)
})

test('explicit range analysis refresh retains the applied dates, separately refreshes the monthly summary and polls the range', async () => {
  const { report } = await open(range)
  report.periodStart.value = '2026-09-15'
  getEnergyRange.mockResolvedValueOnce({ task_report: task('preparing', true) })
    .mockResolvedValueOnce({ task_report: task('updated') })
  await report.refreshAnalysisReport()
  expect(getEnergyRange.mock.lastCall).toEqual([range.start, range.end, { force: true, signal: expect.any(AbortSignal) }])
  expect(getEnergyMonthlyRange.mock.lastCall).toEqual(['2025-11-01', '2026-10-27', { force: true, signal: expect.any(AbortSignal) }])
  await tick()
  expect(getEnergyRange.mock.lastCall).toEqual([range.start, range.end, { mode: 'poll', signal: expect.any(AbortSignal) }])
  expect(report.taskDay.value.id).toBe('updated')
  expect(report.periodStart.value).toBe('2026-09-15')
  expect(report.appliedPeriodStart.value).toBe(range.start)
  await tick()
  expect(getEnergyRange).toHaveBeenCalledTimes(3)
})

test('reactivation retains same-day records through a failed refresh', async () => {
  const { report, router } = await open()
  let fail
  getTaskEnergyDay.mockImplementationOnce(() => new Promise((resolve, reject) => { fail = reject }))
  await router.push('/other')
  await router.push('/equipment')
  await flushPromises()
  expect(report.loading.value).toBe(true)
  expect(report.taskDay.value.id).toBe('daily')
  fail(new Error('后台读取失败'))
  await flushPromises()
  expect(report.taskDay.value.id).toBe('daily')
  expect(report.loading.value).toBe(false)
  expect(report.error.value).toBe('后台读取失败')
})
