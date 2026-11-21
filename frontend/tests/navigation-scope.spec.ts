// @vitest-environment jsdom

import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { defineComponent, h, onUnmounted, ref } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { getAutomationStatus, saveAutomationSchedule } from '../src/api/automation'
import { getDatabaseConfiguration, saveDatabaseConfiguration, testWeldingConnection } from '../src/api/configuration'
import { getDeviceId, registerDeviceSwitchQuery, resetDeviceContext, selectDevice, setDeviceCatalog } from '../src/api/deviceContext'
import { getDeviceCatalog, getProductionLines } from '../src/api/devices'
import AppDatePicker from '../src/components/AppDatePicker.vue'
import AppSelect from '../src/components/AppSelect.vue'
import { useAutomationSchedules } from '../src/composables/useAutomationSchedules'
import { useDeviceSelection } from '../src/composables/useDeviceSelection'
import AdminLayout from '../src/layouts/AdminLayout.vue'
import WorkerLayout from '../src/layouts/WorkerLayout.vue'
import { setupI18n } from '../src/locales'
import router from '../src/router'
import { useAppStore } from '../src/state/app'
import { session } from '../src/state/session'
import { useRouteStore } from '../src/store/modules/route'
import { useThemeStore } from '../src/store/modules/theme'
import type { DeviceCatalog } from '../src/types/devices'
import { setupSoybeanComponents } from '../src/vendor/soybean-admin/components'

vi.mock('vue-router', async importOriginal => {
  const actual = await importOriginal<typeof import('vue-router')>()
  return { ...actual, createWebHistory: actual.createMemoryHistory }
})

vi.mock('../src/api/devices', () => ({ getProductionLines: vi.fn(), getDeviceCatalog: vi.fn() }))

const catalog: DeviceCatalog = {
  default_device_id: 'cut-a',
  items: [
    { id: 'cut-a', type: 'cutting', name: '切割A', type_name: '切割', source_key: 'cutting', capabilities: [], home_path: '/admin/cutting/home', enabled: true },
    { id: 'cut-b', type: 'cutting', name: '切割B', type_name: '切割', source_key: 'cutting', capabilities: [], home_path: '/admin/cutting/home', enabled: true },
    { id: 'weld-a', type: 'welding', name: '焊接A', type_name: '焊接', source_key: 'welding', capabilities: [], home_path: '/admin/welding/home', enabled: true },
  ],
}

enableAutoUnmount(afterEach)
beforeEach(async () => {
  vi.stubGlobal('scrollTo', vi.fn())
  session.ready = true
  session.user = { user_id: 'admin', username: 'admin', display_name: '管理员', role: 'admin', active: true }
  vi.mocked(getProductionLines).mockResolvedValue(catalog)
  await router.push('/admin/home')
})
afterEach(() => {
  resetDeviceContext()
  localStorage.clear()
})

function mountSelection() {
  let selection: ReturnType<typeof useDeviceSelection> | undefined
  mount(defineComponent({
    setup() {
      selection = useDeviceSelection()
      return () => null
    },
  }), { global: { plugins: [router] } })
  if (!selection) throw new Error('Device selection did not mount')
  return selection
}

test('shared modules keep their navigation selection and their own secondary menus', async () => {
  const menus = useRouteStore(createPinia())
  await mountSelection().enterLine('weld-a')
  for (const [path, key, secondary] of [
    ['/admin/home', 'admin-home', false],
    ['/admin/pdm-statistics', 'pdm-statistics', false],
    ['/admin/automation', 'automation', false],
    ['/admin/settings/database', '/admin/settings', true],
    ['/admin/operations/logs', '/admin/operations', true],
  ] as const) {
    await router.push(path)
    expect(menus.primaryKey).toBe(key)
    expect(menus.secondaryMenus.length > 0).toBe(secondary)
    expect(getDeviceId()).toBe('weld-a')
  }
  expect(menus.secondaryMenus.map(menu => menu.key)).toContain('operations-incidents')
  await router.push('/admin/settings')
  expect(router.currentRoute.value.path).toBe('/admin/settings/database')
  expect(menus.secondaryMenus.map(menu => menu.key)).toContain('database-monitors')
  expect(menus.secondaryMenus.find(menu => menu.key === 'configuration-diagnostics')?.routePath)
    .toBe('/admin/settings/configuration-diagnostics')
  expect(menus.secondaryMenus.find(menu => menu.key === 'admin-accounts')?.routePath).toBe('/admin/settings/admins')
  await router.push('/admin/settings/automation?page=2')
  expect(router.currentRoute.value.fullPath).toBe('/admin/automation?page=2')
  expect(menus.primaryKey).toBe('automation')
})

