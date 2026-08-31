const DEFAULT_HEADERS = { 'Content-Type': 'application/json' }
const LEADERSHIP_CACHE_TTL_MS = 5 * 60 * 1000
const leadershipReportCache = new Map()
const leadershipReportRequests = new Map()

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

export function listWorkshopTasks({ reportDate, projectName = '', confirmationStatus = 'all', sortMode = 'manager', page = 1, pageSize = 10 }) {
  const query = new URLSearchParams({
    report_date: reportDate,
    confirmation_status: confirmationStatus,
    sort_mode: sortMode,
    page,
    page_size: pageSize,
  })
  if (projectName) query.set('project_name', projectName)
  return request(`/api/v1/workshop/tasks?${query}`)
}

export function generateWorkshopDailyReport(reportDate) {
  return request('/api/v1/workshop/reports', {
    method: 'POST',
    body: JSON.stringify({ report_date: reportDate }),
  })
}

export function getLatestWorkshopProductionDate(onOrBefore) {
  const query = new URLSearchParams({ on_or_before: onOrBefore })
  return request(`/api/v1/workshop/latest-date?${query}`)
}

export function confirmWorkshopTask(taskId, reportDate) {
  return request(`/api/v1/workshop/tasks/${encodeURIComponent(taskId)}/confirm`, {
    method: 'POST',
    body: JSON.stringify({ report_date: reportDate }),
  })
}

export function createReport(form) {
  return request('/api/v1/reports', {
    method: 'POST',
    body: JSON.stringify(form),
  })
}

export function getReportStatus(reportId) {
  return request(`/api/v1/reports/${encodeURIComponent(reportId)}/status`)
}

export function listReports({ page = 1, pageSize = 20 } = {}) {
  const query = new URLSearchParams({ page, page_size: pageSize })
  return request(`/api/v1/reports?${query}`)
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

export function getLeadershipReport(reportMonth, { force = false } = {}) {
  const now = Date.now()
  const cached = leadershipReportCache.get(reportMonth)
  if (!force && cached && cached.expiresAt > now) {
    return Promise.resolve(cached.data)
  }
  if (leadershipReportRequests.has(reportMonth)) {
    return leadershipReportRequests.get(reportMonth)
  }

  const query = new URLSearchParams({ report_month: reportMonth })
  if (force) query.set('refresh', 'true')
  const pending = request(`/api/v1/leadership-report?${query}`)
    .then((data) => {
      leadershipReportCache.set(reportMonth, {
        data,
        expiresAt: Date.now() + LEADERSHIP_CACHE_TTL_MS,
      })
      return data
    })
    .finally(() => leadershipReportRequests.delete(reportMonth))
  leadershipReportRequests.set(reportMonth, pending)
  return pending
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
  const query = new URLSearchParams()
  if (reportDate) query.set('report_date', reportDate)
  if (force) query.set('refresh', 'true')
  return request(`/api/v1/energy-report/day?${query}`, { signal })
}

export function getEnergyMonth(reportMonth, { signal } = {}) {
  const query = new URLSearchParams({ report_month: reportMonth })
  return request(`/api/v1/energy-report/month?${query}`, { signal })
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

export function searchCuttingFiles(filters, { page = 1, pageSize = 10 } = {}) {
  return request('/api/v1/cutting/files/search', { method: 'POST', body: JSON.stringify({ filters, page, page_size: pageSize }) })
}

export function searchCuttingDownloads(filters, { page = 1, pageSize = 10 } = {}) {
  return request('/api/v1/cutting/downloads/search', { method: 'POST', body: JSON.stringify({ filters, page, page_size: pageSize }) })
}

export function downloadCuttingFile(payload) {
  return request('/api/v1/cutting/downloads', { method: 'POST', body: JSON.stringify(payload) })
}

export function listCuttingDownloads({ page = 1, pageSize = 10 } = {}) {
  return request(`/api/v1/cutting/downloads?${new URLSearchParams({ page, page_size: pageSize })}`)
}

export async function saveCuttingDownload(downloadId, filename) {
  const response = await fetch(`/api/v1/cutting/downloads/${encodeURIComponent(downloadId)}/file`, { credentials: 'same-origin' })
  if (!response.ok) {
    const payload = await response.json().catch(() => null)
    throw new Error(payload?.detail || '本地文件读取失败，请重新下载')
  }
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  setTimeout(() => URL.revokeObjectURL(url), 60000)
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
