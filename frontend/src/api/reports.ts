import type { DesignerResource, DesignerResources, QueryPreview, QueryPreviewRequest, SqlValidation } from '../types/designer'
import type { CuttingCatalog, CuttingDispatch, CuttingFile, CuttingMachine, CuttingProject, CuttingStatus, DispatchBatchItem, DispatchCreationResult } from '../types/cutting'
import type { AlarmIncidentPage, AlarmReport, AlarmReportPage, AlarmIncident, AlarmStatus } from '../types/alarms'
import type { ConfigurationDiagnostics, ConnectionTestResult, DatabasePreviewRequest, DatabaseConfiguration, DatabaseStatus, SaveResult, SqlConnection, WeldingConnection } from '../types/configuration'
import type { AutomationConfiguration, AutomationSchedule, DeliveryResolutionRequest, AutomationStatus, EmailDelivery, SchedulePayload, EmailResolution } from '../types/automation'
import type { UsageReport } from '../types/usage'
import type { ConfirmationAudit, TaskConfirmation, AnalysisRequest, AnalysisResult, LeadershipReport, ReportCreateRequest, ReportDefinition, ReportJob, WorkshopQuery, WorkshopRangeQuery, WorkshopResult } from '../types/reports'
import type { EnergyDayReport, EnergyMonthReport, EnergyPeriodReport, EnergyRangeReport, TaskEnergyReport } from '../types/energy'
import type { SystemHealth } from '../types/system'
import type { ApiError, ApiResponse, DataRow, Items, Page, ReadOptions, RequestOptions } from '../types/api'
import type { Authentication, AuthStatus, BootstrapRequest, HrPerson, LoginRequest, User } from '../types/auth'
import type { DeviceCatalog } from '../types/devices'
import { deviceUrl, getDeviceId } from './deviceContext.ts'
import { reportCache } from './reportCache.ts'
import { reportToday } from './reportDates.ts'
import { createRequestCancellation } from './requestCancellation.ts'
import { notifySessionExpired } from './sessionExpiry.ts'

const DEFAULT_HEADERS = { 'Content-Type': 'application/json' }
const REPORT_VIEW_CACHE_TTL = 5 * 60 * 1000

function invalidateWorkshopReports() {
  reportCache.invalidate('workshop:tasks:')
  reportCache.invalidate('workshop:range:')
}

export function request(url: string, options: RequestOptions & { responseType: 'blob' }): Promise<{ blob: Blob; headers: Headers }>
export function request<T = unknown>(url: string, options?: RequestOptions): Promise<T>
export async function request<T = unknown>(url: string, options: RequestOptions = {}): Promise<T | { blob: Blob; headers: Headers }> {
  url = deviceUrl(url)
  const cancellation = createRequestCancellation(options.signal)
  try {
    const response = await fetch(url, {
      ...options,
      credentials: 'same-origin',
      headers: {
        ...DEFAULT_HEADERS,
        ...(getDeviceId() ? { 'X-Production-Line': getDeviceId() } : {}),
        ...(options.headers || {}),
        ...cancellation.headers,
      },
    })
    if (options.responseType === 'blob' && response.ok) return { blob: await response.blob(), headers: response.headers }
    let payload: ApiResponse<T>
    try {
      payload = await response.json()
    } catch (error) {
      if (error instanceof Error && error.name === 'AbortError') throw error
      throw new Error('服务器返回了无法识别的响应')
    }
    if (
      response.status === 401
      && response.headers?.get?.('X-Session-Expired') === '1'
    ) {
      await notifySessionExpired().catch(() => undefined)
    }
    if (!response.ok || payload.success === false) {
      const message = payload?.error?.message || payload?.detail || '请求失败，请稍后重试'
      const error: ApiError = new Error(message)
      error.status = response.status
      const retryAfter = Number(response.headers?.get?.('Retry-After'))
      if (Number.isFinite(retryAfter) && retryAfter > 0) error.retryAfterMs = retryAfter * 1000
      throw error
    }
    return payload.data
  } finally {
    cancellation.dispose()
  }
}

export function getAuthStatus() {
  return request<AuthStatus>('/api/v1/auth/status')
}