test('switching lines updates the route, the visible capabilities and the request scope', async () => {
  const menus = useRouteStore(createPinia())
  const selection = mountSelection()
  await selection.enterLine('cut-a')
  expect(router.currentRoute.value.name).toBe('cutting-home')
  expect(menus.primaryKey).toBe('line:cut-a')
  expect(menus.secondaryMenus.map(menu => menu.key)).toContain('cutting-dispatch')
  await selection.enterLine('weld-a')
  expect(router.currentRoute.value.name).toBe('welding-home')
  expect(getDeviceId()).toBe('weld-a')
  expect(menus.primaryKey).toBe('line:weld-a')
  expect(menus.secondaryMenus.map(menu => menu.key)).toContain('welding-home')
  expect(menus.secondaryMenus.map(menu => menu.key)).not.toContain('cutting-dispatch')
  await router.push('/admin/welding/reports/daily')
  expect(menus.getSelectedMenuKeyPath('welding-daily', menus.navigationMenus))
    .toEqual(['type:welding', 'line:weld-a', '/admin/welding/reports', 'welding-daily'])

  const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async () =>
    Response.json({ success: true, data: {} }))
  await getDatabaseConfiguration('cut-b')
  await saveDatabaseConfiguration({ production: {}, worker: {}, file: {} }, 'cut-b')
  await testWeldingConnection({}, 'weld-a')
  await getAutomationStatus()
  await saveAutomationSchedule({ device_id: 'cut-a', name: '日报推送', report_type: 'workshop', recipients: [] })
  expect(fetch.mock.calls.map(([, init]) => new Headers(init?.headers).get('X-Production-Line')))
    .toEqual(['cut-b', 'cut-b', 'weld-a', '', ''])
  expect(fetch.mock.lastCall?.[1]?.body).toContain('"device_id":"cut-a"')
  expect(getDeviceId()).toBe('weld-a')
})

test('same-type switches preserve the applied query and cancelled navigation restores the old scope', async () => {
  const selection = mountSelection()
  await selection.enterLine('cut-b')
  await router.push('/admin/reports/workshop')
  const stop = registerDeviceSwitchQuery('workshop-report', () => ({ start_date: '2026-11-01' }))
  try {
    await selection.switchDevice('cut-a')
    expect(router.currentRoute.value.query).toMatchObject({ start_date: '2026-11-01' })
    expect(getDeviceId()).toBe('cut-a')
    await selection.switchDevice('cut-b')
    expect(getDeviceId()).toBe('cut-b')
    const removeGuard = router.beforeEach(() => false)
    try {
      await selection.enterLine('weld-a')
      expect(getDeviceId()).toBe('cut-b')
      expect(router.currentRoute.value.name).toBe('workshop-report')
    } finally { removeGuard() }
  } finally { stop() }
})

test('the admin menu toggles, switches modes and exposes a skip link to the main region', async () => {
  const pinia = createPinia()
  const theme = useThemeStore(pinia)
  useAppStore(pinia).siderCollapse = false
  theme.setThemeLayout('vertical-mix')
  const wrapper = mount(AdminLayout, {
    attachTo: document.body,
    global: {
      plugins: [pinia, router, { install: setupSoybeanComponents }, { install: setupI18n }],
      components: { AppSelect, AppDatePicker },
      stubs: { AdminPageView: true, OperationsNotice: true, ThemeDrawer: true, ThemeTabs: true, ThemeSettings: true, ThemeHeaderTools: true },
    },
  })
  await flushPromises()
  const main = wrapper.get(wrapper.get('a[href="#admin-main"]').attributes('href'))
  expect(main.element.tagName).toBe('MAIN')
  expect(main.attributes('tabindex')).toBe('-1')
  const toggler = wrapper.get('button[aria-label="收起主导航"]')
  expect(toggler.attributes('aria-expanded')).toBe('true')
  await toggler.trigger('click')
  expect(wrapper.get('button[aria-label="展开主导航"]').attributes('aria-expanded')).toBe('false')
  await toggler.trigger('click')
  expect(toggler.attributes('aria-expanded')).toBe('true')
  theme.setThemeLayout('horizontal')
  await flushPromises()
  expect(wrapper.find('button[aria-label="收起主导航"]').exists()).toBe(false)
  expect(wrapper.find('#admin-device-rail').exists()).toBe(false)

  theme.setThemeLayout('vertical')
  useAppStore(pinia).setMobile(true)
  useAppStore(pinia).siderCollapse = true
  await flushPromises()
  const open = wrapper.get('button[aria-label="展开菜单"]')
  await open.trigger('click')
  const drawer = wrapper.get('[role="dialog"][aria-label="管理员导航"]')
  expect(drawer.attributes('aria-modal')).toBe('true')
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
  await flushPromises()
  expect(wrapper.find('[role="dialog"][aria-label="管理员导航"]').exists()).toBe(false)
})

