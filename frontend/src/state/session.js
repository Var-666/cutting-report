import { reactive } from 'vue'
import { getAuthStatus, getSystemHealth, logoutAccount } from '../api/reports'

export const session = reactive({
  ready: false,
  setupRequired: false,
  workerDatabaseAuthEnabled: false,
  user: null,
  systemInfo: null,
  error: '',
})

let loadingPromise = null

export async function loadSession({ force = false } = {}) {
  if (session.ready && !force) return session
  if (loadingPromise && !force) return loadingPromise

  loadingPromise = (async () => {
    session.error = ''
    try {
      const status = await getAuthStatus()
      session.setupRequired = status.setup_required
      session.workerDatabaseAuthEnabled = Boolean(status.worker_database_auth_enabled)
      session.user = status.user
    } catch (exception) {
      session.user = null
      session.systemInfo = null
      session.error = exception.message
      session.ready = true
      loadingPromise = null
      return session
    }
    try {
      session.systemInfo = await getSystemHealth()
    } catch {
      session.systemInfo = null
    } finally {
      session.ready = true
      loadingPromise = null
    }
    return session
  })()

  return loadingPromise
}

export async function completeAuthentication(user) {
  session.user = user
  session.setupRequired = false
  session.error = ''
  try {
    session.systemInfo = await getSystemHealth()
  } catch {
    session.systemInfo = null
  }
}

export async function endSession() {
  try {
    await logoutAccount()
  } finally {
    session.user = null
    session.error = ''
  }
}

export function roleHome(role = session.user?.role) {
  return role === 'worker' ? '/worker/tasks' : '/admin/home'
}
