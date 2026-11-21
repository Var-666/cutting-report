// @vitest-environment jsdom

import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { NDialogProvider } from 'naive-ui'
import { afterEach, expect, test, vi } from 'vitest'
import { KeepAlive, h } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { resetDeviceContext, setDeviceCatalog } from '../src/api/deviceContext'
import { reportCache } from '../src/api/reportCache'
import AnalysisPanel from '../src/components/AnalysisPanel.vue'
import AppDatePicker from '../src/components/AppDatePicker.vue'
import AppSelect from '../src/components/AppSelect.vue'
import StandardReportQuery from '../src/components/StandardReportQuery.vue'
import { setupSoybeanComponents } from '../src/vendor/soybean-admin/components'
import WorkshopView from '../src/views/WorkshopView.vue'
import { stubTableLayout } from './table-layout-fixture.mjs'

const calendar = vi.hoisted(() => ({ today: '2026-09-30' }))
vi.mock('../src/api/reportDates', async importOriginal => ({
  ...await importOriginal(), reportToday: () => calendar.today,
}))

enableAutoUnmount(afterEach)
afterEach(() => {
  vi.useRealTimers()
  calendar.today = '2026-09-30'
  Reflect.deleteProperty(Element.prototype, 'scrollTo')
  reportCache.clear()
  resetDeviceContext()
})

const stamp = '2026-09-30'
const response = data => Response.json({ success: true, data })
const report = { report_date: stamp, period_start: stamp, period_end: stamp, item_count: 1, confirmed_count: 0 }
const linked = { start: stamp, end: stamp, task_line: 'Cutter01', task: 'task-8', project: '已查询工程', confirmation: 'confirmed', page: '2' }
const taskResult = (program_no, page = 1) => ({
  report, items: [{ task_id: 'task-8', program_no, project_code: '已查询工程', production_date: stamp }],
  total: 12, page, page_size: 10, project_names: ['已查询工程', '未查询工程'], confirmed_count: 0,
  worker_summaries: [], analysis_source_version: 'workshop-version',
})

async function open(mode, fetchResponse, query = mode === 'worker' ? {} : linked) {
  stubTableLayout()
  Object.defineProperty(Element.prototype, 'scrollTo', { configurable: true, value: vi.fn() })
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
    { path: '/other', component: { render: () => h('p', '其他页面') } },
  ] })
  await router.push({ path: '/workshop', query })
  const wrapper = mount({ render: () => h(NDialogProvider, {}, { default: () => h(RouterView, {}, {
    default: ({ Component }) => h(KeepAlive, {}, { default: () => Component }),
  }) }) }, {
    global: { plugins: [router, { install: setupSoybeanComponents }], components: { AppSelect, AppDatePicker },
      stubs: { AnalysisPanel: true, WorkshopStructureCharts: true } },
  })
  await flushPromises()
  return { wrapper, calls, router }
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

test('manager snapshot polling cancels outstanding reads on navigation', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  let finish
  let first = true
  const { wrapper, calls, router } = await open('manager', () => {
    if (first) {
      first = false
      return response({ ...taskResult('旧统计', 2), meta: { report_data: { refreshing: true } } })
    }
    return new Promise(resolve => { finish = resolve })
  })
  expect(wrapper.text()).toContain('旧统计')
  await vi.advanceTimersByTimeAsync(5000)
  expect(calls).toHaveLength(2)
  await router.push('/other')
  expect(calls[1].signal.aborted).toBe(true)
  finish(response(taskResult('迟到统计', 2)))
  await vi.advanceTimersByTimeAsync(5000)
  expect(calls).toHaveLength(2)
  expect(wrapper.text()).toContain('其他页面')
  expect(wrapper.text()).not.toContain('迟到统计')
})

