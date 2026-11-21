// @vitest-environment jsdom

import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { ref } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { getLeadershipRange, getLeadershipReport } from '../src/api/cutting'
import { REPORT_POLL_INTERVAL } from '../src/api/reportRequests'
import { useLeadershipReportQuery } from '../src/composables/useLeadershipReportQuery'

vi.mock('../src/api/cutting')
const calendar = vi.hoisted(() => ({ today: '2026-10-27' }))
vi.mock('../src/api/reportDates', async importOriginal => ({
  ...await importOriginal(), reportToday: () => calendar.today,
}))
beforeEach(() => {
  vi.useFakeTimers()
  calendar.today = '2026-10-27'
  getLeadershipReport.mockReset()
  getLeadershipRange.mockReset()
})
afterEach(() => { vi.useRealTimers() })

const date = '2026-10-27'
const report = (id, refreshing = false) => ({ id, snapshot: { data_as_of: date, refreshing }, daily_records: [], monthly_records: [] })
const tick = () => vi.advanceTimersByTimeAsync(REPORT_POLL_INTERVAL)

async function prepare(monthly = report('initial', true)) {
  getLeadershipReport.mockResolvedValueOnce(monthly)
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { name: 'leadership-report', path: '/leadership', component: { render: () => null } },
  ] })
  await router.push('/leadership')
  const pageActive = ref(true)
  const query = useLeadershipReportQuery({
    route: router.currentRoute.value, router, today: date, routeRange: null, pageActive, renderCharts() {},
  })
  await query.loadDefaultReport()
  return { query, pageActive, router }
}

test('a cancelled snapshot timer cannot take over a foreground query even for the same period', async () => {
  const clock = vi.spyOn(window, 'setTimeout')
  const { query } = await prepare()
  const stale = clock.mock.calls.findLast(([, delay]) => delay === REPORT_POLL_INTERVAL)[0]
  let finish
  getLeadershipRange.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  const pending = query.loadReport()
  await stale()
  expect(getLeadershipReport).toHaveBeenCalledOnce()
  expect(getLeadershipRange).toHaveBeenCalledOnce()
  expect(getLeadershipRange.mock.lastCall[2].signal.aborted).toBe(false)
  finish(report('foreground'))
  await pending
  expect(query.report.value.id).toBe('foreground')
  expect(query.refreshLoading.value).toBe(false)
  expect(query.loading.value).toBe(false)
})

test('a background refresh that resolves after the foreground query cannot replace its result', async () => {
  const { query } = await prepare()
  let finish
  getLeadershipReport.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  await tick()
  const [, { mode, signal }] = getLeadershipReport.mock.lastCall
  expect(mode).toBe('poll')
  query.periodStart.value = '2026-09-03'
  query.periodEnd.value = '2026-09-04'
  getLeadershipRange.mockResolvedValueOnce(report('new-period'))
  await query.loadReport()
  expect(signal.aborted).toBe(true)
  finish(report('obsolete', true))
  await vi.advanceTimersByTimeAsync(0)
  expect(query.report.value.id).toBe('new-period')
  expect(query.loading.value).toBe(false)
  expect(query.refreshLoading.value).toBe(false)
})

test('poll failure keeps the current snapshot; retry re-reads it and leaving aborts the next poll', async () => {
  const { query, pageActive } = await prepare()
  getLeadershipReport.mockRejectedValueOnce(new Error('refresh unavailable'))
  await tick()
  expect(query.error.value).toBe('refresh unavailable')
  expect(query.report.value.id).toBe('initial')
  expect(query.refreshLoading.value).toBe(false)
  getLeadershipReport.mockResolvedValueOnce(report('retry', true))
  await query.retryReport()
  expect(getLeadershipReport.mock.lastCall[1].force).toBe(true)
  expect(query.error.value).toBe('')
  let finish
  getLeadershipReport.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  await tick()
  const [, { signal }] = getLeadershipReport.mock.lastCall
  pageActive.value = false
  query.cancelReportRequest()
  expect(signal.aborted).toBe(true)
  finish(report('after-leave', true))
  await tick()
  expect(query.report.value.id).toBe('retry')
  expect(getLeadershipReport).toHaveBeenCalledTimes(4)
})

test('a cached leadership report keeps its data through failure and follows the new month on recovery', async () => {
  const retained = report('cached')
  const { query } = await prepare(retained)
  let reject
  getLeadershipReport.mockImplementationOnce(() => new Promise((resolve, fail) => { reject = fail }))
  const refresh = query.refreshCachedReport()
  expect(query.loading.value).toBe(true)
  expect(query.report.value).toEqual(retained)
  reject(new Error('后台读取失败'))
  await refresh
  expect(query.report.value).toEqual(retained)
  expect(query.error.value).toBe('后台读取失败')
  expect(query.loading.value).toBe(false)
  calendar.today = '2026-11-01'
  getLeadershipReport.mockResolvedValueOnce(report('next-month'))
  await query.refreshCachedReport()
  expect(getLeadershipReport.mock.lastCall[0]).toBe('2026-11')
  expect(query.appliedPeriodStart.value).toBe('2026-11-01')
  expect(query.appliedPeriodEnd.value).toBe('2026-11-01')
  expect(query.monthlyPeriodStart.value).toBe('2025-12-01')
  expect(query.monthlyPeriodEnd.value).toBe('2026-11-01')
  expect(query.report.value.id).toBe('next-month')
})

test('after a cold failure, retry requests a fresh monthly snapshot', async () => {
  getLeadershipReport.mockRejectedValueOnce(new Error('cold source failed'))
  const { query } = await prepare(report('recovered'))
  expect(query.report.value).toBeNull()
  expect(query.error.value).toBe('cold source failed')
  await query.retryReport()
  expect(getLeadershipReport.mock.lastCall[1].force).toBe(true)
  expect(query.report.value.id).toBe('recovered')
  expect(query.error.value).toBe('')
})
