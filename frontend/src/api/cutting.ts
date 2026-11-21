import type { ConfirmTaskRequest, ConfirmTasksRequest, RevokeTaskRequest, CorrectTaskRequest, ConfirmationBatch, WorkshopReportInfo, LatestProductionDate, ReportAccepted, AnalysisRequest as ApiAnalysisRequest, AnalysisVersion, MyConfirmedTasks, MyConfirmedTasksApiV1WorkshopMyConfirmedTasksGetData } from './generated/types.gen'
import type { ConfirmationAudit, TaskConfirmation, AnalysisRequest, AnalysisResult, LeadershipReport, ReportCreateRequest, ReportJob, WorkshopQuery, WorkshopRangeQuery, WorkshopResult } from '../types/reports'
import type { Items, Page, ReadOptions } from '../types/api'
import { reportCache, REPORT_VIEW_CACHE_TTL } from './reportCache.ts'
import { readReport } from './reportRequests.ts'
import { request } from './client.ts'
import { getDeviceId } from './deviceContext.ts'


export type MyConfirmedTasksQuery = NonNullable<MyConfirmedTasksApiV1WorkshopMyConfirmedTasksGetData['query']>
export function listMyConfirmedTasks(query: MyConfirmedTasksQuery, { signal }: ReadOptions = {}) {
  const { start_date, end_date, page, page_size } = query
  const parameters = new URLSearchParams({ start_date, end_date })
  if (page != null) parameters.set('page', String(page))
  if (page_size != null) parameters.set('page_size', String(page_size))
  return request<MyConfirmedTasks>(`/api/v1/workshop/my-confirmed-tasks?${parameters}`, { signal })
}

function invalidateWorkshopReports() {
  reportCache.invalidate('workshop:tasks:')
  reportCache.invalidate('workshop:range:')
}

export function listWorkshopTasks({ reportDate, projectName = '', confirmationStatus = 'all', sortMode = 'manager', page = 1, pageSize = 10 }: WorkshopQuery, { force = false, signal, warmup = false }: ReadOptions = {}) {
  const query = new URLSearchParams({
    report_date: reportDate,
    confirmation_status: confirmationStatus,
    sort_mode: sortMode,
    page: String(page),
    page_size: String(pageSize),
  })
  if (projectName) query.set('project_name', projectName)
  const key = `workshop:tasks:${query}`
  if (warmup) query.set('warmup', 'true')
  return reportCache.read(key, (signal) => request<WorkshopResult>(`/api/v1/workshop/tasks?${query}`, { signal }), { ttl: REPORT_VIEW_CACHE_TTL, force, signal })
}

export function listWorkshopRangeTasks({ startDate, endDate, projectName = '', taskId = '', confirmationStatus = 'all', page = 1, pageSize = 10 }: WorkshopRangeQuery, { force = false, signal, mode, onPreparing }: ReadOptions = {}) {
  const query = new URLSearchParams({
    start_date: startDate,
    end_date: endDate,
    confirmation_status: confirmationStatus,
    page: String(page),
    page_size: String(pageSize),
  })
  if (projectName) query.set('project_name', projectName)
  if (taskId) query.set('task_id', taskId)
  const key = `workshop:range:${query}`
  return readReport(key, (sharedSignal, refresh, lineId) => {
    const selected = new URLSearchParams(query)
    if (refresh) selected.set('refresh', 'true')
    return request<WorkshopResult>(`/api/v1/workshop/range?${selected}`, { signal: sharedSignal, headers: { 'X-Production-Line': lineId } })
  }, { signal, mode: mode ?? (force ? 'refresh' : 'read'), onPreparing })
}

export function generateWorkshopDailyReport(reportDate: string, { signal }: ReadOptions = {}) {
  return request<WorkshopReportInfo>('/api/v1/workshop/reports', {
    method: 'POST',
    body: JSON.stringify({ report_date: reportDate } satisfies ConfirmTaskRequest),
    signal,
  }).then((data) => { invalidateWorkshopReports(); return data })
}

export function getLatestWorkshopProductionDate(onOrBefore: string, { force = false, signal }: ReadOptions = {}) {
  const query = new URLSearchParams({ on_or_before: onOrBefore })
  return reportCache.read(`workshop:latest:${query}`, (signal) => request<LatestProductionDate>(`/api/v1/workshop/latest-date?${query}`, { signal }), { ttl: REPORT_VIEW_CACHE_TTL, force, signal })
}

export function confirmWorkshopTasks(taskIds: string[], reportDate: string, snapshotRevisions: Record<string, number>) {
  return request<ConfirmationBatch>('/api/v1/workshop/tasks/confirm', {
    method: 'POST',
    body: JSON.stringify({ report_date: reportDate, task_ids: taskIds, snapshot_revisions: snapshotRevisions } satisfies ConfirmTasksRequest),
  }).then((data) => { invalidateWorkshopReports(); return data })
}

export function revokeWorkshopTask(taskId: string, reportDate: string, reason: string) {
  return request(`/api/v1/workshop/tasks/${encodeURIComponent(taskId)}/revoke`, {
    method: 'POST',
    body: JSON.stringify({ report_date: reportDate, reason } satisfies RevokeTaskRequest),
  }).then((data) => { invalidateWorkshopReports(); return data })
}

