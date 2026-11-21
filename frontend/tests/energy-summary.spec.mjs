// @vitest-environment jsdom

import { getEnergyMonthlyRange } from '../src/api/energy'
import { REPORT_POLL_INTERVAL } from '../src/api/reportRequests'
import { useEnergyReportSummary } from '../src/composables/useEnergyReportSummary'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { ref } from 'vue'

vi.mock('../src/api/energy')
beforeEach(() => {
  vi.useFakeTimers()
  getEnergyMonthlyRange.mockReset()
})
afterEach(() => { vi.useRealTimers() })

const date = '2026-10-27'
const snapshot = id => ({ id, rows: [{ report_date: '2026-10' }], syncing: true, missing_day_count: 2 })
const tick = () => vi.advanceTimersByTimeAsync(REPORT_POLL_INTERVAL)

test('monthly summary catches up once per interval and stops after completion and reactivation', async () => {
  const active = ref(true)
  const summary = useEnergyReportSummary(active, date)
  const initial = snapshot('first')
  const completed = { ...snapshot('complete'), syncing: false, missing_day_count: 0 }
  let finish
  getEnergyMonthlyRange.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  await summary.applyMonthlySummary(initial)
  active.value = false
  summary.pauseSummary()
  await tick()
  expect(getEnergyMonthlyRange).not.toHaveBeenCalled()
  active.value = true
  summary.scheduleSummaryRefresh(initial)
  summary.scheduleSummaryRefresh(initial)
  await tick()
  summary.scheduleSummaryRefresh(initial)
  await tick()
  expect(getEnergyMonthlyRange.mock.calls).toEqual([['2025-11-01', date, { mode: 'poll', signal: expect.any(AbortSignal) }]])
  finish(completed)
  await vi.advanceTimersByTimeAsync(0)
  expect(summary.monthlyReport.value).toEqual(completed)
  summary.pauseSummary()
  summary.scheduleSummaryRefresh(completed)
  await tick()
  expect(getEnergyMonthlyRange).toHaveBeenCalledOnce()
})

test('failed monthly checks retain data and require a deliberate refresh to resume', async () => {
  const active = ref(true)
  const summary = useEnergyReportSummary(active, date)
  const initial = snapshot('retained')
  getEnergyMonthlyRange.mockRejectedValueOnce(new Error('meter unavailable'))
  await summary.applyMonthlySummary(initial)
  await tick()
  expect(summary.monthlyReport.value).toEqual(initial)
  expect(summary.summaryRefreshError.value).toBe('meter unavailable')
  active.value = false
  summary.pauseSummary()
  active.value = true
  summary.scheduleSummaryRefresh(initial)
  await tick()
  expect(getEnergyMonthlyRange).toHaveBeenCalledOnce()
  await summary.applyMonthlySummary(initial, true)
  expect(summary.summaryRefreshError.value).toBe('')
  getEnergyMonthlyRange.mockResolvedValue({ ...initial, syncing: false })
  await tick()
  expect(getEnergyMonthlyRange).toHaveBeenCalledTimes(2)
})

test('75 monthly checks exhaust the budget across hiding and reopening the report', async () => {
  const active = ref(true)
  const summary = useEnergyReportSummary(active, date)
  const initial = snapshot('retained')
  getEnergyMonthlyRange.mockResolvedValue(initial)
  await summary.applyMonthlySummary(initial)
  await vi.advanceTimersByTimeAsync(REPORT_POLL_INTERVAL * 76)
  expect(getEnergyMonthlyRange).toHaveBeenCalledTimes(75)
  active.value = false
  summary.pauseSummary()
  active.value = true
  summary.scheduleSummaryRefresh(initial)
  await tick()
  expect(getEnergyMonthlyRange).toHaveBeenCalledTimes(75)
})

test('changing the summary scope and pausing both abort running checks and reject late data', async () => {
  const active = ref(true)
  const summary = useEnergyReportSummary(active, date)
  const pending = []
  getEnergyMonthlyRange.mockImplementation((start, end, { signal }) =>
    new Promise(resolve => pending.push({ start, end, signal, resolve })))
  await summary.applyMonthlySummary(snapshot('old-scope'))
  await tick()
  summary.resetSummaryScope('2026-09-15')
  expect(pending[0].signal.aborted).toBe(true)
  expect(summary.monthlyReport.value).toBeNull()
  await summary.applyMonthlySummary(snapshot('current-scope'), true)
  pending[0].resolve({ ...snapshot('obsolete'), syncing: false })
  await tick()
  expect(summary.monthlyReport.value.id).toBe('current-scope')
  expect(pending[1]).toMatchObject({ start: '2025-10-01', end: '2026-09-15' })
  active.value = false
  summary.pauseSummary()
  expect(pending[1].signal.aborted).toBe(true)
  pending[1].resolve(snapshot('after-leave'))
  await tick()
  expect(summary.monthlyReport.value.id).toBe('current-scope')
  expect(getEnergyMonthlyRange).toHaveBeenCalledTimes(2)
})
