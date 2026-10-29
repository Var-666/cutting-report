import type { ConfirmTaskRequest, ConfirmTasksRequest, RevokeTaskRequest, CorrectTaskRequest, ConfirmationBatch, WorkshopReportInfo, LatestProductionDate } from './generated/types.gen'
import type { ConfirmationAudit, TaskConfirmation, AnalysisRequest, AnalysisResult, LeadershipReport, ReportCreateRequest, ReportJob, WorkshopQuery, WorkshopRangeQuery, WorkshopResult } from '../types/reports'
import type { Items, Page, ReadOptions } from '../types/api'
import { reportCache } from './reportCache.ts'
import { request } from './client.ts'

const REPORT_VIEW_CACHE_TTL = 5 * 60 * 1000

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

export function listWorkshopRangeTasks({ startDate, endDate, projectName = '', confirmationStatus = 'all', page = 1, pageSize = 10 }: WorkshopRangeQuery, { force = false, signal }: ReadOptions = {}) {
  const query = new URLSearchParams({
    start_date: startDate,
    end_date: endDate,
    confirmation_status: confirmationStatus,
    page: String(page),
    page_size: String(pageSize),
  })
  if (projectName) query.set('project_name', projectName)
  const key = `workshop:range:${query}`
  return reportCache.read(key, (requestSignal) => request<WorkshopResult>(`/api/v1/workshop/range?${query}`, { signal: requestSignal }), { ttl: REPORT_VIEW_CACHE_TTL, force, signal })
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

export function createReport(form: ReportCreateRequest) {
  return request<ReportJob>('/api/v1/reports', {
    method: 'POST',
    body: JSON.stringify(form),
  }).then((data) => { reportCache.invalidate('history:'); return data })
}

export function getReportStatus(reportId: string, { signal }: ReadOptions = {}) {
  return request<ReportJob>(`/api/v1/reports/${encodeURIComponent(reportId)}/status`, { signal })
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

export function getLeadershipReport(reportMonth: string, { force = false, signal }: ReadOptions = {}) {
  const query = new URLSearchParams({ report_month: reportMonth })
  return reportCache.read(`leadership:${reportMonth}`, (signal) => request<LeadershipReport>(`/api/v1/leadership-report?${query}`, { signal }), { ttl: REPORT_VIEW_CACHE_TTL, force, signal })
}

export function getLeadershipRange(startDate: string, endDate: string, { force = false, signal }: ReadOptions = {}) {
  const query = new URLSearchParams({ start_date: startDate, end_date: endDate })
  return reportCache.read(`leadership:range:${query}`, (requestSignal) => request<LeadershipReport>(`/api/v1/leadership-report?${query}`, { signal: requestSignal }), { ttl: REPORT_VIEW_CACHE_TTL, force, signal })
}

export function analyzeReport({ reportType, reportDate, periodStart, periodEnd, summaryPeriodStart, summaryPeriodEnd, dataVersion, force = false }: AnalysisRequest, { signal }: ReadOptions = {}) {
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
      force,
    }),
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
  return request<{ data_version: string } | null>(`/api/v1/analysis/version?${query}`, { signal })
}
