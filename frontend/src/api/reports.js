import { reportCache } from './reportCache.js'
import { reportToday } from './reportDates.js'

const DEFAULT_HEADERS = { 'Content-Type': 'application/json' }

async function request(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    credentials: 'same-origin',
    headers: { ...DEFAULT_HEADERS, ...(options.headers || {}) },
  })
  let payload
  try {
    payload = await response.json()
  } catch {
    throw new Error('服务器返回了无法识别的响应')
  }
  if (!response.ok || payload.success === false) {
    const message = payload?.error?.message || payload?.detail || '请求失败，请稍后重试'
    throw new Error(message)
  }
  return payload.data
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
  return reportCache.read(key, (signal) => request(`/api/v1/workshop/tasks?${query}`, { signal }), { force, signal })
}

export function generateWorkshopDailyReport(reportDate) {
  return request('/api/v1/workshop/reports', {
    method: 'POST',
    body: JSON.stringify({ report_date: reportDate }),
  }).then((data) => { reportCache.invalidate('workshop:tasks:'); return data })
}

export function getLatestWorkshopProductionDate(onOrBefore, { force = false, signal } = {}) {
  const query = new URLSearchParams({ on_or_before: onOrBefore })
  return reportCache.read(`workshop:latest:${query}`, (signal) => request(`/api/v1/workshop/latest-date?${query}`, { signal }), { ttl: 60000, force, signal })
}

export function confirmWorkshopTask(taskId, reportDate) {
  return request(`/api/v1/workshop/tasks/${encodeURIComponent(taskId)}/confirm`, {
    method: 'POST',
    body: JSON.stringify({ report_date: reportDate }),
  }).then((data) => { reportCache.invalidate('workshop:tasks:'); return data })
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

export function listReports({ page = 1, pageSize = 20, force = false } = {}) {
  const query = new URLSearchParams({ page, page_size: pageSize })
  return reportCache.read(`history:${query}`, (signal) => request(`/api/v1/reports?${query}`, { signal }), { ttl: 5000, force })
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

export function getLeadershipReport(reportMonth, { force = false, signal } = {}) {
  const query = new URLSearchParams({ report_month: reportMonth })
  if (force) query.set('refresh', 'true')
  return reportCache.read(`leadership:${reportMonth}`, (signal) => request(`/api/v1/leadership-report?${query}`, { signal }), { ttl: 300000, force, signal })
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
    return data
  }, { ttl: 60000, force, signal })
}

export function getTaskEnergyDay(reportDate = '', { force = false, signal } = {}) {
  reportDate ||= reportToday()
  const query = new URLSearchParams()
  if (reportDate) query.set('report_date', reportDate)
  if (force) query.set('refresh', 'true')
  return reportCache.read(`energy:tasks:${reportDate}`, (signal) => request(`/api/v1/energy-report/tasks?${query}`, { signal }), { ttl: 60000, force, signal })
}

export function getEnergyMonth(reportMonth, { force = false, signal } = {}) {
  const query = new URLSearchParams({ report_month: reportMonth })
  return reportCache.read(`energy:month:${reportMonth}`, (signal) => request(`/api/v1/energy-report/month?${query}`, { signal }), { ttl: 60000, force, signal })
}

export function analyzeReport({ reportType, reportDate, force = false }) {
  return request('/api/v1/analysis', {
    method: 'POST',
    body: JSON.stringify({
      report_type: reportType,
      report_date: reportDate,
      force,
    }),
  })
}

export function getAutomationStatus() {
  return request('/api/v1/automation/status')
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

export function getCuttingStatus() {
  return request('/api/v1/cutting/status')
}

export function getConditionSchema(dataset) {
  return request(`/api/v1/conditions/${encodeURIComponent(dataset)}/schema`)
}

export function clearCuttingFilePages() {
  reportCache.invalidate('cutting:files:')
}

export function searchCuttingFiles(filters, { page = 1, pageSize = 10, sort = [], force = false } = {}) {
  if (force) clearCuttingFilePages()
  const body = JSON.stringify({ filters, page, page_size: pageSize, sort })
  return reportCache.read(`cutting:files:${body}`, (signal) => request('/api/v1/cutting/files/search', {
    method: 'POST', body, signal,
  }), { ttl: 60000 })
}

export function searchCuttingDownloads(filters, { page = 1, pageSize = 10, sort = [] } = {}) {
  return request('/api/v1/cutting/downloads/search', { method: 'POST', body: JSON.stringify({ filters, page, page_size: pageSize, sort }) })
}

export function downloadCuttingFile(payload) {
  return request('/api/v1/cutting/downloads', { method: 'POST', body: JSON.stringify(payload) })
}

export function listCuttingDownloads({ page = 1, pageSize = 10 } = {}) {
  return request(`/api/v1/cutting/downloads?${new URLSearchParams({ page, page_size: pageSize })}`)
}

export { canChooseSaveLocation } from './saveFile.js'
import { saveFileFromUrl } from './saveFile.js'

export function saveCuttingDownload(downloadId, filename, options) {
  return saveFileFromUrl(`/api/v1/cutting/downloads/${encodeURIComponent(downloadId)}/file`, filename, options)
}

export function listCuttingProjects(keyword = '') {
  const query = new URLSearchParams({ limit: 100 })
  if (keyword) query.set('keyword', keyword)
  return request(`/api/v1/cutting/projects?${query}`)
}

export function listCuttingCatalogs(projectId) {
  const query = new URLSearchParams({ project_id: projectId })
  return request(`/api/v1/cutting/catalogs?${query}`)
}

export function listCuttingFiles({ projectId, catalogId = '', status = 'all', keyword = '', page = 1, pageSize = 10 }) {
  const query = new URLSearchParams({
    project_id: projectId,
    status,
    page,
    page_size: pageSize,
  })
  if (catalogId) query.set('catalog_id', catalogId)
  if (keyword) query.set('keyword', keyword)
  return request(`/api/v1/cutting/files?${query}`)
}

export function createCuttingDispatch(payload) {
  return request('/api/v1/cutting/dispatches', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function listCuttingDispatches({ page = 1, pageSize = 10 } = {}) {
  const query = new URLSearchParams({ page, page_size: pageSize })
  return request(`/api/v1/cutting/dispatches?${query}`)
}

export function listCuttingMachines() {
  return request('/api/v1/cutting/machines')
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
