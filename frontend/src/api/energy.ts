import type { EnergyDayReport, EnergyMonthReport, EnergyPeriodReport, EnergyRangeReport, TaskEnergyReport } from '../types/energy'
import type { ReadOptions } from '../types/api'
import { reportCache } from './reportCache.ts'
import { reportToday } from './reportDates.ts'
import { request } from './client.ts'

const REPORT_VIEW_CACHE_TTL = 5 * 60 * 1000

export function getEnergyRange(startDate: string, endDate: string, { force = false, signal }: ReadOptions = {}) {
  const query = new URLSearchParams({ start_date: startDate, end_date: endDate })
  const key = `energy:range:${query}`
  if (force) query.set('refresh', 'true')
  return reportCache.read(key, (requestSignal) => request<EnergyRangeReport>(`/api/v1/energy-report/range?${query}`, { signal: requestSignal }), { ttl: REPORT_VIEW_CACHE_TTL, force, signal })
}

export function getEnergyDay(reportDate = '', { force = false, signal }: ReadOptions = {}) {
  reportDate ||= reportToday()
  const query = new URLSearchParams()
  if (reportDate) query.set('report_date', reportDate)
  if (force) query.set('refresh', 'true')
  return reportCache.read(`energy:day:${reportDate}`, async (signal) => {
    const data = await request<EnergyDayReport>(`/api/v1/energy-report/day?${query}`, { signal })
    if (!signal.aborted) reportCache.invalidate(`energy:month:${reportDate.slice(0, 7)}`)
    if (!signal.aborted) reportCache.invalidate('energy:monthly-range:')
    return data
  }, { ttl: REPORT_VIEW_CACHE_TTL, force, signal })
}

export function getTaskEnergyDay(reportDate = '', { force = false, signal }: ReadOptions = {}) {
  reportDate ||= reportToday()
  const query = new URLSearchParams()
  if (reportDate) query.set('report_date', reportDate)
  if (force) query.set('refresh', 'true')
  return reportCache.read(`energy:tasks:${reportDate}`, (signal) => request<TaskEnergyReport>(`/api/v1/energy-report/tasks?${query}`, { signal }), { ttl: REPORT_VIEW_CACHE_TTL, force, signal })
}

export function getEnergyMonth(reportMonth: string, { force = false, signal }: ReadOptions = {}) {
  const query = new URLSearchParams({ report_month: reportMonth })
  return reportCache.read(`energy:month:${reportMonth}`, (signal) => request<EnergyMonthReport>(`/api/v1/energy-report/month?${query}`, { signal }), { ttl: REPORT_VIEW_CACHE_TTL, force, signal })
}

export function getEnergyMonthlyRange(startDate: string, endDate: string, { force = false, signal }: ReadOptions = {}) {
  const query = new URLSearchParams({ start_date: startDate, end_date: endDate })
  const key = `energy:monthly-range:${query}`
  return reportCache.read(key, (requestSignal) => request<EnergyPeriodReport>(`/api/v1/energy-report/monthly-range?${query}`, { signal: requestSignal }), { ttl: REPORT_VIEW_CACHE_TTL, force, signal })
}