test('worker navigation announces catalog failure, retries, and switches the task view without losing its URL', async () => {
  resetDeviceContext()
  let fail: (reason: Error) => void = () => {}
  vi.mocked(getDeviceCatalog).mockResolvedValue({ ...catalog, items: catalog.items.filter(line => line.type === 'cutting') })
    .mockImplementationOnce(() => new Promise((_, reject) => { fail = reject }))
  const unmounted: string[] = []
  const tasks = defineComponent({
    setup() {
      const line = getDeviceId()
      onUnmounted(() => unmounted.push(line))
      return () => h('h1', `设备任务 ${line}`)
    },
  })
  const workerRouter = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/worker', component: WorkerLayout, children: [{ path: 'tasks', name: 'worker-tasks', component: tasks }] },
  ] })
  await workerRouter.push('/worker/tasks?project=P1')
  const wrapper = mount(RouterView, { global: { plugins: [workerRouter, createPinia(), { install: setupSoybeanComponents }] } })
  await flushPromises()
  const device = wrapper.get('[aria-label="当前设备"]')
  expect(device.get('[role="status"]').text()).toContain('正在读取设备')
  fail(new Error('设备服务离线'))
  await flushPromises()
  expect(device.get('[role="alert"]').text()).toContain('设备读取失败')
  await device.get('button').trigger('click')
  await flushPromises()
  expect(wrapper.get('h1').text()).toBe('设备任务 cut-a')
  device.getComponent(AppSelect).vm.$emit('update:modelValue', 'cut-b')
  await flushPromises()
  expect(unmounted).toEqual(['cut-a'])
  expect(wrapper.get('h1').text()).toBe('设备任务 cut-b')
  expect(workerRouter.currentRoute.value.fullPath).toBe('/worker/tasks?project=P1')
})

test('editing a scheduled report keeps its fixed device when the active cutting line changes', async () => {
  setDeviceCatalog(catalog)
  selectDevice('cut-b')
  let workflow: ReturnType<typeof useAutomationSchedules> | undefined
  mount(defineComponent({
    setup() {
      workflow = useAutomationSchedules({ hasUnsavedChanges: ref(false), message: ref(''), error: ref(''),
        refresh: async () => {}, confirm: async () => true })
      return () => null
    },
  }))
  if (!workflow) throw new Error('Automation workflow did not mount')
  const current = { ...workflow.form, schedule_id: 'fixed', device_id: 'cut-b', recipients: ['report@example.test'] }
  workflow.editSchedule(current)
  selectDevice('cut-a')
  const requests: unknown[] = []
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    expect(String(input)).toBe('/api/v1/automation/schedules/fixed')
    expect(init?.method).toBe('PUT')
    requests.push(JSON.parse(String(init?.body)))
    return Response.json({ success: true, data: current })
  })
  expect(await workflow.submit()).toBe(true)
  expect(requests).toEqual([expect.objectContaining({ device_id: 'cut-b', recipients: ['report@example.test'] })])
  expect(getDeviceId()).toBe('cut-a')
})

