import { request } from './client'
import type {
  AcknowledgeIncident, ExportLogsApiV1OperationsLogsExportGetData, Incident, IncidentsApiV1OperationsIncidentsGetData,
  LogPage, LogsApiV1OperationsLogsGetData, OperationsOverview, OperationsStatus,
  PageIncident, PendingNoticeApiV1OperationsPendingNoticeGetResponse, RelatedOperation,
} from './generated/types.gen'

export type IncidentQuery = NonNullable<IncidentsApiV1OperationsIncidentsGetData['query']>
export type LogQuery = NonNullable<LogsApiV1OperationsLogsGetData['query']>
type LogExportQuery = NonNullable<ExportLogsApiV1OperationsLogsExportGetData['query']>
export type RelatedKind = RelatedOperation['kind']

const base = '/api/v1/operations'
function queryString(values: IncidentQuery | LogQuery) {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(values)) {
    if (value != null && value !== '') params.set(key, String(value))
  }
  return params.toString()
}

export const operations = {
  overview: (signal: AbortSignal) => request<OperationsOverview>(`${base}/overview`, { signal }),
  status: (signal: AbortSignal) => request<OperationsStatus>(`${base}/status`, { signal }),
  pendingNotice: (signal: AbortSignal) =>
    request<PendingNoticeApiV1OperationsPendingNoticeGetResponse['data']>(`${base}/pending-notice`, { signal }),
  incidents: (query: IncidentQuery, signal: AbortSignal) =>
    request<PageIncident>(`${base}/incidents?${queryString(query)}`, { signal }),
  incident: (id: string, signal: AbortSignal) =>
    request<Incident>(`${base}/incidents/${encodeURIComponent(id)}`, { signal }),
  acknowledge: (id: string, body: AcknowledgeIncident) =>
    request<Incident>(`${base}/incidents/${encodeURIComponent(id)}/acknowledge`, { method: 'POST', body: JSON.stringify(body) }),
  logs: (query: LogQuery, signal: AbortSignal) =>
    request<LogPage>(`${base}/logs?${queryString(query)}`, { signal }),
  exportLogs: (query: LogExportQuery, signal: AbortSignal) =>
    request(`${base}/logs/export?${queryString(query)}`, { responseType: 'blob', signal }),
  related: (kind: RelatedKind, id: string, signal: AbortSignal) =>
    request<RelatedOperation>(`${base}/related/${kind}/${encodeURIComponent(id)}`, { signal }),
}
