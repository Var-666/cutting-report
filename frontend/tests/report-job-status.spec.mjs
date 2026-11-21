// @vitest-environment jsdom

import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { NButton, NDatePicker } from 'naive-ui'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import { resetDeviceContext, setDeviceCatalog } from '../src/api/deviceContext'
import { reportCache } from '../src/api/reportCache'
import AppDatePicker from '../src/components/AppDatePicker.vue'
import AppSelect from '../src/components/AppSelect.vue'
import ReportForm from '../src/components/ReportForm.vue'
import ReportJobStatus from '../src/components/ReportJobStatus.vue'
import ReportCenterView from '../src/views/ReportCenterView.vue'
import { stubTableLayout } from './table-layout-fixture.mjs'

enableAutoUnmount(afterEach)
beforeEach(() => {
  setDeviceCatalog({ default_device_id: 'Cutter02', items: [{ id: 'Cutter02' }] })
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-09-01T04:00:00Z'))
})
afterEach(() => {
  vi.useRealTimers()
  reportCache.clear()
  resetDeviceContext()
})

const readOptions = items => Response.json({ success: true, data: { items } })
const components = { AppSelect, AppDatePicker, NButton }

function select(wrapper, label) {
  const control = wrapper.findAllComponents(AppSelect).find(item => item.element.closest('label')?.textContent.startsWith(label))
  if (!control) throw new Error(`Missing report field ${label}`)
  return control
}

async function choose(wrapper, label, value) {
  select(wrapper, label).vm.$emit('update:modelValue', value)
  await wrapper.vm.$nextTick()
}

async function chooseDate(wrapper, value) {
  wrapper.getComponent(NDatePicker).vm.$emit('update:formatted-value', value)
  await wrapper.vm.$nextTick()
}

async function center({ query = {}, readHistory, readStatus } = {}) {
  const pending = []
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = new URL(String(input), 'http://localhost')
    if (url.pathname.endsWith('/cancel')) return Response.json({})
    if (url.pathname === '/api/v1/reports/batch-42/status' && readStatus) return readStatus(init.signal)
    if (url.pathname === '/api/v1/reports' && readHistory) return readHistory(url.searchParams)
    if (url.pathname === '/api/v1/reports' || url.pathname === '/api/v1/designer/published-definitions') {
      return Response.json({ success: true, data: { items: [], total: 0, page: 1, page_size: 10 } })
    }
    const type = url.pathname.split('/').at(-1)
    expect(url.pathname).toMatch(/^\/api\/v1\/options\/(projects|ships)$/)
    expect(url.searchParams.get('device_id')).toBe('Cutter02')
    return new Promise(resolve => pending.push({
      type, date: url.searchParams.get('report_date'), project: url.searchParams.get('project_code'), signal: init.signal,
      finish: items => resolve(readOptions(items)), fail: detail => resolve(Response.json({ detail }, { status: 502 })),
    }))
  })
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/reports', name: 'report-center', component: ReportCenterView },
  ] })
  await router.push({ path: '/reports', query })
  const wrapper = mount(ReportCenterView, { props: { fixedReportType: 'workshop' }, global: { plugins: [router], components } })
  await flushPromises()
  return { wrapper, pending, router, form: wrapper.getComponent(ReportForm) }
}

test('report status provides a user-facing label when AI is unavailable', () => {
  const wrapper = mount(ReportJobStatus, { props: { job: {
    report_id: 'finished', report_type: 'leadership', status: 'completed', task_count: 8, ai_status: 'unavailable',
  } }, global: { components } })
  expect(wrapper.text()).toContain('8 个任务 · AI 暂不可用')
})

test('switching the report date cancels both old reads and a slow response cannot restore old options', async () => {
  const { form, pending } = await center()
  await chooseDate(form, '2026-09-02')
  expect(pending.map(call => [call.type, call.date])).toEqual([
    ['ships', '2026-09-01'], ['projects', '2026-09-01'], ['ships', '2026-09-02'], ['projects', '2026-09-02'],
  ])
  expect(pending.slice(0, 2).every(call => call.signal.aborted)).toBe(true)
  pending[2].finish(['新船名'])
  pending[3].finish(['新工程'])
  pending[0].finish(['旧船名'])
  pending[1].finish(['旧工程'])
  await flushPromises()
  expect(select(form, '工程号').text()).toContain('新工程')
  expect(select(form, '船名').text()).toContain('新船名')
  expect(form.text()).not.toMatch(/旧船名|旧工程/)
  expect(form.props('projectsLoading')).toBe(false)
  expect(form.props('shipsLoading')).toBe(false)
})

test('latest project-scoped ship options win over both all-project and old-project requests', async () => {
  const { form, wrapper, pending } = await center()
  pending[1].finish(['A', 'B'])
  await flushPromises()
  await choose(form, '工程号', 'A')
  await choose(form, '工程号', 'B')
  expect(pending.filter(call => call.type === 'ships').map(call => call.project)).toEqual([null, 'A', 'B'])
  expect(pending[0].signal.aborted).toBe(true)
  expect(pending[2].signal.aborted).toBe(true)
  pending[3].finish(['B船'])
  pending[2].fail('旧工程错误')
  pending[0].finish(['所有工程船名'])
  await flushPromises()
  expect(select(form, '船名').text()).toContain('B船')
  expect(wrapper.text()).not.toMatch(/旧工程错误|所有工程船名/)
})

