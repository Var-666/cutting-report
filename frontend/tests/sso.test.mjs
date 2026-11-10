import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'
import { renderComponent } from './vue-component-helpers.mjs'
import { loadSession, session } from '../src/state/session.ts'

const originalFetch = globalThis.fetch
afterEach(() => { globalThis.fetch = originalFetch; session.ssoEnabled = false })

test('SSO failure keeps the password form and displays an escaped message without an SSO button', async () => {
  const html = await renderComponent('views/LoginView', {
    ssoError: '认证身份未关联，请联系管理员。<script>unsafe</script>',
  })
  assert.doesNotMatch(html, /统一认证登录/)
  assert.match(html, /name="username"/)
  assert.match(html, /name="password"/)
  assert.equal((html.match(/<button\b/g) || []).length, 1)
  assert.match(html, /role="alert"/)
  assert.match(html, /认证身份未关联/)
  assert.doesNotMatch(html, /<script>unsafe/)
})

test('ordinary login and bootstrap retain the existing login choices', async () => {
  const disabled = await renderComponent('views/LoginView')
  assert.doesNotMatch(disabled, /统一认证登录/)
  assert.match(disabled, /管理员账号/)
  const bootstrap = await renderComponent('views/LoginView', { setupRequired: true, ssoError: '不应显示的认证错误' })
  assert.match(bootstrap, /初始化管理员/)
  assert.doesNotMatch(bootstrap, /统一认证登录/)
  assert.doesNotMatch(bootstrap, /不应显示的认证错误/)
})

test('session consumes the server SSO flag and clears it when disabled', async () => {
  for (const enabled of [true, false]) {
    globalThis.fetch = async url => ({
      ok: true,
      json: async () => ({ data: String(url).includes('/auth/status')
        ? { setup_required: false, worker_database_auth_enabled: false, sso_enabled: enabled, authenticated: false, user: null }
        : {} }),
    })
    await loadSession({ force: true })
    assert.equal(session.ssoEnabled, enabled)
    assert.equal(session.user, null)
  }
})
