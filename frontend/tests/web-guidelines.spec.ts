// @vitest-environment jsdom

import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, expect, test, vi } from 'vitest'
import { nextTick } from 'vue'
import App from '../src/App.vue'
import { session } from '../src/state/session'
import { createMemoryHistory, createRouter } from 'vue-router'
import { loginAccount } from '../src/api/auth'
import DispatchBatchDialog from '../src/components/dispatch/DispatchBatchDialog.vue'
import HomeKpiCard from '../src/components/HomeKpiCard.vue'
import WorkshopBatchConfirmationDialog from '../src/components/workshop/WorkshopBatchConfirmationDialog.vue'
import WorkerLayout from '../src/layouts/WorkerLayout.vue'
import { useAppStore } from '../src/state/app'
import AppSelect from '../src/components/AppSelect.vue'
import { setupSoybeanComponents } from '../src/vendor/soybean-admin/components'
import LoginView from '../src/views/LoginView.vue'

vi.mock('../src/api/auth', async importOriginal => ({
  ...await importOriginal<typeof import('../src/api/auth')>(),
  loginAccount: vi.fn(),
}))

enableAutoUnmount(afterEach)

test('the application announces authentication progress and initialization errors', async () => {
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
  const wrapper = mount(App, { global: { plugins: [createPinia(), { install: setupSoybeanComponents }] } })
  try {
    const status = wrapper.get('[role="status"]')
    expect(status.text()).toBe('正在检查登录状态…')
    expect(status.attributes('aria-live')).toBe('polite')
    session.error = '登录服务不可用'
    session.ready = true
    await nextTick()
    const message = wrapper.get('[role="alert"]')
    expect(message.text()).toContain('登录服务不可用')
    expect(message.get('button').text()).toBe('重新连接')
  } finally {
    session.error = ''
    session.ready = false
  }
})

test('the worker skip link leads to the focusable main region', async () => {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: { template: '<p>待确认任务</p>' } }],
  })
  await router.push('/')
  const wrapper = mount(WorkerLayout, { global: { plugins: [createPinia(), router, { install: setupSoybeanComponents }], components: { AppSelect } } })
  const skip = wrapper.get('a[href="#worker-main"]')
  const main = wrapper.get(skip.attributes('href'))
  expect(skip.text()).toBe('跳到主要内容')
  expect(main.element.tagName).toBe('MAIN')
  expect(main.attributes('tabindex')).toBe('-1')
})

test('login sends the entered credentials and moves keyboard focus to a failed submission', async () => {
  const login = vi.mocked(loginAccount)
  login.mockRejectedValueOnce(new Error('账号或密码错误'))
  const user = { user_id: 'u1', username: '工人01', display_name: '工人', role: 'worker' as const, active: true }
  login.mockResolvedValueOnce({ user, expires_at: '2026-12-31T00:00:00Z' })
  const wrapper = mount(LoginView, { attachTo: document.body, props: { workerDatabaseAuthEnabled: true } })
  const username = wrapper.get('input[name="username"]')
  expect(username.attributes('required')).toBeDefined()
  expect(username.attributes('spellcheck')).toBe('false')
  const password = wrapper.get('input[name="password"]')
  expect(password.attributes('autocomplete')).toBe('current-password')

  await username.setValue(' 工人01 ')
  await password.setValue('secret123')
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  expect(login).toHaveBeenCalledWith({ username: '工人01', password: 'secret123' })
  const alert = wrapper.get('[role="alert"]')
  expect(alert.text()).toContain('账号或密码错误')
  expect(document.activeElement).toBe(alert.element)
  expect(wrapper.emitted('authenticated')).toBeUndefined()

  await wrapper.get('form').trigger('submit')
  await flushPromises()
  expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  expect(wrapper.emitted('authenticated')).toEqual([[user]])
})

