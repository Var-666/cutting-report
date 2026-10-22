import { resetDeviceContext } from '../api/deviceContext.js'
import { reactive } from 'vue'
import { getAuthStatus, getSystemHealth, logoutAccount } from '../api/reports.js'
import { startReportPreload, stopReportPreload } from './reportPreload.js'
import { deviceSelection, loadDevices } from './devices.js'

export const session = reactive({
  ready: false,
  setupRequired: false,
  workerDatabaseAuthEnabled: false,
  user: null,
  systemInfo: null,
  error: '',
})

let loadingPromise = null
let sessionGeneration = 0

function preloadDeviceReports(user, generation) {
  if (!user) { stopReportPreload(); return }
  // Validate the remembered device before any production report warmup starts.
  loadDevices().then(() => {
    if (generation === sessionGeneration && deviceSelection.ready) startReportPreload(user)
  })
}

function refreshSystemInfo(generation) {
  getSystemHealth().then((info) => {
    if (generation === sessionGeneration) session.systemInfo = info
  }).catch(() => {
    if (generation === sessionGeneration) session.systemInfo = null
  })
}

export async function loadSession({ force = false } = {}) {
  if (session.ready && !force) return session
  if (loadingPromise && !force) return loadingPromise
  const generation = ++sessionGeneration
  if (force) stopReportPreload()
  loadingPromise = (async () => {
    session.error = ''
    try {
      const status = await getAuthStatus()
      if (generation !== sessionGeneration) return session
      session.setupRequired = status.setup_required
      session.workerDatabaseAuthEnabled = Boolean(status.worker_database_auth_enabled)
      session.user = status.user
      preloadDeviceReports(status.user, generation)
    } catch (exception) {
      if (generation !== sessionGeneration) return session
      stopReportPreload()
      session.user = null
      session.systemInfo = null
      session.error = exception.message
      session.ready = true
      loadingPromise = null
      return session
    }
    refreshSystemInfo(generation)
    session.ready = true
    loadingPromise = null
    return session
  })()

  return loadingPromise
}

export async function completeAuthentication(user) {
  const generation = ++sessionGeneration
  loadingPromise = null
  stopReportPreload()
  session.user = user
  session.ready = true
  session.setupRequired = false
  session.error = ''
  session.systemInfo = null
  preloadDeviceReports(user, generation)
  refreshSystemInfo(generation)
}

export function expireSession() {
  resetDeviceContext()
  sessionGeneration += 1
  loadingPromise = null
  stopReportPreload()
  session.user = null
  session.systemInfo = null
  session.error = ''
  session.ready = true
}

export async function endSession() {
  resetDeviceContext()
  const generation = ++sessionGeneration
  loadingPromise = null
  stopReportPreload()
  session.user = null
  session.systemInfo = null
  try {
    await logoutAccount()
  } finally {
    if (generation === sessionGeneration) session.error = ''
  }
}

export function roleHome(role = session.user?.role) {
  return role === 'worker' ? '/worker/tasks' : '/admin/home'
}
