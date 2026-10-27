import type { DesignerResource, DesignerResources, QueryPreview, QueryPreviewRequest, SqlValidation } from '../types/designer'
import type { ReportDefinition } from '../types/reports'
import type { Items } from '../types/api'
import { request } from './client.ts'

function adminHeaders(token: string): Record<string, string> {
  return token ? { 'X-SQL-Workbench-Token': token } : {}
}

export function listPublishedDefinitions() {
  return request<Items<ReportDefinition>>('/api/v1/designer/published-definitions')
}

export function listDesignerResources<K extends keyof DesignerResources>(resource: K, token = '') {
  return request<Items<DesignerResources[K]>>(`/api/v1/designer/${resource}`, { headers: adminHeaders(token) })
}

export function saveDesignerResource(resource: keyof DesignerResources, payload: DesignerResource, id = '', token = '') {
  const url = id
    ? `/api/v1/designer/${resource}/${encodeURIComponent(id)}`
    : `/api/v1/designer/${resource}`
  return request<DesignerResource>(url, {
    method: id ? 'PUT' : 'POST',
    headers: adminHeaders(token),
    body: JSON.stringify(payload),
  })
}

export function deleteDesignerResource(resource: keyof DesignerResources, id: string, token = '') {
  return request(`/api/v1/designer/${resource}/${encodeURIComponent(id)}`, {
    method: 'DELETE',
    headers: adminHeaders(token),
  })
}

export function validateDesignerSql(sqlText: string, token = '') {
  return request<SqlValidation>('/api/v1/designer/query-tasks/validate', {
    method: 'POST',
    headers: adminHeaders(token),
    body: JSON.stringify({ sql_text: sqlText }),
  })
}

export function previewDesignerQuery(queryTaskId: string, payload: QueryPreviewRequest, token = '') {
  return request<QueryPreview>(`/api/v1/designer/query-tasks/${encodeURIComponent(queryTaskId)}/preview`, {
    method: 'POST',
    headers: adminHeaders(token),
    body: JSON.stringify(payload),
  })
}
