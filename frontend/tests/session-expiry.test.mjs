import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'

import { getDatabaseStatus, executeWorkbenchSql } from '../src/api/reports.js'
import {
  notifySessionExpired,
  setSessionExpiredHandler,
} from '../src/api/sessionExpiry.js'
import { reportCache } from '../src/api/reportCache.js'
import { session } from '../src/state/session.js'
import { installSessionExpiryRedirect } from '../src/state/sessionExpiry.js'

const originalFetch = globalThis.fetch
let restoreHandler = () => {}

function errorResponse(message, { sessionExpired = false } = {}) {
  return {
    ok: false,
    status: 401,
    headers: {
      get(name) {
        return sessionExpired && name.toLowerCase() === 'x-session-expired' ? '1' : null
      },
    },
    json: async () => ({ detail: message }),
  }
}

afterEach(() => {
  restoreHandler()
  restoreHandler = () => {}
  globalThis.fetch = originalFetch
  reportCache.clear()
  session.user = null
  session.systemInfo = null
  session.error = ''
  session.ready = true
})

test('concurrent expired-session responses trigger one global recovery', async () => {
  let release
  let handled = 0
  restoreHandler = setSessionExpiredHandler(() => {
    handled += 1
    return new Promise((resolve) => { release = resolve })
  })
  globalThis.fetch = async () => errorResponse('请先登录', { sessionExpired: true })

  const requests = Promise.allSettled([getDatabaseStatus(), getDatabaseStatus()])
  await new Promise((resolve) => setImmediate(resolve))

  assert.equal(handled, 1)
  release()
  const results = await requests
  assert.ok(results.every((result) => result.status === 'rejected'))
  assert.ok(results.every((result) => result.reason.message === '请先登录'))
})

test('ordinary 401 errors do not discard the signed-in session', async () => {
  let handled = 0
  restoreHandler = setSessionExpiredHandler(() => { handled += 1 })
  globalThis.fetch = async () => errorResponse('SQL 工作台管理员令牌无效')

  await assert.rejects(
    executeWorkbenchSql({ sql: 'SELECT 1', maxRows: 1, token: 'wrong' }),
    /SQL 工作台管理员令牌无效/,
  )
  assert.equal(handled, 0)
})

test('expired sessions clear account data and redirect back through login', async () => {
  session.user = { user_id: 'admin-1', role: 'admin' }
  session.systemInfo = { version: 'stale' }
  session.error = 'stale error'
  await reportCache.read('account-data', () => 'old account')

  const navigations = []
  const router = {
    currentRoute: {
      value: {
        name: 'equipment-report',
        fullPath: '/admin/reports/equipment?report_date=2026-09-22',
        matched: [{ meta: { role: 'admin' } }],
      },
    },
    async replace(target) { navigations.push(target) },
  }
  restoreHandler = installSessionExpiryRedirect(router)

  await notifySessionExpired()

  assert.equal(session.user, null)
  assert.equal(session.systemInfo, null)
  assert.equal(session.error, '')
  assert.equal(session.ready, true)
  assert.deepEqual(navigations, [{
    name: 'login',
    query: { redirect: '/admin/reports/equipment?report_date=2026-09-22' },
  }])
  assert.equal(await reportCache.read('account-data', () => 'new login'), 'new login')
})
