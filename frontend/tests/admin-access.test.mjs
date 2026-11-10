import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { parse } from '@vue/compiler-sfc'
import { renderComponent } from './vue-component-helpers.mjs'

function source(path) {
  return readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8')
}

test('settings exposes HR-backed administrator management', () => {
  const router = source('router/index.ts')

  assert.match(router, /path: 'admins'[\s\S]*AdminAccountsView\.vue/)
  assert.match(router, /path: 'admins'[\s\S]*?meta: \{ title: '管理员管理'/)
})

test('administrator permission changes require the current administrator password', () => {
  const descriptor = parse(source('views/AdminAccountsView.vue')).descriptor
  const script = descriptor.scriptSetup.content
  const template = descriptor.template.content

  assert.match(template, /当前管理员密码/)
  assert.match(template, /不会保存该密码/)
  assert.match(script, /grantAdminAccess\(dialog\.target\.external_id, dialog\.password\)/)
  assert.match(script, /revokeAdminAccess\(dialog\.target\.user_id, dialog\.password\)/)
  assert.doesNotMatch(template, /被授权人员密码|新管理员密码/)
})

test('administrator management follows the shared settings UI baseline', () => {
  const descriptor = parse(source('views/AdminAccountsView.vue')).descriptor
  const template = descriptor.template.content
  const styles = descriptor.styles.map((style) => style.content).join('\n')

  assert.match(template, /<ManagementPageHero/)
  assert.match(template, /title="管理员管理"/)
  assert.match(template, /status-label="HR 身份认证"/)
  assert.match(template, /status-note="系统仅保存管理员权限映射"/)
  assert.match(template, /<AppReportTable :columns="adminTableColumns"[\s\S]*:row-key="admin => admin.user_id"/)
  assert.doesNotMatch(styles, /linear-gradient\(120deg,#083e61,#0d7391\)/)
  assert.doesNotMatch(styles, /\.panel-title\s*\{/)
})

test('login offers group numbers for HR accounts and keeps local administrator modes', async () => {
  const login = await renderComponent('views/LoginView', { workerDatabaseAuthEnabled: true })
  assert.match(login, /系统登录/)
  assert.match(login, /集团工号 \/ 管理员账号/)
  assert.match(login, /自动识别账号权限并进入对应页面/)
  assert.doesNotMatch(login, /公司工号/)

  const local = await renderComponent('views/LoginView')
  assert.match(local, /使用管理员账号和密码登录/)
  assert.doesNotMatch(local, /集团工号/)

  const setup = await renderComponent('views/LoginView', { setupRequired: true, workerDatabaseAuthEnabled: true })
  assert.match(setup, /初始化管理员/)
  assert.match(setup, /创建并登录/)
  assert.doesNotMatch(setup, /集团工号/)
})
