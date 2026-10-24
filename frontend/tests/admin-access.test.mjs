import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { parse } from '@vue/compiler-sfc'

function source(path) {
  return readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8')
}

test('settings exposes HR-backed administrator management', () => {
  const router = source('router/index.js')
  const home = source('views/SettingsHomeView.vue')

  assert.match(router, /path: 'admins'[\s\S]*AdminAccountsView\.vue/)
  assert.match(router, /path: 'admins'[\s\S]*?meta: \{ title: '管理员管理'/)
  assert.match(home, /to: '\/admin\/settings\/admins'[^\n]*title: '管理员管理'/)
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
  assert.match(template, /class="history-table account-table"/)
  assert.doesNotMatch(styles, /linear-gradient\(120deg,#083e61,#0d7391\)/)
  assert.doesNotMatch(styles, /\.panel-title\s*\{/)
})

test('login uses one account form and routes by the automatically resolved role', () => {
  const login = parse(source('views/LoginView.vue')).descriptor

  assert.match(login.template.content, /系统登录/)
  assert.match(login.template.content, /公司工号 \/ 管理员账号/)
  assert.match(login.template.content, /自动识别账号权限并进入对应页面/)
  assert.match(login.scriptSetup.content, /loginAccount\(/)
  assert.doesNotMatch(login.template.content, /login-tabs|HR账号登录|应急管理员登录/)
  assert.doesNotMatch(login.scriptSetup.content, /hrLoginAccount|mode = ref/)
})
