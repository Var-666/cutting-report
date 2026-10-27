import type { AlarmIncidentPage, AlarmReport, AlarmReportPage, AlarmIncident, AlarmStatus } from '../types/alarms'
import type { ReadOptions } from '../types/api'
import { request } from './client.ts'

export function getAlarmStatus({ signal }: ReadOptions = {}) {
  return request<AlarmStatus>('/api/v1/alarms/status', { signal })
}

export function listAlarmIncidents({ status = '', limit = 100 } = {}, { signal }: ReadOptions = {}) {
  const query = new URLSearchParams({ limit: String(limit) })
  if (status) query.set('status', status)
  return request<AlarmIncident[]>(`/api/v1/alarms/incidents?${query}`, { signal })
}

export function listAlarmIncidentPage({ status = '', severity = '', page = 1, pageSize = 15 } = {}, { signal }: ReadOptions = {}) {
  const query = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
  if (status) query.set('status', status)
  if (severity) query.set('severity', severity)
  return request<AlarmIncidentPage>(`/api/v1/alarms/incidents/page?${query}`, { signal })
}

export function listAlarmReports({ limit = 50 } = {}, { signal }: ReadOptions = {}) {
  const query = new URLSearchParams({ limit: String(limit) })
  return request<AlarmReport[]>(`/api/v1/alarms/reports?${query}`, { signal })
}

export function listAlarmReportPage({ page = 1, pageSize = 10 } = {}, { signal }: ReadOptions = {}) {
  const query = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
  return request<AlarmReportPage>(`/api/v1/alarms/reports/page?${query}`, { signal })
}
