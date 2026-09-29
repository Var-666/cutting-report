import { reportCache } from './reportCache.js'
import { reportToday } from './reportDates.js'
import { createRequestCancellation } from './requestCancellation.js'
import { notifySessionExpired } from './sessionExpiry.js'

const DEFAULT_HEADERS = { 'Content-Type': 'application/json' }
const REPORT_VIEW_CACHE_TTL = 5 * 60 * 1000

function invalidateWorkshopReports() {
  reportCache.invalidate('workshop:tasks:')
  reportCache.invalidate('workshop:range:')
}

async function request(url, options = {}) {
  const cancellation = createRequestCancellation(options.signal)
  try {
    const response = await fetch(url, {
      ...options,
      credentials: 'same-origin',
      headers: {
        ...DEFAULT_HEADERS,
        ...(options.headers || {}),
        ...cancellation.headers,
      },
    })
    let payload
    try {
      payload = await response.json()
    } catch (error) {
      if (error.name === 'AbortError') throw error
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
      const error = new Error(message)
      error.status = response.status
      throw error
    }
    return payload.data
  } finally {
    cancellation.dispose()
  }
}

export function getAuthStatus() {
  return request('/api/v1/auth/status')
}

export function bootstrapAccount(payload) {
  return request('/api/v1/auth/bootstrap', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function loginAccount(payload) {
  return request('/api/v1/auth/login', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function workerLoginAccount(payload) {
  return request('/api/v1/auth/worker-login', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function hrLoginAccount(payload) {
  return request('/api/v1/auth/hr-login', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function logoutAccount() {
  return request('/api/v1/auth/logout', { method: 'POST' })
}

export function listUserAccounts() {
  return request('/api/v1/auth/users')
}

export function createUserAccount(payload) {
  return request('/api/v1/auth/users', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function searchHrPeople(query) {
  return request(`/api/v1/auth/hr-directory?${new URLSearchParams({ query })}`)
}

export function listAdminAccounts() {
  return request('/api/v1/auth/admins')
}

export function grantAdminAccess(externalId, currentPassword) {
  return request('/api/v1/auth/admins', {
    method: 'POST',
    body: JSON.stringify({ external_id: externalId, current_password: currentPassword }),
  })
}

export function revokeAdminAccess(userId, currentPassword) {
  return request(`/api/v1/auth/admins/${encodeURIComponent(userId)}/revoke`, {
    method: 'POST',
    body: JSON.stringify({ current_password: currentPassword }),
  })
}

export function listWorkshopTasks({ reportDate, projectName = '', confirmationStatus = 'all', sortMode = 'manager', page = 1, pageSize = 10 }, { force = false, signal, warmup = false } = {}) {
  const query = new URLSearchParams({
    report_date: reportDate,
    confirmation_status: confirmationStatus,
    sort_mode: sortMode,
    page,
    page_size: pageSize,
  })
  if (projectName) query.set('project_name', projectName)
  const key = `workshop:tasks:${query}`
  if (warmup) query.set('warmup', 'true')
  return reportCache.read(key, (signal) => request(`/api/v1/workshop/tasks?${query}`, { signal }), { ttl: REPORT_VIEW_CACHE_TTL, force, signal })
}

export function listWorkshopRangeTasks({ startDate, endDate, projectName = '', confirmationStatus = 'all', page = 1, pageSize = 10 }, { force = false, signal } = {}) {
  const query = new URLSearchParams({
    start_date: startDate,
    end_date: endDate,
    confirmation_status: confirmationStatus,
    page,
    page_size: pageSize,
  })
  if (projectName) query.set('project_name', projectName)
  const key = `workshop:range:${query}`
  return reportCache.read(key, (requestSignal) => request(`/api/v1/workshop/range?${query}`, { signal: requestSignal }), { ttl: REPORT_VIEW_CACHE_TTL, force, signal })
}

export function generateWorkshopDailyReport(reportDate) {
  return request('/api/v1/workshop/reports', {
    method: 'POST',
    body: JSON.stringify({ report_date: reportDate }),
  }).then((data) => { invalidateWorkshopReports(); return data })
}

export function getLatestWorkshopProductionDate(onOrBefore, { force = false, signal } = {}) {
  const query = new URLSearchParams({ on_or_before: onOrBefore })
  return reportCache.read(`workshop:latest:${query}`, (signal) => request(`/api/v1/workshop/latest-date?${query}`, { signal }), { ttl: REPORT_VIEW_CACHE_TTL, force, signal })
}

export function confirmWorkshopTask(taskId, reportDate) {
  return request(`/api/v1/workshop/tasks/${encodeURIComponent(taskId)}/confirm`, {
    method: 'POST',
    body: JSON.stringify({ report_date: reportDate }),
  }).then((data) => { invalidateWorkshopReports(); return data })
}

export function confirmWorkshopTasks(taskIds, reportDate) {
  return request('/api/v1/workshop/tasks/confirm', {
    method: 'POST',
    body: JSON.stringify({ report_date: reportDate, task_ids: taskIds }),
  }).then((data) => { invalidateWorkshopReports(); return data })
}

export function revokeWorkshopTask(taskId, reportDate, reason) {
  return request(`/api/v1/workshop/tasks/${encodeURIComponent(taskId)}/revoke`, {
    method: 'POST',
    body: JSON.stringify({ report_date: reportDate, reason }),
  }).then((data) => { invalidateWorkshopReports(); return data })
}

export function correctWorkshopTask(taskId, { reportDate, action, workerId = null, reason }) {
  return request(`/api/v1/workshop/tasks/${encodeURIComponent(taskId)}/correct`, {
    method: 'POST',
    body: JSON.stringify({ report_date: reportDate, action, worker_id: workerId, reason }),
  }).then((data) => { invalidateWorkshopReports(); return data })
}

export function listWorkshopConfirmationAudits({ reportDate, taskId, limit = 30 }) {
  const query = new URLSearchParams({ report_date: reportDate, task_id: taskId, limit })
  return request(`/api/v1/workshop/confirmation-audits?${query}`)
}

export function createReport(form) {
  return request('/api/v1/reports', {
    method: 'POST',
    body: JSON.stringify(form),
  }).then((data) => { reportCache.invalidate('history:'); return data })
}

export function getReportStatus(reportId, { signal } = {}) {
  return request(`/api/v1/reports/${encodeURIComponent(reportId)}/status`, { signal })
}

export function listReports({ page = 1, pageSize = 20, force = false, signal } = {}) {
  const query = new URLSearchParams({ page, page_size: pageSize })
  return reportCache.read(`history:${query}`, (signal) => request(`/api/v1/reports?${query}`, { signal }), { ttl: 5000, force, signal })
}

export function listProjects(reportDate) {
  const query = new URLSearchParams({ report_date: reportDate })
  return request(`/api/v1/options/projects?${query}`)
}

export function listShips(reportDate, projectCode) {
  const query = new URLSearchParams({ report_date: reportDate })
  if (projectCode) query.set('project_code', projectCode)
  return request(`/api/v1/options/ships?${query}`)
}

export function getDatabaseStatus() {
  return request('/api/v1/database/status')
}

export function getDatabaseConfiguration() {
  return request('/api/v1/database/configuration')
}

export function saveDatabaseConfiguration(payload) {
  return request('/api/v1/database/configuration', {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function getSystemHealth() {
  return request('/api/v1/health')
}

export function getAlarmStatus({ signal } = {}) {
  return request('/api/v1/alarms/status', { signal })
}

export function listAlarmIncidents({ status = '', limit = 100 } = {}, { signal } = {}) {
  const query = new URLSearchParams({ limit })
  if (status) query.set('status', status)
  return request(`/api/v1/alarms/incidents?${query}`, { signal })
}

export function listAlarmIncidentPage({ status = '', severity = '', page = 1, pageSize = 15 } = {}, { signal } = {}) {
  const query = new URLSearchParams({ page, page_size: pageSize })
  if (status) query.set('status', status)
  if (severity) query.set('severity', severity)
  return request(`/api/v1/alarms/incidents/page?${query}`, { signal })
}

export function listAlarmReports({ limit = 50 } = {}, { signal } = {}) {
  const query = new URLSearchParams({ limit })
  return request(`/api/v1/alarms/reports?${query}`, { signal })
}

export function getLeadershipReport(reportMonth, { force = false, signal } = {}) {
  const query = new URLSearchParams({ report_month: reportMonth })
  return reportCache.read(`leadership:${reportMonth}`, (signal) => request(`/api/v1/leadership-report?${query}`, { signal }), { ttl: REPORT_VIEW_CACHE_TTL, force, signal })
}

export function getLeadershipRange(startDate, endDate, { force = false, signal } = {}) {
  const query = new URLSearchParams({ start_date: startDate, end_date: endDate })
  return reportCache.read(`leadership:range:${query}`, (requestSignal) => request(`/api/v1/leadership-report?${query}`, { signal: requestSignal }), { ttl: REPORT_VIEW_CACHE_TTL, force, signal })
}

export function getEnergyRange(startDate, endDate, { force = false, signal } = {}) {
  const query = new URLSearchParams({ start_date: startDate, end_date: endDate })
  const key = `energy:range:${query}`
  if (force) query.set('refresh', 'true')
  return reportCache.read(key, (requestSignal) => request(`/api/v1/energy-report/range?${query}`, { signal: requestSignal }), { ttl: REPORT_VIEW_CACHE_TTL, force, signal })
}

export function refreshLeadershipReport(reportMonth) {
  return request('/api/v1/leadership-report/refresh', {
    method: 'POST',
    body: JSON.stringify({ report_month: reportMonth }),
  }).then((data) => {
    reportCache.invalidate(`leadership:${reportMonth}`)
    return data
  })
}

export function getEquipmentReport(reportDate = '', { force = false } = {}) {
  const query = new URLSearchParams()
  if (reportDate) query.set('report_date', reportDate)
  if (force) query.set('refresh', 'true')
  const queryString = query.toString()
  const suffix = queryString ? `?${queryString}` : ''
  return request(`/api/v1/equipment-report${suffix}`)
}

export function getEnergyDay(reportDate = '', { force = false, signal } = {}) {
  reportDate ||= reportToday()
  const query = new URLSearchParams()
  if (reportDate) query.set('report_date', reportDate)
  if (force) query.set('refresh', 'true')
  return reportCache.read(`energy:day:${reportDate}`, async (signal) => {
    const data = await request(`/api/v1/energy-report/day?${query}`, { signal })
    if (!signal.aborted) reportCache.invalidate(`energy:month:${reportDate.slice(0, 7)}`)
    if (!signal.aborted) reportCache.invalidate('energy:monthly-range:')
    return data
  }, { ttl: REPORT_VIEW_CACHE_TTL, force, signal })
}

export function getTaskEnergyDay(reportDate = '', { force = false, signal } = {}) {
  reportDate ||= reportToday()
  const query = new URLSearchParams()
  if (reportDate) query.set('report_date', reportDate)
  if (force) query.set('refresh', 'true')
  return reportCache.read(`energy:tasks:${reportDate}`, (signal) => request(`/api/v1/energy-report/tasks?${query}`, { signal }), { ttl: REPORT_VIEW_CACHE_TTL, force, signal })
}

export function getEnergyMonth(reportMonth, { force = false, signal } = {}) {
  const query = new URLSearchParams({ report_month: reportMonth })
  return reportCache.read(`energy:month:${reportMonth}`, (signal) => request(`/api/v1/energy-report/month?${query}`, { signal }), { ttl: REPORT_VIEW_CACHE_TTL, force, signal })
}

export function getEnergyMonthlyRange(startDate, endDate, { force = false, signal } = {}) {
  const query = new URLSearchParams({ start_date: startDate, end_date: endDate })
  const key = `energy:monthly-range:${query}`
  return reportCache.read(key, (requestSignal) => request(`/api/v1/energy-report/monthly-range?${query}`, { signal: requestSignal }), { ttl: REPORT_VIEW_CACHE_TTL, force, signal })
}

export function analyzeReport({ reportType, reportDate, periodStart, periodEnd, summaryPeriodStart, summaryPeriodEnd, dataVersion, force = false }, { signal } = {}) {
  return request('/api/v1/analysis', {
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

export function getAnalysisVersion({ reportType, reportDate }, { signal } = {}) {
  const query = new URLSearchParams({
    report_type: reportType,
    report_date: reportDate,
  })
  return request(`/api/v1/analysis/version?${query}`, { signal })
}

export function getAutomationStatus() {
  return request('/api/v1/automation/status')
}

export function getLlmUsage() {
  return request('/api/v1/llm-usage')
}

export function getAutomationConfiguration() {
  return request('/api/v1/automation/configuration')
}

export function saveAutomationConfiguration(payload) {
  return request('/api/v1/automation/configuration', {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function listAutomationSchedules() {
  return request('/api/v1/automation/schedules')
}

export function saveAutomationSchedule(payload, scheduleId = '') {
  const url = scheduleId
    ? `/api/v1/automation/schedules/${encodeURIComponent(scheduleId)}`
    : '/api/v1/automation/schedules'
  return request(url, {
    method: scheduleId ? 'PUT' : 'POST',
    body: JSON.stringify(payload),
  })
}

export function deleteAutomationSchedule(scheduleId) {
  return request(`/api/v1/automation/schedules/${encodeURIComponent(scheduleId)}`, {
    method: 'DELETE',
  })
}

export function runAutomationSchedule(scheduleId) {
  return request(`/api/v1/automation/schedules/${encodeURIComponent(scheduleId)}/run`, {
    method: 'POST',
  })
}

export function listEmailDeliveries({ page = 1, pageSize = 20 } = {}) {
  const query = new URLSearchParams({ page, page_size: pageSize })
  return request(`/api/v1/automation/deliveries?${query}`)
}

export function retryEmailDelivery(deliveryId) {
  return request(`/api/v1/automation/deliveries/${encodeURIComponent(deliveryId)}/retry`, {
    method: 'POST',
  })
}

export function getCuttingStatus(scope = 'push', { signal } = {}) {
  const query = new URLSearchParams({ scope })
  return reportCache.read(
    `cutting:status:${scope}`,
    (signal) => request(`/api/v1/cutting/status?${query}`, { signal }),
    { ttl: 60000, signal },
  )
}

export function searchCuttingDownloads(filters, { page = 1, pageSize = 10, sort = [] } = {}) {
  return request('/api/v1/cutting/downloads/search', { method: 'POST', body: JSON.stringify({ filters, page, page_size: pageSize, sort }) })
}

export function downloadCuttingFile(payload) {
  return request('/api/v1/cutting/downloads', { method: 'POST', body: JSON.stringify(payload) })
}

export function listCuttingDownloads({ page = 1, pageSize = 10 } = {}, { signal } = {}) {
  return request(`/api/v1/cutting/downloads?${new URLSearchParams({ page, page_size: pageSize })}`, { signal })
}

export function listCuttingProjects(keyword = '', limit = 1000, { signal } = {}) {
  const query = new URLSearchParams({ limit: String(limit) })
  if (keyword) query.set('keyword', keyword)
  const url = `/api/v1/cutting/projects?${query}`
  return reportCache.read(
    `cutting:projects:${query}`,
    (signal) => request(url, { signal }),
    { ttl: 300000, signal },
  )
}

export function listCuttingCatalogs(projectId, { signal } = {}) {
  const query = new URLSearchParams({ project_id: projectId })
  const url = `/api/v1/cutting/catalogs?${query}`
  return reportCache.read(
    `cutting:catalogs:${String(projectId)}`,
    (signal) => request(url, { signal }),
    { ttl: 300000, signal },
  )
}

export function listCuttingFiles({ projectId, catalogId = '', status = 'all', keyword = '', page = 1, pageSize = 10 }, { signal } = {}) {
  const query = new URLSearchParams({
    project_id: projectId,
    status,
    page,
    page_size: pageSize,
  })
  if (catalogId) query.set('catalog_id', catalogId)
  if (keyword) query.set('keyword', keyword)
  const url = `/api/v1/cutting/files?${query}`
  return reportCache.read(
    `cutting:files:${query}`,
    (signal) => request(url, { signal }),
    { ttl: 300000, signal },
  )
}

export function createCuttingDispatch(payload) {
  return request('/api/v1/cutting/dispatches', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function createCuttingDispatchBatch(payload) {
  return request('/api/v1/cutting/dispatches/batch', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function listCuttingDispatches({ page = 1, pageSize = 10 } = {}, { signal } = {}) {
  const query = new URLSearchParams({ page, page_size: pageSize })
  return request(`/api/v1/cutting/dispatches?${query}`, { signal })
}

export function getCuttingDispatch(dispatchId, { signal } = {}) {
  return request(`/api/v1/cutting/dispatches/${encodeURIComponent(dispatchId)}`, { signal })
}

export function listCuttingMachines({ signal } = {}) {
  return request('/api/v1/cutting/machines', { signal })
}

export function executeCuttingDispatch(dispatchId) {
  return request(`/api/v1/cutting/dispatches/${encodeURIComponent(dispatchId)}/execute`, {
    method: 'POST',
  })
}

export function cancelCuttingDispatch(dispatchId) {
  return request(`/api/v1/cutting/dispatches/${encodeURIComponent(dispatchId)}/cancel`, {
    method: 'POST',
  })
}

export function testDatabaseConnection(connection) {
  return request('/api/v1/database/test-connection', {
    method: 'POST',
    body: JSON.stringify(connection),
  })
}

export function previewDatabaseDataset(filters) {
  return request('/api/v1/database/preview', {
    method: 'POST',
    body: JSON.stringify(filters),
  })
}

export function validateWorkbenchSql(sql) {
  return request('/api/v1/database/sql/validate', {
    method: 'POST',
    body: JSON.stringify({ sql }),
  })
}

export function executeWorkbenchSql({ sql, maxRows, token }) {
  return request('/api/v1/database/sql/execute', {
    method: 'POST',
    headers: token ? { 'X-SQL-Workbench-Token': token } : {},
    body: JSON.stringify({ sql, max_rows: maxRows }),
  })
}

function adminHeaders(token) {
  return token ? { 'X-SQL-Workbench-Token': token } : {}
}

export function listPublishedDefinitions() {
  return request('/api/v1/designer/published-definitions')
}

export function listDesignerResources(resource, token = '') {
  return request(`/api/v1/designer/${resource}`, { headers: adminHeaders(token) })
}

export function saveDesignerResource(resource, payload, id = '', token = '') {
  const url = id
    ? `/api/v1/designer/${resource}/${encodeURIComponent(id)}`
    : `/api/v1/designer/${resource}`
  return request(url, {
    method: id ? 'PUT' : 'POST',
    headers: adminHeaders(token),
    body: JSON.stringify(payload),
  })
}

export function deleteDesignerResource(resource, id, token = '') {
  return request(`/api/v1/designer/${resource}/${encodeURIComponent(id)}`, {
    method: 'DELETE',
    headers: adminHeaders(token),
  })
}

export function validateDesignerSql(sqlText, token = '') {
  return request('/api/v1/designer/query-tasks/validate', {
    method: 'POST',
    headers: adminHeaders(token),
    body: JSON.stringify({ sql_text: sqlText }),
  })
}

export function previewDesignerQuery(queryTaskId, payload, token = '') {
  return request(`/api/v1/designer/query-tasks/${encodeURIComponent(queryTaskId)}/preview`, {
    method: 'POST',
    headers: adminHeaders(token),
    body: JSON.stringify(payload),
  })
}