export function bootstrapAccount(payload: BootstrapRequest) {
  return request<Authentication>('/api/v1/auth/bootstrap', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function loginAccount(payload: LoginRequest) {
  return request<Authentication>('/api/v1/auth/login', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function logoutAccount() {
  return request<{ logged_out: boolean }>('/api/v1/auth/logout', { method: 'POST' })
}

export function listUserAccounts() {
  return request<Items<User>>('/api/v1/auth/users')
}

export function searchHrPeople(query: string) {
  return request<Items<HrPerson>>(`/api/v1/auth/hr-directory?${new URLSearchParams({ query })}`)
}

export function listAdminAccounts() {
  return request<Items<User>>('/api/v1/auth/admins')
}

export function grantAdminAccess(externalId: string, currentPassword: string) {
  return request<User>('/api/v1/auth/admins', {
    method: 'POST',
    body: JSON.stringify({ external_id: externalId, current_password: currentPassword }),
  })
}

export function revokeAdminAccess(userId: string, currentPassword: string) {
  return request<User>(`/api/v1/auth/admins/${encodeURIComponent(userId)}/revoke`, {
    method: 'POST',
    body: JSON.stringify({ current_password: currentPassword }),
  })
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
  return request('/api/v1/workshop/reports', {
    method: 'POST',
    body: JSON.stringify({ report_date: reportDate }),
    signal,
  }).then((data) => { invalidateWorkshopReports(); return data })
}

export function getLatestWorkshopProductionDate(onOrBefore: string, { force = false, signal }: ReadOptions = {}) {
  const query = new URLSearchParams({ on_or_before: onOrBefore })
  return reportCache.read(`workshop:latest:${query}`, (signal) => request<{ report_date: string | null }>(`/api/v1/workshop/latest-date?${query}`, { signal }), { ttl: REPORT_VIEW_CACHE_TTL, force, signal })
}

export function confirmWorkshopTasks(taskIds: string[], reportDate: string, snapshotRevisions: Record<string, number>) {
  return request<Items<TaskConfirmation>>('/api/v1/workshop/tasks/confirm', {
    method: 'POST',
    body: JSON.stringify({ report_date: reportDate, task_ids: taskIds, snapshot_revisions: snapshotRevisions }),
  }).then((data) => { invalidateWorkshopReports(); return data })
}

export function revokeWorkshopTask(taskId: string, reportDate: string, reason: string) {
  return request(`/api/v1/workshop/tasks/${encodeURIComponent(taskId)}/revoke`, {
    method: 'POST',
    body: JSON.stringify({ report_date: reportDate, reason }),
  }).then((data) => { invalidateWorkshopReports(); return data })
}

export function correctWorkshopTask(taskId: string, { reportDate, action, workerId = null, reason }: { reportDate: string; action: string; workerId?: string | null; reason: string }) {
  return request(`/api/v1/workshop/tasks/${encodeURIComponent(taskId)}/correct`, {
    method: 'POST',
    body: JSON.stringify({ report_date: reportDate, action, worker_id: workerId, reason }),
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

export function listProjects(reportDate: string) {
  const query = new URLSearchParams({ report_date: reportDate })
  return request<Items<string>>(`/api/v1/options/projects?${query}`)
}

export function listShips(reportDate: string, projectCode: string) {
  const query = new URLSearchParams({ report_date: reportDate })
  if (projectCode) query.set('project_code', projectCode)
  return request<Items<string>>(`/api/v1/options/ships?${query}`)
}

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

export function getSystemHealth() {
  return request<SystemHealth>('/api/v1/health')
}

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

export function getLeadershipReport(reportMonth: string, { force = false, signal }: ReadOptions = {}) {
  const query = new URLSearchParams({ report_month: reportMonth })
  return reportCache.read(`leadership:${reportMonth}`, (signal) => request<LeadershipReport>(`/api/v1/leadership-report?${query}`, { signal }), { ttl: REPORT_VIEW_CACHE_TTL, force, signal })
}

export function getLeadershipRange(startDate: string, endDate: string, { force = false, signal }: ReadOptions = {}) {
  const query = new URLSearchParams({ start_date: startDate, end_date: endDate })
  return reportCache.read(`leadership:range:${query}`, (requestSignal) => request<LeadershipReport>(`/api/v1/leadership-report?${query}`, { signal: requestSignal }), { ttl: REPORT_VIEW_CACHE_TTL, force, signal })
}

export function getEnergyRange(startDate: string, endDate: string, { force = false, signal }: ReadOptions = {}) {
  const query = new URLSearchParams({ start_date: startDate, end_date: endDate })
  const key = `energy:range:${query}`
  if (force) query.set('refresh', 'true')
  return reportCache.read(key, (requestSignal) => request<EnergyRangeReport>(`/api/v1/energy-report/range?${query}`, { signal: requestSignal }), { ttl: REPORT_VIEW_CACHE_TTL, force, signal })
}

export function getEnergyDay(reportDate = '', { force = false, signal }: ReadOptions = {}) {
  reportDate ||= reportToday()
  const query = new URLSearchParams()
  if (reportDate) query.set('report_date', reportDate)
  if (force) query.set('refresh', 'true')
  return reportCache.read(`energy:day:${reportDate}`, async (signal) => {
    const data = await request<EnergyDayReport>(`/api/v1/energy-report/day?${query}`, { signal })
    if (!signal.aborted) reportCache.invalidate(`energy:month:${reportDate.slice(0, 7)}`)
    if (!signal.aborted) reportCache.invalidate('energy:monthly-range:')
    return data
  }, { ttl: REPORT_VIEW_CACHE_TTL, force, signal })
}

export function getTaskEnergyDay(reportDate = '', { force = false, signal }: ReadOptions = {}) {
  reportDate ||= reportToday()
  const query = new URLSearchParams()
  if (reportDate) query.set('report_date', reportDate)
  if (force) query.set('refresh', 'true')
  return reportCache.read(`energy:tasks:${reportDate}`, (signal) => request<TaskEnergyReport>(`/api/v1/energy-report/tasks?${query}`, { signal }), { ttl: REPORT_VIEW_CACHE_TTL, force, signal })
}

export function getEnergyMonth(reportMonth: string, { force = false, signal }: ReadOptions = {}) {
  const query = new URLSearchParams({ report_month: reportMonth })
  return reportCache.read(`energy:month:${reportMonth}`, (signal) => request<EnergyMonthReport>(`/api/v1/energy-report/month?${query}`, { signal }), { ttl: REPORT_VIEW_CACHE_TTL, force, signal })
}

export function getEnergyMonthlyRange(startDate: string, endDate: string, { force = false, signal }: ReadOptions = {}) {
  const query = new URLSearchParams({ start_date: startDate, end_date: endDate })
  const key = `energy:monthly-range:${query}`
  return reportCache.read(key, (requestSignal) => request<EnergyPeriodReport>(`/api/v1/energy-report/monthly-range?${query}`, { signal: requestSignal }), { ttl: REPORT_VIEW_CACHE_TTL, force, signal })
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

export function getAutomationStatus() {
  return request<AutomationStatus>('/api/v1/automation/status')
}

export function getLlmUsage() {
  return request('/api/v1/llm-usage')
}

export function getLlmUsageReport({ startDate, endDate, granularity = 'day', scene = '' }: { startDate: string; endDate: string; granularity?: string; scene?: string }, { signal }: ReadOptions = {}) {
  const query = new URLSearchParams({ start_date: startDate, end_date: endDate, granularity, scene })
  return request<UsageReport>(`/api/v1/llm-usage/report?${query}`, { signal })
}

export function getAutomationConfiguration() {
  return request<AutomationConfiguration>('/api/v1/automation/configuration')
}

export function saveAutomationConfiguration(payload: AutomationConfiguration) {
  return request<SaveResult>('/api/v1/automation/configuration', {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function listAutomationSchedules() {
  return request<Items<AutomationSchedule>>('/api/v1/automation/schedules')
}

export function saveAutomationSchedule(payload: SchedulePayload, scheduleId = '') {
  const url = scheduleId
    ? `/api/v1/automation/schedules/${encodeURIComponent(scheduleId)}`
    : '/api/v1/automation/schedules'
  return request<AutomationSchedule>(url, {
    method: scheduleId ? 'PUT' : 'POST',
    body: JSON.stringify(payload),
  })
}

export function deleteAutomationSchedule(scheduleId: string) {
  return request(`/api/v1/automation/schedules/${encodeURIComponent(scheduleId)}`, {
    method: 'DELETE',
  })
}

export function runAutomationSchedule(scheduleId: string) {
  return request<{ report_id: string; status: string; last_error?: string }>(`/api/v1/automation/schedules/${encodeURIComponent(scheduleId)}/run`, {
    method: 'POST',
  })
}

export function listEmailDeliveries({ page = 1, pageSize = 20 } = {}) {
  const query = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
  return request<Page<EmailDelivery>>(`/api/v1/automation/deliveries?${query}`)
}

export function retryEmailDelivery(deliveryId: string) {
  return request(`/api/v1/automation/deliveries/${encodeURIComponent(deliveryId)}/retry`, {
    method: 'POST',
  })
}

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

export function resolveEmailDelivery(deliveryId: string, payload: DeliveryResolutionRequest) {
  return request(`/api/v1/automation/deliveries/${encodeURIComponent(deliveryId)}/resolve`, {
    method: 'POST', body: JSON.stringify(payload),
  })
}

export function listEmailDeliveryResolutions(deliveryId: string, { signal }: ReadOptions = {}) {
  return request<Items<EmailResolution>>(`/api/v1/automation/deliveries/${encodeURIComponent(deliveryId)}/resolutions`, { signal })
}

export function getDeviceCatalog() { return request<DeviceCatalog>('/api/v1/devices') }
export function getProductionLines() { return request<DeviceCatalog>('/api/v1/production-lines') }
export function testWeldingConnection(payload: WeldingConnection) { return request<ConnectionTestResult>('/api/v1/database/welding/test-connection', { method: 'POST', body: JSON.stringify(payload) }) }
export function saveWeldingConfiguration(payload: WeldingConnection) { return request<SaveResult>('/api/v1/database/welding/configuration', { method: 'PUT', body: JSON.stringify(payload) }) }

export function getConfigurationDiagnostics({ signal }: ReadOptions = {}) {
  return request<ConfigurationDiagnostics>('/api/v1/configuration/diagnostics', { signal })
}