test('new option failures clear stale choices and a later successful date recovers', async () => {
  const { form, wrapper, pending } = await center()
  pending[0].finish(['旧船名'])
  pending[1].finish(['旧工程'])
  await flushPromises()
  await chooseDate(form, '2026-09-02')
  expect(form.text()).not.toMatch(/旧船名|旧工程/)
  pending[2].fail('船名暂不可用')
  pending[3].fail('工程暂不可用')
  await flushPromises()
  expect(wrapper.get('[role="alert"]').text()).toContain('暂不可用')
  expect(form.text()).not.toMatch(/旧船名|旧工程/)
  await chooseDate(form, '2026-09-03')
  pending[4].finish(['恢复的船名'])
  pending[5].finish(['恢复的工程'])
  await flushPromises()
  expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  expect(form.text()).toContain('恢复的船名')
  expect(form.text()).toContain('恢复的工程')
})

test('leaving the report center aborts both option requests', async () => {
  const { wrapper, pending } = await center()
  expect(pending).toHaveLength(2)
  wrapper.unmount()
  expect(pending.every(call => call.signal.aborted)).toBe(true)
  for (const request of pending) request.finish(['迟到的结果'])
  await flushPromises()
})

test('the form clears dependent filters once on date change and submits project number and ship name separately', async () => {
  const form = mount(ReportForm, { props: { initialReportType: 'workshop', projects: ['CMYZ0170'], ships: ['18600DWT-02'] }, global: { components } })
  await choose(form, '工程号', 'CMYZ0170')
  await choose(form, '船名', '18600DWT-02')
  await chooseDate(form, '2026-09-02')
  expect(select(form, '工程号').props('modelValue')).toBe('')
  expect(select(form, '船名').props('modelValue')).toBe('')
  expect(form.emitted('date-change')).toEqual([['2026-09-01'], ['2026-09-02']])
  expect(form.emitted('project-change')).toEqual([[{ reportDate: '2026-09-01', projectCode: 'CMYZ0170' }]])
  await choose(form, '工程号', 'CMYZ0170')
  await choose(form, '船名', '18600DWT-02')
  await form.trigger('submit')
  expect(form.emitted('submit')[0][0]).toEqual({
    report_type: 'workshop', definition_id: null, report_date: '2026-09-02',
    project_code: 'CMYZ0170', ship_no: '18600DWT-02', parameters: { chart_type: 'bar_line' },
  })
  await form.setProps({ initialReportType: 'leadership' })
  await chooseDate(form, '2026-08')
  expect(form.emitted('date-change').at(-1)).toEqual(['2026-08-01'])
  await form.trigger('submit')
  expect(form.emitted('submit').at(-1)[0]).toMatchObject({ report_type: 'leadership', report_date: '2026-08-01', project_code: null, ship_no: null })
})

test('report history restores the linked page, keeps its fixed page size and preserves unrelated URL filters', async () => {
  stubTableLayout()
  const requests = []
  const { wrapper, router } = await center({
    query: { page: '2', page_size: '99', source: 'notice' },
    readHistory(params) {
      const page = Number(params.get('page'))
      requests.push([page, Number(params.get('page_size'))])
      return Response.json({ success: true, data: {
        page, page_size: 10, total: 30,
        items: [{ report_id: `report-${page}`, report_title: `列表第 ${page} 页`, report_type: 'workshop', status: 'failed' }],
      } })
    },
  })
  expect(requests).toEqual([[2, 10]])
  expect(wrapper.text()).toContain('列表第 2 页')
  await wrapper.get('button[aria-label="第 3 页"]').trigger('click')
  await flushPromises()
  expect(requests).toEqual([[2, 10], [3, 10]])
  expect(wrapper.text()).toContain('列表第 3 页')
  expect(router.currentRoute.value.query).toEqual({ page: '3', source: 'notice' })
  router.back()
  await flushPromises()
  expect(wrapper.text()).toContain('列表第 2 页')
})

test('leaving an active report job aborts its status poll and ignores the late result', async () => {
  vi.useRealTimers()
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'], now: new Date('2026-09-01T04:00:00Z') })
  const reads = []
  let finish
  const { wrapper } = await center({
    query: { job: 'batch-42' },
    readStatus(signal) {
      reads.push(signal)
      if (reads.length === 1) return Response.json({ success: true, data: {
        report_id: 'batch-42', report_type: 'workshop', status: 'querying', stage_message: '正在查询数据',
      } })
      return new Promise(resolve => { finish = resolve })
    },
  })
  expect(wrapper.text()).toContain('正在查询数据')
  await vi.advanceTimersByTimeAsync(1000)
  expect(reads).toHaveLength(2)
  wrapper.unmount()
  expect(reads[1].aborted).toBe(true)
  finish(Response.json({ success: true, data: { report_id: 'batch-42', status: 'completed' } }))
  await vi.advanceTimersByTimeAsync(5000)
  expect(reads).toHaveLength(2)
})