test('report query changes preserve scroll and reuse the tab, then closing it returns to the previous page', async () => {
  const pinia = createPinia()
  const scrollPage = vi.fn()
  const scroll = vi.fn()
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: scroll })
  const previous = document.createElement('main')
  previous.scrollTo = scrollPage
  previous.innerHTML = '<div></div>'
  const shell = document.createElement('div')
  shell.className = 'soybean-layout'
  shell.append(previous)
  document.body.append(shell)
  const { default: ThemeTabs } = await import('../src/layouts/modules/ThemeTabs.vue')
  const wrapper = mount(ThemeTabs, { global: { plugins: [pinia, router, { install: setupSoybeanComponents }] } })
  try {
    await router.push('/admin/settings/database')
    await router.push('/admin/reports/leadership')
    await flushPromises()
    expect(scrollPage).toHaveBeenCalledWith({ top: 0 })
    scrollPage.mockClear()
    await router.push('/admin/reports/leadership?start_date=2026-10-01')
    await flushPromises()
    expect(scrollPage).not.toHaveBeenCalled()
    expect(wrapper.findAll('[role="tab"]').map(tab => tab.text())).toEqual(['首页', '数据库配置', '领导报表'])
    const active = wrapper.get('[role="tab"][aria-selected="true"]')
    expect(active.text()).toBe('领导报表')
    useThemeStore(pinia).tab.closeTabByMiddleClick = false
    await active.trigger('mousedown', { button: 1 })
    expect(router.currentRoute.value.query.start_date).toBe('2026-10-01')
    useThemeStore(pinia).tab.closeTabByMiddleClick = true
    await active.trigger('mousedown', { button: 1 })
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/admin/settings/database')
    expect(wrapper.get('[role="tab"][aria-selected="true"]').text()).toBe('数据库配置')
  } finally {
    shell.remove()
    Reflect.deleteProperty(HTMLElement.prototype, 'scrollIntoView')
  }
})

test('route guards use registered line identifiers and still enforce the user role', async () => {
  const controls = mountSelection()
  await router.push('/admin/welding/reports/daily')
  expect(router.currentRoute.value.name).toBe('cutting-home')
  expect(getDeviceId()).toBe('cut-a')
  await controls.switchDevice('weld-a')
  expect(router.currentRoute.value.name).toBe('welding-home')
  expect(getDeviceId()).toBe('weld-a')
  await router.push('/admin/welding/analysis?start=2026-09-01&end=2026-09-02')
  expect(router.currentRoute.value.name).toBe('welding-daily')
  expect(router.currentRoute.value.query).toMatchObject({ start: '2026-09-01', end: '2026-09-02' })
  await router.push('/admin/cutting/home')
  expect(router.currentRoute.value.name).toBe('welding-home')
  await controls.switchDevice('cut-b')
  expect(router.currentRoute.value.name).toBe('cutting-home')
  expect(getDeviceId()).toBe('cut-b')
  session.user = { user_id: 'worker', username: 'worker', display_name: '工人', role: 'worker', active: true }
  await router.push('/admin/welding/reports/daily')
  expect(router.currentRoute.value.path).toBe('/worker/tasks')
  expect(getDeviceId()).toBe('cut-b')
  await router.push('/admin/settings/configuration-diagnostics')
  expect(router.currentRoute.value.path).toBe('/worker/tasks')
})

test('a pending device navigation keeps its filters and cannot be overlapped or applied after failure', async () => {
  const controls = mountSelection()
  await router.push('/admin/reports/workshop?project=P1')
  const removeQuery = registerDeviceSwitchQuery('workshop-report', () => ({ start_date: '2026-10-12' }))
  let resolveGuard: ((allowed: boolean) => void) | undefined
  const removeGuard = router.beforeEach(() => new Promise(resolve => { resolveGuard = resolve }))
  try {
    await controls.switchDevice('cut-a')
    expect(resolveGuard).toBeUndefined()
    const pending = controls.switchDevice('cut-b')
    await flushPromises()
    await controls.switchDevice('cut-b')
    expect(controls.switching.value).toBe(true)
    expect(getDeviceId()).toBe('cut-a')
    if (!resolveGuard) throw new Error('Switch did not start navigation')
    resolveGuard(true)
    await pending
    expect(getDeviceId()).toBe('cut-b')
    expect(controls.switching.value).toBe(false)
    expect(router.currentRoute.value.query).toMatchObject({ project: 'P1', start_date: '2026-10-12' })
  } finally {
    removeGuard()
    removeQuery()
  }

  const stopErrors = router.onError(() => {})
  const removeFailingGuard = router.beforeEach(() => { throw new Error('导航失败') })
  try {
    await expect(controls.switchDevice('weld-a')).rejects.toThrow('导航失败')
    expect(getDeviceId()).toBe('cut-b')
    expect(controls.switching.value).toBe(false)
    expect(router.currentRoute.value.name).toBe('workshop-report')
  } finally {
    removeFailingGuard()
    stopErrors()
  }
})