export function correctWorkshopTask(taskId: string, { reportDate, action, workerId = null, reason }: { reportDate: string; action: CorrectTaskRequest['action']; workerId?: string | null; reason: string }) {
  return request(`/api/v1/workshop/tasks/${encodeURIComponent(taskId)}/correct`, {
    method: 'POST',
    body: JSON.stringify({ report_date: reportDate, action, worker_id: workerId, reason } satisfies CorrectTaskRequest),
  }).then((data) => { invalidateWorkshopReports(); return data })
}

export function listWorkshopConfirmationAudits({ reportDate, taskId, limit = 30 }: { reportDate: string; taskId: string; limit?: number }) {
  const query = new URLSearchParams({ report_date: reportDate, task_id: taskId, limit: String(limit) })
  return request<Items<ConfirmationAudit>>(`/api/v1/workshop/confirmation-audits?${query}`)
}

export function createReport(form: ReportCreateRequest, { idempotencyKey, deviceId }: { idempotencyKey?: string; deviceId?: string } = {}) {
  return request<ReportAccepted>('/api/v1/reports', {
    method: 'POST',
    body: JSON.stringify(form),
    headers: { ...(idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : {}),
      ...(deviceId ? { 'X-Production-Line': deviceId } : {}) },
  }).then((data) => { reportCache.invalidate('history:'); return data })
}

export function getReportStatus(reportId: string, { signal, deviceId }: ReadOptions & { deviceId?: string } = {}) {
  return request<ReportJob>(`/api/v1/reports/${encodeURIComponent(reportId)}/status`, {
    signal, headers: deviceId ? { 'X-Production-Line': deviceId } : undefined,
  })
}

export function listReports({ page = 1, pageSize = 20, force = false, signal }: ReadOptions & { page?: number; pageSize?: number } = {}) {
  const query = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
  return reportCache.read(`history:${query}`, (signal) => request<Page<ReportJob>>(`/api/v1/reports?${query}`, { signal }), { ttl: 5000, force, signal })
}

export function listProjects(reportDate: string, { signal }: ReadOptions = {}) {
  const query = new URLSearchParams({ report_date: reportDate })
  return request<Items<string>>(`/api/v1/options/projects?${query}`, { signal })
}

export function listShips(reportDate: string, projectCode: string, { signal }: ReadOptions = {}) {
  const query = new URLSearchParams({ report_date: reportDate })
  if (projectCode) query.set('project_code', projectCode)
  return request<Items<string>>(`/api/v1/options/ships?${query}`, { signal })
}

export function getLeadershipReport(reportMonth: string, { force = false, signal, lineId = getDeviceId(), mode, onPreparing }: ReadOptions & { lineId?: string } = {}) {
  const query = new URLSearchParams({ report_month: reportMonth })
  if (lineId) query.set('device_id', lineId)
  return readReport(`leadership:${lineId}:${reportMonth}`, (sharedSignal, refresh) => {
    const selected = new URLSearchParams(query)
    if (refresh) selected.set('refresh', 'true')
    return request<LeadershipReport>(`/api/v1/leadership-report?${selected}`, { signal: sharedSignal, headers: { 'X-Production-Line': lineId } })
  }, { signal, mode: mode ?? (force ? 'refresh' : 'read'), cacheScope: lineId, onPreparing })
}

export function getLeadershipRange(startDate: string, endDate: string, { force = false, signal, mode, onPreparing }: ReadOptions = {}) {
  const query = new URLSearchParams({ start_date: startDate, end_date: endDate })
  return readReport(`leadership:range:${query}`, (sharedSignal, refresh, lineId) => {
    const selected = new URLSearchParams(query)
    if (refresh) selected.set('refresh', 'true')
    return request<LeadershipReport>(`/api/v1/leadership-report?${selected}`, { signal: sharedSignal, headers: { 'X-Production-Line': lineId } })
  }, { signal, mode: mode ?? (force ? 'refresh' : 'read'), onPreparing })
}

export function analyzeReport({ reportType, reportDate, periodStart, periodEnd, summaryPeriodStart, summaryPeriodEnd, dataVersion, workshopScope, force = false }: AnalysisRequest, { signal }: ReadOptions = {}) {
  return request<AnalysisResult>('/api/v1/analysis', {
    method: 'POST',
    signal,
    body: JSON.stringify({
      report_type: reportType,
      report_date: reportDate,
      period_start: periodStart,
      period_end: periodEnd,
      summary_period_start: summaryPeriodStart,
      summary_period_end: summaryPeriodEnd,
      data_version: dataVersion || undefined,
      workshop_scope: workshopScope,
      force,
    } satisfies ApiAnalysisRequest),
  })
}

export function getLatestAnalysis({ reportType, reportDate }: Pick<AnalysisRequest, 'reportType' | 'reportDate'>, { signal }: ReadOptions = {}) {
  const query = new URLSearchParams({ report_type: reportType, report_date: reportDate })
  return request<AnalysisResult | null>(`/api/v1/analysis/latest?${query}`, { signal })
}

export function getAnalysisVersion({ reportType, reportDate }: Pick<AnalysisRequest, 'reportType' | 'reportDate'>, { signal }: ReadOptions = {}) {
  const query = new URLSearchParams({
    report_type: reportType,
    report_date: reportDate,
  })
  return request<AnalysisVersion | null>(`/api/v1/analysis/version?${query}`, { signal })
}
