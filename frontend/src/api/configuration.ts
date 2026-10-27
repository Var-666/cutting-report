import type { ConfigurationDiagnostics, ConnectionTestResult, DatabasePreviewRequest, DatabaseConfiguration, DatabaseStatus, SaveResult, SqlConnection, WeldingConnection } from '../types/configuration'
import type { ReadOptions } from '../types/api'
import { reportCache } from './reportCache.ts'
import { request } from './client.ts'

export function getDatabaseStatus() {
  return request<DatabaseStatus>('/api/v1/database/status')
}

export function getDatabaseConfiguration() {
  return request<DatabaseConfiguration>('/api/v1/database/configuration')
}

export function saveDatabaseConfiguration(payload: DatabaseConfiguration) {
  return request<SaveResult>('/api/v1/database/configuration', {
    method: 'PUT',
    body: JSON.stringify(payload),
  }).then((result) => {
    reportCache.clear()
    return result
  })
}

export function testDatabaseConnection(connection: SqlConnection) {
  return request<ConnectionTestResult>('/api/v1/database/test-connection', {
    method: 'POST',
    body: JSON.stringify(connection),
  })
}

export function previewDatabaseDataset(filters: DatabasePreviewRequest) {
  return request('/api/v1/database/preview', {
    method: 'POST',
    body: JSON.stringify(filters),
  })
}

export function validateWorkbenchSql(sql: string) {
  return request('/api/v1/database/sql/validate', {
    method: 'POST',
    body: JSON.stringify({ sql }),
  })
}

export function executeWorkbenchSql({ sql, maxRows, token }: { sql: string; maxRows: number; token: string }) {
  return request('/api/v1/database/sql/execute', {
    method: 'POST',
    headers: token ? { 'X-SQL-Workbench-Token': token } : {},
    body: JSON.stringify({ sql, max_rows: maxRows }),
  })
}

export function testWeldingConnection(payload: WeldingConnection) { return request<ConnectionTestResult>('/api/v1/database/welding/test-connection', { method: 'POST', body: JSON.stringify(payload) }) }
export function saveWeldingConfiguration(payload: WeldingConnection) { return request<SaveResult>('/api/v1/database/welding/configuration', { method: 'PUT', body: JSON.stringify(payload) }) }

export function getConfigurationDiagnostics({ signal }: ReadOptions = {}) {
  return request<ConfigurationDiagnostics>('/api/v1/configuration/diagnostics', { signal })
}
