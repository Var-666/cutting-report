// @vitest-environment jsdom

import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { defineComponent } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { resetDeviceContext, setDeviceCatalog } from '../src/api/deviceContext'
import { useReportExport } from '../src/composables/useReportExport'
import { session } from '../src/state/session'
import AdminEquipmentView from '../src/views/AdminEquipmentView.vue'
import AdminLeadershipView from '../src/views/AdminLeadershipView.vue'
import AdminWorkshopView from '../src/views/AdminWorkshopView.vue'
import EnergyPeriodReportView from '../src/views/EnergyPeriodReportView.vue'
import LeadershipReportView from '../src/views/LeadershipReportView.vue'
import WorkshopView from '../src/views/WorkshopView.vue'

const form = { report_type: 'workshop' as const, report_date: '2026-11-20' }

enableAutoUnmount(afterEach)
beforeEach(() => setDeviceCatalog({ default_device_id: 'Cutter01', items: [{
  id: 'Cutter01', type: 'cutting', name: '切割线', type_name: '切割', source_key: 'production',
  enabled: true, capabilities: [], home_path: '/admin/cutting/home',
}] }))
afterEach(resetDeviceContext)

async function mountExport() {
  const destination = defineComponent({ render: () => null })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: destination }, { path: '/center', name: 'report-center', component: destination }],
  })
  await router.push('/')
  let state: ReturnType<typeof useReportExport> | undefined
  const wrapper = mount(defineComponent({
    setup() {
      state = useReportExport()
      return () => null
    },
  }), { global: { plugins: [router] } })
  if (!state) throw new Error('Report exporter did not mount')
  return { state, wrapper, router }
}

test('leaving during report creation prevents late navigation without showing an error', async () => {
  // Match the browser's shared Error prototype in Vitest's mixed Node/jsdom realms.
  vi.stubGlobal('DOMException', class extends Error {
    constructor(message: string, name: string) {
      super(message)
      this.name = name
    }
  })
  let finish: ((value: Response) => void) | undefined
  vi.spyOn(globalThis, 'fetch').mockImplementation(() => new Promise(resolve => { finish = resolve }))
  const { state, wrapper, router } = await mountExport()
  const pending = state.exportReport(form)
  expect(state.exporting.value).toBe(true)
  wrapper.unmount()
  if (!finish) throw new Error('Report creation did not start')
  finish(Response.json({ success: true, data: { report_id: 'late-report' } }))
  await pending
  expect(state.exporting.value).toBe(false)
  expect(state.error.value).toBe('')
  expect(router.currentRoute.value.path).toBe('/')
})

test('creation retry reuses the same idempotency key and retries only the existing PDF archive', async () => {
  const creates: Array<{ key: string | null; line: string | null }> = []
  const statuses: string[] = []
  const downloads: string[] = []
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = new URL(String(input), 'http://localhost')
    if (url.pathname === '/api/v1/reports') {
      const headers = new Headers(init?.headers)
      creates.push({ key: headers.get('Idempotency-Key'), line: headers.get('X-Production-Line') })
      if (creates.length === 1) throw new Error('提交响应丢失')
      return Response.json({ success: true, data: { report_id: 'existing' } })
    }
    if (url.pathname === '/api/v1/reports/existing/status') {
      statuses.push(url.pathname)
      return Response.json({ success: true, data: { status: 'completed' } })
    }
    if (url.pathname === '/api/v1/reports/existing/download') {
      downloads.push(url.pathname)
      if (downloads.length === 1) return Response.json({ detail: 'Chromium 超时' }, { status: 502 })
      return new Response('%PDF-1.7', { headers: { 'Content-Type': 'application/pdf' } })
    }
    throw new Error(`Unexpected request ${url.pathname}`)
  })
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const link = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  const createObjectURL = Object.getOwnPropertyDescriptor(URL, 'createObjectURL')
  Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: () => 'blob:report' })
  try {
    const { state } = await mountExport()
    await state.exportReport(form, 'pdf')
    expect(state.retryLabel.value).toBe('确认创建结果')
    await state.retryExport()
    expect(state.retryLabel.value).toBe('重试下载 PDF')
    expect(state.error.value).toContain('已归档的 HTML')
    await state.retryExport()
    expect(creates).toHaveLength(2)
    expect(creates[0]).toMatchObject({ key: expect.stringMatching(/^[a-f0-9]{32}$/), line: 'Cutter01' })
    expect(creates[1]).toEqual(creates[0])
    expect(statuses).toHaveLength(1)
    expect(downloads).toHaveLength(2)
    expect(link).toHaveBeenCalledOnce()
    expect(state.progress.value).toContain('已交给浏览器下载')
  } finally {
    vi.useRealTimers()
    if (createObjectURL) Object.defineProperty(URL, 'createObjectURL', createObjectURL)
    else Reflect.deleteProperty(URL, 'createObjectURL')
  }
})

test.each([
  ['leadership', AdminLeadershipView, LeadershipReportView, 'LeadershipReportView'],
  ['workshop', AdminWorkshopView, WorkshopView, 'WorkshopView'],
  ['equipment', AdminEquipmentView, EnergyPeriodReportView, 'EnergyPeriodReportView'],
] as const)('%s export shows a retry action and opens the successfully created report', async (reportType, shell, reportView, stub) => {
  const original = session.user
  session.user = { user_id: 'admin', username: 'admin', display_name: '管理员', role: 'admin', active: true }
  const requests: Array<{ key: string | null; body: unknown }> = []
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const path = new URL(String(input), 'http://localhost').pathname
    expect(path).toBe('/api/v1/reports')
    requests.push({ key: new Headers(init?.headers).get('Idempotency-Key'), body: JSON.parse(String(init?.body)) })
    if (requests.length === 1) throw new Error('提交响应丢失')
    return Response.json({ success: true, data: { report_id: 'recovered' } })
  })
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', component: shell },
    { path: '/center', name: 'report-center', component: { render: () => null } },
  ] })
  try {
    await router.push('/')
    const wrapper = mount(shell, { global: { plugins: [router], stubs: { [stub]: true } } })
    wrapper.getComponent(reportView).vm.$emit('export', { ...form, report_type: reportType }, 'html')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('导出未完成')
    const retry = wrapper.findAll('button').find(button => button.text() === '确认创建结果')
    expect(retry).toBeDefined()
    if (!retry) throw new Error('Export retry is missing')
    await retry.trigger('click')
    await flushPromises()
    expect(requests).toHaveLength(2)
    expect(requests[0].body).toMatchObject({ report_type: reportType })
    expect(requests[0].key).toMatch(/^[a-f0-9]{32}$/)
    expect(requests[1]).toEqual(requests[0])
    expect(router.currentRoute.value.query.job).toBe('recovered')
  } finally {
    session.user = original
  }
})