test('manager pagination, export and analysis keep the last successful date scope through draft edits and failure', async () => {
  let finish
  let first = true
  const { wrapper, calls } = await open('manager', () => {
    if (first) { first = false; return response(taskResult('保留的记录', 2)) }
    return new Promise(resolve => { finish = resolve })
  })
  const controls = wrapper.getComponent(StandardReportQuery)
  controls.vm.$emit('update:startDate', '2026-09-22')
  controls.vm.$emit('update:endDate', '2026-09-25')
  await flushPromises()
  expect(controls.props('dateDirty')).toBe(true)
  controls.vm.$emit('export', 'pdf')
  const exportRequest = wrapper.getComponent(WorkshopView).emitted('export')[0]
  expect(exportRequest).toEqual([expect.objectContaining({
    period_start: stamp, period_end: stamp, parameters: expect.objectContaining({ task_id: 'task-8' }),
  }), 'pdf'])
  controls.vm.$emit('submit')
  await flushPromises()
  expect(Object.fromEntries(calls.at(-1).query)).toMatchObject({ start_date: '2026-09-22', end_date: '2026-09-25' })
  finish(Response.json({ detail: '数据源暂不可用' }, { status: 500 }))
  await flushPromises()
  expect(wrapper.text()).toContain('数据源暂不可用')
  expect(wrapper.text()).toContain('保留的记录')

  await wrapper.get('button[aria-label="第 1 页"]').trigger('click')
  await flushPromises()
  expect(Object.fromEntries(calls.at(-1).query)).toMatchObject({ start_date: stamp, end_date: stamp, page: '1', task_id: 'task-8' })
  finish(response(taskResult('上一页的记录', 1)))
  await flushPromises()
  expect(wrapper.text()).toContain('上一页的记录')
  wrapper.getComponent(AnalysisPanel).vm.$emit('refresh-report')
  await flushPromises()
  expect(Object.fromEntries(calls.at(-1).query)).toMatchObject({ start_date: stamp, end_date: stamp, task_id: 'task-8', refresh: 'true' })
  finish(response(taskResult('重新读取的记录', 1)))
  await flushPromises()
  expect(wrapper.text()).toContain('重新读取的记录')
})

test('a chart choice preserves a pending task link and a failed result leaves the previous scope on export', async () => {
  let first = true
  const pending = []
  const { wrapper, router } = await open('manager', call => {
    if (first) { first = false; return response(taskResult('当前范围', 2)) }
    return new Promise(resolve => pending.push({ ...call, resolve }))
  })
  const next = { ...linked, start: '2026-08-30', end: '2026-08-31', project: '其他工程', task: 'T-8', page: '1' }
  await router.push({ query: next })
  await flushPromises()
  const controls = wrapper.getComponent(StandardReportQuery)
  controls.vm.$emit('update:chartType', 'line')
  await flushPromises()
  expect(router.currentRoute.value.query).toEqual({ ...next, chart: 'line' })
  expect(pending).toHaveLength(1)
  expect(pending[0].signal.aborted).toBe(false)
  pending[0].resolve(Response.json({ detail: '数据源暂不可用' }, { status: 500 }))
  await flushPromises()
  controls.vm.$emit('export', 'html')
  const exportRequest = wrapper.getComponent(WorkshopView).emitted('export')[0]
  expect(exportRequest).toEqual([expect.objectContaining({
    period_start: stamp, period_end: stamp, project_code: '已查询工程',
    parameters: expect.objectContaining({ task_id: 'task-8', chart_type: 'line' }),
  }), 'html'])
  await router.push({ query: { ...next, project: '新项目' } })
  await flushPromises()
  await router.push({ query: linked })
  await flushPromises()
  expect(pending[1].signal.aborted).toBe(true)
  pending[1].resolve(response(taskResult('其他项目迟到的记录', 1)))
  await flushPromises()
  expect(wrapper.text()).not.toContain('其他项目迟到的记录')
  expect(wrapper.text()).toContain('当前范围')
})

test('the default manager report follows the current day and clears old tasks immediately after midnight', async () => {
  let first = true
  let finish
  const { wrapper, calls, router } = await open('manager', call => {
    expect(call.path).toBe('/api/v1/workshop/range')
    if (first) { first = false; return response(taskResult('昨天的任务')) }
    return new Promise(resolve => { finish = resolve })
  }, {})
  expect(calls.map(call => call.query.get('start_date'))).toEqual([stamp])
  expect(wrapper.text()).toContain('昨天的任务')
  await router.push('/other')
  calendar.today = '2026-10-01'
  await router.push('/workshop')
  await flushPromises()
  expect(wrapper.text()).not.toContain('昨天的任务')
  expect(calls.at(-1).query.get('start_date')).toBe('2026-10-01')
  finish(response({ ...taskResult(''), items: [], total: 0,
    report: { ...report, period_start: '2026-10-01', period_end: '2026-10-01', item_count: 0 } }))
  await flushPromises()
  expect(wrapper.text()).toContain('当前条件下暂无任务')
})
