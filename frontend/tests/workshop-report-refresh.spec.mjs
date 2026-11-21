// @vitest-environment jsdom

import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { NDialogProvider } from 'naive-ui'
import { afterEach, expect, test, vi } from 'vitest'
import { h } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { resetDeviceContext, setDeviceCatalog } from '../src/api/deviceContext'
import { reportCache } from '../src/api/reportCache'
import AnalysisPanel from '../src/components/AnalysisPanel.vue'
import AppDatePicker from '../src/components/AppDatePicker.vue'
import AppSelect from '../src/components/AppSelect.vue'
import { setupSoybeanComponents } from '../src/vendor/soybean-admin/components'
import WorkshopView from '../src/views/WorkshopView.vue'
import { stubTableLayout } from './table-layout-fixture.mjs'

enableAutoUnmount(afterEach)
afterEach(() => {
  vi.useRealTimers()
  reportCache.clear()
  resetDeviceContext()
})

const stamp = '2026-09-30'
const response = data => Response.json({ success: true, data })
const report = { report_date: stamp, period_start: stamp, period_end: stamp, item_count: 1, confirmed_count: 0 }
const taskResult = (program_no, page = 1) => ({
  report, items: [{ task_id: 'task-8', program_no, project_code: '已查询工程', production_date: stamp }],
  total: 12, page, page_size: 10, project_names: ['已查询工程', '未查询工程'], confirmed_count: 0,
  worker_summaries: [], analysis_source_version: 'workshop-version',
})

async function open(mode, fetchResponse) {
  stubTableLayout()
  setDeviceCatalog({ default_device_id: 'Cutter01', items: [{ id: 'Cutter01' }] })
  const calls = []
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = new URL(String(input), 'http://localhost')
    if (url.pathname.endsWith('/cancel') || url.pathname === '/api/v1/operations/browser') return Response.json({})
    const call = { path: url.pathname, query: url.searchParams, method: init?.method ?? 'GET', signal: init?.signal,
      body: init?.body && JSON.parse(init.body) }
    calls.push(call)
    return fetchResponse(call)
  })
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/workshop', name: 'workshop-report', component: WorkshopView,
      props: { mode, user: { user_id: 'workshop-user', display_name: '当前工人', role: mode === 'worker' ? 'worker' : 'admin' } } },
  ] })
  await router.push(mode === 'worker' ? '/workshop'
    : `/workshop?start=${stamp}&end=${stamp}&task_line=Cutter01&task=task-8&project=已查询工程&confirmation=confirmed&page=2`)
  const wrapper = mount({ render: () => h(NDialogProvider, {}, { default: () => h(RouterView) }) }, {
    global: { plugins: [router, { install: setupSoybeanComponents }], components: { AppSelect, AppDatePicker },
      stubs: { AnalysisPanel: true, WorkshopStructureCharts: true } },
  })
  await flushPromises()
  return { wrapper, calls }
}

test('worker synchronizes the selected production day once and automatic polling only reads tasks', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
  let reads = 0
  const { wrapper, calls } = await open('worker', ({ path }) => {
    if (path === '/api/v1/workshop/latest-date') return response({ report_date: stamp })
    if (path === '/api/v1/workshop/reports') return response(report)
    if (path === '/api/v1/workshop/tasks') return response(taskResult(['同步前', '同步后', '后台刷新'][reads++]))
    throw new Error(`Unexpected request ${path}`)
  })
  expect(calls.map(call => [call.method, call.path])).toEqual([
    ['GET', '/api/v1/workshop/latest-date'], ['GET', '/api/v1/workshop/tasks'],
    ['POST', '/api/v1/workshop/reports'], ['GET', '/api/v1/workshop/tasks'],
  ])
  expect(calls[2].body).toEqual({ report_date: stamp })
  expect(calls[2].signal).toBeInstanceOf(AbortSignal)
  expect(wrapper.text()).toContain('同步后')
  await vi.advanceTimersByTimeAsync(5000)
  expect(calls.slice(4).map(call => [call.method, call.path])).toEqual([['GET', '/api/v1/workshop/tasks']])
  expect(calls[4].query.get('report_date')).toBe(stamp)
  expect(wrapper.text()).toContain('后台刷新')
})

test('manager source refresh and retry use the applied filters and retain rows while pending or failed', async () => {
  let finish
  let first = true
  const { wrapper, calls } = await open('manager', ({ path }) => {
    expect(path).toBe('/api/v1/workshop/range')
    if (first) { first = false; return response(taskResult('首次结果', 2)) }
    return new Promise(resolve => { finish = resolve })
  })
  const project = wrapper.findAllComponents(AppSelect).find(component => component.element.closest('label')?.textContent.startsWith('工程号'))
  project.vm.$emit('update:modelValue', '未查询工程')
  await flushPromises()
  wrapper.getComponent(AnalysisPanel).vm.$emit('refresh-report')
  await flushPromises()
  expect(wrapper.text()).toContain('首次结果')
  expect(calls.at(-1).query.get('refresh')).toBe('true')
  expect(Object.fromEntries(calls.at(-1).query)).toMatchObject({
    task_id: 'task-8', project_name: '已查询工程', confirmation_status: 'confirmed', page: '2',
  })
  finish(response(taskResult('刷新结果', 2)))
  await flushPromises()
  expect(wrapper.text()).toContain('刷新结果')
  expect(project.props('modelValue')).toBe('未查询工程')

  wrapper.getComponent(AnalysisPanel).vm.$emit('refresh-report')
  await flushPromises()
  finish(Response.json({ detail: '后台更新失败' }, { status: 500 }))
  await flushPromises()
  expect(wrapper.text()).toContain('后台更新失败')
  expect(wrapper.text()).toContain('刷新结果')
  await wrapper.findAll('button').find(button => button.text() === '重新查询').trigger('click')
  await flushPromises()
  expect(Object.fromEntries(calls.at(-1).query)).toMatchObject({
    task_id: 'task-8', project_name: '已查询工程', confirmation_status: 'confirmed', page: '2',
  })
  finish(response(taskResult('重试结果', 2)))
  await flushPromises()
  expect(wrapper.text()).toContain('重试结果')
})
