// @vitest-environment jsdom

import assert from 'node:assert/strict'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { NDialogProvider } from 'naive-ui'
import { afterEach, expect, test, vi } from 'vitest'
import { h } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { session } from '../src/state/session'
import { setupSoybeanComponents } from '../src/vendor/soybean-admin/components'
import AdminAccountsView from '../src/views/AdminAccountsView.vue'
import LoginView from '../src/views/LoginView.vue'
import { stubTableLayout } from './table-layout-fixture.mjs'

enableAutoUnmount(afterEach)

test('login offers group numbers for HR accounts and keeps local administrator modes', () => {
  const login = mount(LoginView, { props: { workerDatabaseAuthEnabled: true } }).text()
  assert.match(login, /系统登录/)
  assert.match(login, /集团工号 \/ 管理员账号/)
  assert.doesNotMatch(login, /公司工号/)

  const local = mount(LoginView).text()
  assert.match(local, /管理员账号/)
  assert.doesNotMatch(local, /集团工号/)

  const setup = mount(LoginView, { props: { setupRequired: true, workerDatabaseAuthEnabled: true } }).text()
  assert.match(setup, /初始化管理员/)
  assert.match(setup, /密码（至少 8 位）/)
  assert.match(setup, /创建并登录/)
  assert.doesNotMatch(setup, /集团工号/)
})

test('administrator changes require the current password and preserve protections on the current and emergency accounts', async () => {
  const original = session.user
  const user = (user_id, display_name, auth_source) => ({ user_id, display_name, auth_source, username: user_id, role: 'admin', active: true })
  session.user = user('self', '当前管理员', 'hr_database')
  const colleague = { ...user('other', '同事', 'hr_database'), group_no: 'G002' }
  const admins = [session.user, colleague, user('emergency', '应急管理员', 'local')]
  const newAdmin = { external_id: 'hr-new', display_name: '新同事', company_no: '1001', group_no: 'G1001' }
  const changes = []
  let directoryQuery
  stubTableLayout()
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = new URL(String(input), 'http://localhost')
    const data = value => Response.json({ success: true, data: value })
    if (url.pathname === '/api/v1/auth/hr-directory') {
      directoryQuery = url.searchParams.get('query')
      return data({ items: [newAdmin] })
    }
    if (url.pathname === '/api/v1/auth/admins' && init.method !== 'POST') return data({ items: admins })
    expect(init.method).toBe('POST')
    changes.push({ path: url.pathname, body: JSON.parse(init.body) })
    if (url.pathname.endsWith('/revoke')) admins.splice(1, 1)
    return data(colleague)
  })
  try {
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/admins', component: AdminAccountsView }] })
    await router.push('/admins')
    const wrapper = mount({ render: () => h(NDialogProvider, {}, { default: () => h(RouterView) }) }, {
      attachTo: document.body,
      global: { plugins: [router, { install: setupSoybeanComponents }], stubs: { teleport: true } },
    })
    const button = (root, label) => root.findAll('button').find(element => element.text() === label)
    await flushPromises()
    const rows = wrapper.get('[aria-label="现有管理员"]').findAll('tbody tr')
    expect(button(rows[0], '撤销权限').element.disabled).toBe(true)
    expect(rows[1].text()).toContain('G002')
    expect(rows[2].text()).toContain('受保护')
    expect(rows[2].find('button').exists()).toBe(false)
    expect(wrapper.text()).toContain('姓名 / 集团工号 / 公司工号')

    await wrapper.get('input[name="hr_keyword"]').setValue('  新同事  ')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(directoryQuery).toBe('新同事')
    await button(wrapper, '授予管理员权限').trigger('click')
    await flushPromises()
    let dialog = wrapper.get('[role="dialog"][aria-modal="true"]')
    expect(dialog.text()).toContain('使用集团工号和 HR 密码登录')
    expect(dialog.text()).toContain('不会保存该密码')
    expect(button(dialog, '授予管理员权限').element.disabled).toBe(true)
    const password = () => dialog.get('input[name="current_admin_password"]')
    expect(password().attributes('autocomplete')).toBe('current-password')
    await password().setValue('fixture-grant')
    await button(dialog, '授予管理员权限').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('已授予 新同事 管理员权限')

    const target = wrapper.get('[aria-label="现有管理员"]').findAll('tbody tr').find(row => row.text().includes('G002'))
    await button(target, '撤销权限').trigger('click')
    await flushPromises()
    dialog = wrapper.get('[role="dialog"][aria-modal="true"]')
    expect(password().element.value).toBe('')
    await password().setValue('fixture-revoke')
    await button(dialog, '撤销管理员权限').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('已撤销 同事 的管理员权限')
    expect(wrapper.get('[aria-label="现有管理员"]').text()).not.toContain('G002')
    expect(changes).toEqual([
      { path: '/api/v1/auth/admins', body: { external_id: 'hr-new', current_password: 'fixture-grant' } },
      { path: '/api/v1/auth/admins/other/revoke', body: { current_password: 'fixture-revoke' } },
    ])
  } finally {
    session.user = original
  }
})
