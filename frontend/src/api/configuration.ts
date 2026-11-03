import type { DatabaseConfigurationRequestWritable as DatabaseConfigurationRequest, DatabaseConfiguration as DatabaseConfigurationData, DatabasePreview, ValidatedWorkbenchSql, WorkbenchQueryResult, ConnectionTestRequestWritable as ConnectionTestRequest, WeldingConnectionRequestWritable as WeldingConnectionRequest } from './generated/types.gen'
import type { ConfigurationDiagnostics, ConnectionTestResult, DatabasePreviewRequest, DatabaseStatus, SaveResult } from '../types/configuration'
import type { ReadOptions } from '../types/api'
import { reportCache } from './reportCache.ts'
import { request } from './client.ts'

export function getDatabaseStatus() {
  return request<DatabaseStatus>('/api/v1/database/status')
}

export function getDatabaseConfiguration() {
  return request<DatabaseConfigurationData>('/api/v1/database/configuration')
}

export function saveDatabaseConfiguration(payload: DatabaseConfigurationRequest) {
  return request<SaveResult>('/api/v1/database/configuration', {
    method: 'PUT',
    body: JSON.stringify(payload),
  }).then((result) => {
    reportCache.clear()
    return result
  })
}

export function testDatabaseConnection(connection: ConnectionTestRequest) {
  return request<ConnectionTestResult>('/api/v1/database/test-connection', {
    method: 'POST',
    body: JSON.stringify(connection),
  })
}

export function previewDatabaseDataset(filters: DatabasePreviewRequest) {
  return request<DatabasePreview>('/api/v1/database/preview', {
    method: 'POST',
    body: JSON.stringify(filters),
  })
}

export function validateWorkbenchSql(sql: string) {
  return request<ValidatedWorkbenchSql>('/api/v1/database/sql/validate', {
    method: 'POST',
    body: JSON.stringify({ sql }),
  })
}

export function executeWorkbenchSql({ sql, maxRows, token }: { sql: string; maxRows: number; token: string }) {
  return request<WorkbenchQueryResult>('/api/v1/database/sql/execute', {
    method: 'POST',
    headers: token ? { 'X-SQL-Workbench-Token': token } : {},
    body: JSON.stringify({ sql, max_rows: maxRows }),
  })
}

export function testWeldingConnection(payload: WeldingConnectionRequest) { return request<ConnectionTestResult>('/api/v1/database/welding/test-connection', { method: 'POST', body: JSON.stringify(payload) }) }
export function saveWeldingConfiguration(payload: WeldingConnectionRequest) {
  return request<SaveResult>('/api/v1/database/welding/configuration', { method: 'PUT', body: JSON.stringify(payload) })
    .then(result => { reportCache.clear(); return result })
}

export function getConfigurationDiagnostics({ signal }: ReadOptions = {}) {
  return request<ConfigurationDiagnostics>('/api/v1/configuration/diagnostics', { signal })
}

// Additional sources share generated contracts; no arbitrary query API is exposed.
export function listDatabaseSources() {
  return request<import('./generated/types.gen').ItemsDatabaseSourceView>('/api/v1/database/sources')
}
export function getDatabaseSource(id: string) {
  return request<import('./generated/types.gen').DatabaseSourceView>(`/api/v1/database/sources/${encodeURIComponent(id)}`)
}
export function saveDatabaseSource(payload: import('./generated/types.gen').DatabaseSourceRequestWritable, create: boolean) {
  return request<import('./generated/types.gen').DatabaseSourceView>(create ? '/api/v1/database/sources' : `/api/v1/database/sources/${encodeURIComponent(payload.id)}`, {
    method: create ? 'POST' : 'PUT', body: JSON.stringify(payload),
  }).then(result => { reportCache.clear(); return result })
}
export function setDatabaseSourceEnabled(id: string, enabled: boolean) {
  return request<import('./generated/types.gen').DatabaseSourceView>(`/api/v1/database/sources/${encodeURIComponent(id)}/enabled`, {
    method: 'PATCH', body: JSON.stringify({ enabled }),
  }).then(result => { reportCache.clear(); return result })
}
export function deleteDatabaseSource(id: string) {
  return request<import('./generated/types.gen').SourceDeleted>(`/api/v1/database/sources/${encodeURIComponent(id)}`, { method: 'DELETE' }).then(result => { reportCache.clear(); return result })
}
export function testDatabaseSource(payload: import('./generated/types.gen').DatabaseSourceRequestWritable) {
  return request<ConnectionTestResult>('/api/v1/database/sources/test-connection', { method: 'POST', body: JSON.stringify(payload) })
}
