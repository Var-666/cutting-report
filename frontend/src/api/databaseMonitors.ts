import { request } from './client.ts'
import type { ReadOptions } from '../types/api'
import type {
  DatabaseMonitorInput, DatabaseMonitorList, PageDatabaseMonitorIncident,
  DatabaseMonitorSaved, DatabaseMonitorDeleted,
} from './generated/types.gen'

export function listDatabaseMonitors({ signal }: ReadOptions = {}) {
  return request<DatabaseMonitorList>('/api/v1/database-monitors', { signal })
}

export function saveDatabaseMonitor(item: DatabaseMonitorInput, id?: string) {
  return request<DatabaseMonitorSaved>(`/api/v1/database-monitors${id ? `/${encodeURIComponent(id)}` : ''}`, {
    method: id ? 'PUT' : 'POST', body: JSON.stringify(item),
  })
}

export function deleteDatabaseMonitor(id: string) {
  return request<DatabaseMonitorDeleted>(`/api/v1/database-monitors/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

export function listDatabaseMonitorIncidents(page = 1, { signal }: ReadOptions = {}) {
  return request<PageDatabaseMonitorIncident>(`/api/v1/database-monitors/incidents/page?page=${page}&page_size=15`, { signal })
}
