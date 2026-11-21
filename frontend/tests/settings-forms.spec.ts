// @vitest-environment jsdom

import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { NInputNumber, NMessageProvider, NDialogProvider } from 'naive-ui'
import { createPinia } from 'pinia'
import { afterEach, expect, test, vi } from 'vitest'
import { h } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import AppSelect from '../src/components/AppSelect.vue'
import AutomationView from '../src/views/AutomationView.vue'
import DatabaseView from '../src/views/DatabaseView.vue'
import { setupSoybeanComponents } from '../src/vendor/soybean-admin/components'

enableAutoUnmount(afterEach)

async function mountSettings(component: typeof AutomationView | typeof DatabaseView) {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component }, { path: '/away', component: { render: () => '已离开设置' } }],
  })
  await router.push('/')
  const wrapper = mount({
    render: () => h(NMessageProvider, {}, { default: () => h(NDialogProvider, {}, { default: () => h(RouterView) }) }),
  }, {
    attachTo: document.body,
    global: {
      plugins: [router, createPinia(), { install: setupSoybeanComponents }],
      components: { AppSelect, NInputNumber },
      stubs: { teleport: true, DatabaseSourcesPanel: true, ReportCacheSettings: true },
    },
  })
  await flushPromises()
  return { wrapper, router }
}

test('database connection tests submit the named credentials and announce the reported failure', async () => {
  let connection: object | undefined
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input)
    if (url.endsWith('production-lines')) return Response.json({ success: true, data: { items: [], default_device_id: '' } })
    if (url.endsWith('/status')) return Response.json({ success: true, data: { driver: 'ODBC Driver 18 for SQL Server' } })
    if (url.endsWith('/test-connection') && typeof init?.body === 'string') {
      connection = JSON.parse(init.body)
      return Response.json({ detail: '连接凭据无效' }, { status: 502 })
    }
    throw new Error(`Unexpected request ${url}`)
  })
  const { wrapper } = await mountSettings(DatabaseView)
  await wrapper.get('input[name="test_host"]').setValue('sql.example.com')
  await wrapper.get('input[name="test_database"]').setValue('stats')
  await wrapper.get('input[name="test_username"]').setValue('report-reader')
  const password = wrapper.get('input[name="test_password"]')
  expect(password.attributes('autocomplete')).toBe('current-password')
  await password.setValue('example-password')
  await wrapper.get('form:has(input[name="test_host"])').trigger('submit')
  await flushPromises()
  expect(connection).toMatchObject({ host: 'sql.example.com', database: 'stats', username: 'report-reader', password: 'example-password' })
  expect(wrapper.get('[role="alert"]').text()).toContain('连接凭据无效')
})

test('automation protects the sender draft and submits the email contract', async () => {
  let submitted: object | undefined
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input)
    if (url.includes('/cancel')) return Response.json({})
    if (url.endsWith('/configuration') && init?.method === 'PUT' && typeof init.body === 'string') {
      submitted = JSON.parse(init.body)
      return Response.json({ success: true, data: { message: '已保存邮件配置' } })
    }
    if (url.endsWith('/configuration')) return Response.json({ success: true, data: { smtp_from_email: 'before@example.com' } })
    if (url.endsWith('/status')) return Response.json({ success: true, data: { automation_enabled: true, smtp: { enabled: false } } })
    if (url.endsWith('/schedules') || url.includes('/deliveries?')) return Response.json({ success: true, data: { items: [], total: 0 } })
    throw new Error(`Unexpected request ${url}`)
  })
  const { wrapper } = await mountSettings(AutomationView)
  const open = wrapper.findAll('button').find(button => button.text() === '运行与邮件参数')
  if (!open) throw new Error('Configuration button is missing')
  await open.trigger('click')
  await flushPromises()
  const sender = wrapper.get('input[name="smtp_from_email"]')
  expect(sender.attributes('type')).toBe('email')
  expect(sender.attributes('inputmode')).toBe('email')
  await sender.setValue('reports@example.com')
  const pendingUnload = new Event('beforeunload', { cancelable: true })
  expect(window.dispatchEvent(pendingUnload)).toBe(false)
  expect((sender.element as HTMLInputElement).value).toBe('reports@example.com')
  await wrapper.get('form:has(input[name="smtp_from_email"])').trigger('submit')
  await flushPromises()
  expect(submitted).toMatchObject({ smtp_from_email: 'reports@example.com', smtp_password: null })
  expect(wrapper.text()).toContain('已保存邮件配置')
  expect(window.dispatchEvent(new Event('beforeunload', { cancelable: true }))).toBe(true)
})
