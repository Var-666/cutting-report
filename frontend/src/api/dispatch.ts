import type { CuttingCatalog, CuttingDispatch, CuttingFile, CuttingMachine, CuttingProject, CuttingStatus, DispatchBatchItem, DispatchCreationResult } from '../types/cutting'
import type { Items, Page, ReadOptions } from '../types/api'
import { reportCache } from './reportCache.ts'
import { request } from './client.ts'

export function getCuttingStatus(scope = 'push', { signal, force = false }: ReadOptions = {}) {
  const query = new URLSearchParams({ scope })
  return reportCache.read(
    `cutting:status:${scope}`,
    (signal) => request<CuttingStatus>(`/api/v1/cutting/status?${query}`, { signal }),
    { ttl: 60000, signal, force },
  )
}

export function listCuttingProjects(keyword = '', limit = 1000, { signal }: ReadOptions = {}) {
  const query = new URLSearchParams({ limit: String(limit) })
  if (keyword) query.set('keyword', keyword)
  const url = `/api/v1/cutting/projects?${query}`
  return reportCache.read(
    `cutting:projects:${query}`,
    (signal) => request<Items<CuttingProject>>(url, { signal }),
    { ttl: 300000, signal },
  )
}

export function listCuttingCatalogs(projectId: string, { signal }: ReadOptions = {}) {
  const query = new URLSearchParams({ project_id: projectId })
  const url = `/api/v1/cutting/catalogs?${query}`
  return reportCache.read(
    `cutting:catalogs:${String(projectId)}`,
    (signal) => request<{ tree: CuttingCatalog[] }>(url, { signal }),
    { ttl: 300000, signal },
  )
}

export function listCuttingFiles({ projectId, catalogId = '', status = 'all', keyword = '', page = 1, pageSize = 10 }: { projectId: string; catalogId?: string; status?: string; keyword?: string; page?: number; pageSize?: number }, { signal, force = false }: ReadOptions = {}) {
  const query = new URLSearchParams({
    project_id: projectId,
    status,
    page: String(page),
    page_size: String(pageSize),
  })
  if (catalogId) query.set('catalog_id', catalogId)
  if (keyword) query.set('keyword', keyword)
  const url = `/api/v1/cutting/files?${query}`
  return reportCache.read(
    `cutting:files:${query}`,
    (signal) => request<Page<CuttingFile>>(url, { signal }),
    { ttl: 300000, signal, force },
  )
}

export function createCuttingDispatchBatch(payload: { machine_ip: string; items: DispatchBatchItem[] }) {
  return request<Items<DispatchCreationResult>>('/api/v1/cutting/dispatches/batch', {
    method: 'POST',
    body: JSON.stringify(payload),
  }).finally(() => reportCache.invalidate('cutting:files:'))
}

export function listCuttingDispatches({ page = 1, pageSize = 10 } = {}, { signal }: ReadOptions = {}) {
  const query = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
  return request<Page<CuttingDispatch>>(`/api/v1/cutting/dispatches?${query}`, { signal })
}

export function getCuttingDispatch(dispatchId: string, { signal }: ReadOptions = {}) {
  return request<CuttingDispatch>(`/api/v1/cutting/dispatches/${encodeURIComponent(dispatchId)}`, { signal })
}

export function listCuttingMachines({ signal }: ReadOptions = {}) {
  return request<Items<CuttingMachine>>('/api/v1/cutting/machines', { signal })
}

export function executeCuttingDispatch(dispatchId: string, segmentName?: string) {
  return request<CuttingDispatch>(`/api/v1/cutting/dispatches/${encodeURIComponent(dispatchId)}/execute`, {
    method: 'POST',
    ...(segmentName ? { body: JSON.stringify({ segment_name: segmentName }) } : {}),
  }).finally(() => reportCache.invalidate('cutting:files:'))
}
