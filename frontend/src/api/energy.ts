import type { EnergyDayReport, EnergyMonthReport, EnergyPeriodReport, EnergyRangeReport, TaskEnergyReport } from '../types/energy'
import type { ReadOptions } from '../types/api'
import { reportCache } from './reportCache.ts'
import { readReport } from './reportRequests.ts'
import { reportToday } from './reportDates.ts'
import { request } from './client.ts'


export function getEnergyRange(startDate: string, endDate: string, { force = false, signal, mode, onPreparing }: ReadOptions = {}) {
  const query = new URLSearchParams({ start_date: startDate, end_date: endDate })
  const key = `energy:range:${query}`
  return readReport(key, (sharedSignal, refresh, lineId) => {
    const selected = new URLSearchParams(query)
    if (refresh) selected.set('refresh', 'true')
    return request<EnergyRangeReport>(`/api/v1/energy-report/range?${selected}`, { signal: sharedSignal, headers: { 'X-Production-Line': lineId } })
  }, { signal, mode: mode ?? (force ? 'refresh' : 'read'), onPreparing })
}

export function getEnergyDay(reportDate = '', { force = false, signal, mode, onPreparing }: ReadOptions = {}) {
  reportDate ||= reportToday()
  const query = new URLSearchParams()
  if (reportDate) query.set('report_date', reportDate)
  return readReport(`energy:day:${reportDate}`, async (sharedSignal, refresh, lineId) => {
    const selected = new URLSearchParams(query)
    if (refresh) selected.set('refresh', 'true')
    const data = await request<EnergyDayReport>(`/api/v1/energy-report/day?${selected}`, { signal: sharedSignal, headers: { 'X-Production-Line': lineId } })
    if (!sharedSignal.aborted) reportCache.invalidate(`energy:month:${reportDate.slice(0, 7)}`)
    if (!sharedSignal.aborted) reportCache.invalidate('energy:monthly-range:')
    return data
  }, { signal, mode: mode ?? (force ? 'refresh' : 'read'), onPreparing })
}

export function getTaskEnergyDay(reportDate = '', { force = false, signal, mode, onPreparing }: ReadOptions = {}) {
  reportDate ||= reportToday()
  const query = new URLSearchParams()
  if (reportDate) query.set('report_date', reportDate)
  return readReport(`energy:tasks:${reportDate}`, (sharedSignal, refresh, lineId) => {
    const selected = new URLSearchParams(query)
    if (refresh) selected.set('refresh', 'true')
    return request<TaskEnergyReport>(`/api/v1/energy-report/tasks?${selected}`, { signal: sharedSignal, headers: { 'X-Production-Line': lineId } })
  }, { signal, mode: mode ?? (force ? 'refresh' : 'read'), onPreparing })
}

export function getEnergyMonth(reportMonth: string, { force = false, signal, mode, onPreparing }: ReadOptions = {}) {
  const query = new URLSearchParams({ report_month: reportMonth })
  return readReport(`energy:month:${reportMonth}`, (sharedSignal, refresh, lineId) => {
    const selected = new URLSearchParams(query)
    if (refresh) selected.set('refresh', 'true')
    return request<EnergyMonthReport>(`/api/v1/energy-report/month?${selected}`, { signal: sharedSignal, headers: { 'X-Production-Line': lineId } })
  }, { signal, mode: mode ?? (force ? 'refresh' : 'read'), onPreparing })
}

export function getEnergyMonthlyRange(startDate: string, endDate: string, { force = false, signal, mode, onPreparing }: ReadOptions = {}) {
  const query = new URLSearchParams({ start_date: startDate, end_date: endDate })
  const key = `energy:monthly-range:${query}`
  return readReport(key, (sharedSignal, refresh, lineId) => {
    const selected = new URLSearchParams(query)
    if (refresh) selected.set('refresh', 'true')
    return request<EnergyPeriodReport>(`/api/v1/energy-report/monthly-range?${selected}`, { signal: sharedSignal, headers: { 'X-Production-Line': lineId } })
  }, { signal, mode: mode ?? (force ? 'refresh' : 'read'), onPreparing })
}
