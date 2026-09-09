import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { parse } from '@vue/compiler-sfc'

function source(path) {
  return readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8')
}

test('settings exposes HR-backed administrator management', () => {
  const router = source('router/index.js')
  const layout = source('layouts/SettingsLayout.vue')
  const home = source('views/SettingsHomeView.vue')

  assert.match(router, /path: 'admins'[\s\S]*AdminAccountsView\.vue/)
  assert.match(layout, /\/admin\/settings\/admins">管理员管理/)
  assert.match(home, /从 HR 人员库授予管理员权限/)
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

  assert.match(template, /class="database-hero admin-accounts-hero"/)
  assert.match(template, /class="security-badge admin-security-badge"/)
  assert.match(template, /class="history-table account-table"/)
  assert.match(template, /仅保存管理员权限映射/)
  assert.doesNotMatch(styles, /linear-gradient\(120deg,#083e61,#0d7391\)/)
  assert.doesNotMatch(styles, /\.panel-title\s*\{/)
})

test('HR login uses the original HR password and routes by granted role', () => {
  const login = parse(source('views/LoginView.vue')).descriptor

  assert.match(login.template.content, /HR账号登录/)
  assert.match(login.template.content, /HR 原密码登录/)
  assert.match(login.template.content, /根据已授予的权限进入对应页面/)
  assert.match(login.scriptSetup.content, /hrLoginAccount\(workerForm\)/)
})