test('KPI text and accessible labels distinguish measured values, zero, missing and loading states', async () => {
  const pinia = createPinia()
  useAppStore(pinia).reducedMotion = true
  const wrapper = mount(HomeKpiCard, {
    props: { title: '切割长度', unit: '米', precision: 1, value: 9714.2 },
    global: { plugins: [pinia] },
  })
  expect(wrapper.attributes('aria-label')).toBe('切割长度：9,714.2 米')
  expect(wrapper.text()).toContain('9,714.2')
  await wrapper.setProps({ value: 0 })
  expect(wrapper.attributes('aria-label')).toBe('切割长度：0.0 米')
  expect(wrapper.text()).toContain('0.0')
  await wrapper.setProps({ value: null })
  expect(wrapper.attributes('aria-label')).toBe('切割长度：暂无数据')
  expect(wrapper.text()).toContain('暂无数据')
  expect(wrapper.text()).not.toContain('米')
  await wrapper.setProps({ value: 0, loading: true })
  expect(wrapper.attributes('aria-label')).toBe('切割长度：正在加载')
  expect(wrapper.get('[role="status"]').attributes('aria-label')).toBe('正在获取生产数据')
  expect(wrapper.text()).not.toContain('0.0')
})

test('the workshop confirmation announces a failure and prevents dismissal while submitting', async () => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  const wrapper = mount(WorkshopBatchConfirmationDialog, {
    attachTo: document.body,
    props: {
      batchConfirmation: { visible: true, saving: true, error: '任务数据已更新', tasks: [] },
      user: { user_id: 'worker-1', username: 'worker-1', display_name: '测试工人', role: 'worker', active: true },
    },
    global: { plugins: [{ install: setupSoybeanComponents }], stubs: { teleport: true } },
  })
  await flushPromises()
  const dialog = wrapper.get('[role="dialog"][aria-modal="true"]')
  const heading = wrapper.get(`#${dialog.attributes('aria-labelledby')}`)
  expect(heading.text()).toBe('确认以下任务由我完成')
  expect(dialog.get('[role="alert"]').text()).toBe('任务数据已更新')
  const close = dialog.get('button[aria-label="关闭"]')
  expect(close.attributes('disabled')).toBeDefined()
  await close.trigger('click')
  expect(wrapper.emitted('close')).toBeUndefined()
  await wrapper.setProps({ batchConfirmation: { visible: true, saving: false, error: '', tasks: [] } })
  const unlocked = wrapper.get('button[aria-label="关闭"]')
  expect(unlocked.attributes('disabled')).toBeUndefined()
  await unlocked.trigger('click')
  expect(wrapper.emitted('close')).toEqual([[]])
})

test('dispatch names editable fields by file and blocks confirmation during a send', async () => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  const item = {
    referenceKey: 'file-1', documentId: '1', versionId: 'v1', filePath: 'files/one.gen',
    filename: 'one.gen', version: 'V1', parsed: null, segmentName: '806C',
    material: 'A0', thickness: 8, quantity: 1, status: 'pending' as const,
    error: '', dispatchId: '', retryable: false, statusChecked: false,
  }
  const batch = {
    visible: true, saving: false, resultsReady: false, items: [item], completed: 0, total: 1,
    currentFilename: '', error: '', nonStandardConfirmed: false, overrideConfirmed: false,
  }
  const wrapper = mount(DispatchBatchDialog, {
    props: { pushBatch: batch, machines: [], machineName: String, canPush: true, machineIp: 'fixture', bulkQuantity: 1 },
    attachTo: document.body,
    global: { plugins: [{ install: setupSoybeanComponents }], components: { AppSelect }, stubs: { teleport: true } },
  })
  await flushPromises()
  const dialog = wrapper.get('[role="dialog"][aria-modal="true"]')
  const material = dialog.get('input[aria-label="one.gen 材质"]')
  const thickness = dialog.get('input[aria-label="one.gen 板厚"]')
  expect(thickness.attributes('inputmode')).toBe('decimal')
  await material.setValue('B0')
  expect(wrapper.emitted('parametersChanged')).toHaveLength(1)
  await wrapper.setProps({ pushBatch: { ...batch, saving: true } })
  const sending = wrapper.get('[role="dialog"][aria-modal="true"]')
  expect(sending.attributes('aria-busy')).toBe('true')
  expect(sending.get('input[aria-label="one.gen 材质"]').attributes('disabled')).toBeDefined()
  expect(sending.get('button[aria-label="关闭批量推送窗口"]').attributes('disabled')).toBeDefined()
})
