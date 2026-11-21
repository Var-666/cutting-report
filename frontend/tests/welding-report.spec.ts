// @vitest-environment jsdom

import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { NButton, NCheckbox, NInput, NInputNumber } from 'naive-ui'
import { createPinia } from 'pinia'
import { afterEach, expect, test, vi } from 'vitest'
import { nextTick } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { resetDeviceContext, selectDevice, setDeviceCatalog } from '../src/api/deviceContext'
import { reportCache } from '../src/api/reportCache'
import AppDatePicker from '../src/components/AppDatePicker.vue'
import AppSelect from '../src/components/AppSelect.vue'
import WeldingReportView from '../src/views/WeldingReportView.vue'
import WeldingHomeView from '../src/views/WeldingHomeView.vue'
import { useAppStore } from '../src/state/app'
import { stubTableLayout } from './table-layout-fixture.mjs'

enableAutoUnmount(afterEach)
afterEach(() => {
  vi.useRealTimers()
  reportCache.clear()
  resetDeviceContext()
})

const weldLine = {
  id: 'weld-a', name: '焊接A', type: 'welding' as const, enabled: true,
  type_name: '焊接', source_key: 'weld-db', capabilities: [], home_path: '/home',
}

function report(query: URLSearchParams, label: string) {
  return Response.json({ success: true, data: {
    report_type: 'jobs',
    query: { ...Object.fromEntries(query), page: Number(query.get('page') ?? 1), page_size: 10, descending: query.get('descending') !== 'false' },
    total: 35,
    items: [{ id: 1, serial_number: label }],
    summary: { job_count: 1 },
    data_version: label,
    meta: { quality: {}, notices: [], pending_metrics: {} },
  } })
}

async function setup(initial?: (query: URLSearchParams) => Response | Promise<Response>) {
  setDeviceCatalog({ default_device_id: 'weld-a', items: [weldLine] })
  stubTableLayout()
  const calls: Array<{ query: URLSearchParams; scope: string | null; signal: AbortSignal | null | undefined }> = []
  let respond = initial ?? (query => report(query, `初始作业-${query.get('page') ?? 1}`))
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = new URL(String(input), 'http://localhost')
    if (url.pathname.includes('/cancel')) return Response.json({})
    if (url.pathname.endsWith('/analysis/latest')) return Response.json({ success: true, data: null })
    if (url.pathname !== '/api/v1/welding/jobs') throw new Error(`Unexpected request ${url.pathname}`)
    calls.push({ query: url.searchParams, scope: new Headers(init?.headers).get('X-Production-Line'), signal: init?.signal })
    return respond(url.searchParams)
  })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/jobs', name: 'welding-jobs', component: WeldingReportView, props: { kind: 'jobs' } }],
  })
  await router.push('/jobs?start=2026-09-01&end=2026-09-02&sort_by=length&descending=false')
  const wrapper = mount(WeldingReportView, {
    props: { kind: 'jobs' },
    attachTo: document.body,
    global: {
      plugins: [createPinia(), router],
      components: { AppSelect, AppDatePicker, NButton, NCheckbox, NInput, NInputNumber },
      stubs: { teleport: true, WeldingCharts: true },
    },
  })
  await flushPromises()
  return { calls, router, wrapper, replyWith: (callback: typeof respond) => { respond = callback } }
}

test('copied dates and sorting determine results and pagination uses the applied query', async () => {
  const { wrapper, calls, router, replyWith } = await setup()
  expect(calls).toHaveLength(1)
  expect(calls[0].query.get('start')).toBe('2026-09-01')
  expect(calls[0].query.get('end')).toBe('2026-09-02')
  expect(calls[0].query.get('sort_by')).toBe('length')
  expect(calls[0].query.get('descending')).toBe('false')
  expect(calls[0].scope).toBe('weld-a')
  expect(wrapper.text()).toContain('初始作业-1')
  await wrapper.get('button[aria-haspopup="dialog"]').trigger('click')
  await nextTick()
  const form = wrapper.get('form[aria-label="焊接报表查询"]')
  const sort = wrapper.findAllComponents(AppSelect).find(component => component.find('[aria-label="排序方式"]').exists())
  if (!sort) throw new Error('Sort control is missing')
  sort.vm.$emit('update:modelValue', 'id')
  await form.get('input[placeholder="完整流水号精确匹配"]').setValue('SERIAL-2')
  expect(calls).toHaveLength(1)
  await form.trigger('submit')
  await flushPromises()
  expect(calls[1].query.get('serial_number')).toBe('SERIAL-2')
  expect(calls[1].query.get('sort_by')).toBe('id')
  expect(router.currentRoute.value.query.serial_number).toBe('SERIAL-2')
  await wrapper.get('button[aria-label="第 3 页"]').trigger('click')
  await flushPromises()
  expect(calls[2].query.get('serial_number')).toBe('SERIAL-2')
  expect(calls[2].query.get('page')).toBe('3')
  expect(calls[2].query.get('page_size')).toBe('10')
  expect(wrapper.get('button[aria-current="page"]').attributes('aria-label')).toBe('第 3 页')
  expect(wrapper.text()).toContain('初始作业-3')
  replyWith(() => Response.json({ detail: '焊接查询失败' }, { status: 502 }))
  await wrapper.get('button[aria-label="第 2 页"]').trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('焊接查询失败')
  expect(wrapper.text()).toContain('初始作业-3')
  expect(router.currentRoute.value.query.page).toBe('3')
})

test('a cancelled older result cannot replace the range selected by the latest route', async () => {
  const { wrapper, calls, router, replyWith } = await setup()
  let resolveOld: ((value: Response) => void) | undefined
  replyWith(query => query.get('start') === '2026-09-03'
    ? new Promise(resolve => { resolveOld = resolve })
    : report(query, '最新作业'))
  await router.push('/jobs?start=2026-09-03&end=2026-09-04')
  await flushPromises()
  const old = calls.at(-1)
  expect(old?.query.get('start')).toBe('2026-09-03')
  expect(wrapper.find('form[aria-label="焊接报表查询"]').exists()).toBe(false)
  await router.push('/jobs?start=2026-09-05&end=2026-09-06')
  await flushPromises()
  expect(old?.signal?.aborted).toBe(true)
  expect(wrapper.text()).toContain('最新作业')
  if (!resolveOld || !old) throw new Error('Old request was not held')
  resolveOld(report(old.query, '过期作业'))
  await flushPromises()
  expect(wrapper.text()).not.toContain('过期作业')
  expect(wrapper.text()).toContain('最新作业')
  expect(router.currentRoute.value.query.start).toBe('2026-09-05')
})

test('an unprepared snapshot shows loading and the scheduled read displays the result once', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  let reads = 0
  const { wrapper, calls } = await setup(query => ++reads === 1
    ? Response.json({ detail: '统计正在生成' }, { status: 503, headers: { 'X-Welding-Error': 'snapshot_refresh_busy' } })
    : report(query, '已准备好'))
  expect(calls).toHaveLength(1)
  expect(wrapper.text()).toContain('正在读取焊接数据')
  await vi.advanceTimersByTimeAsync(5000)
  await flushPromises()
  expect(calls).toHaveLength(2)
  expect(calls[1].query.has('refresh')).toBe(false)
  expect(wrapper.text()).toContain('已准备好')
  expect(wrapper.text()).not.toContain('正在读取焊接数据')
})

test('leaving a queued source read aborts the network without replay', async () => {
  let finish: ((reply: Response) => void) | undefined
  const { wrapper, calls } = await setup(() => new Promise(resolve => { finish = resolve }))
  expect(wrapper.text()).toContain('正在读取焊接数据')
  expect(calls).toHaveLength(1)
  wrapper.unmount()
  expect(calls[0].signal?.aborted).toBe(true)
  finish?.(report(calls[0].query, '迟到结果'))
  await flushPromises()
  expect(calls).toHaveLength(1)
})

async function openWeldingOverview(path = '/home') {
  stubTableLayout()
  const destination = { render: () => null }
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/home', component: WeldingHomeView },
      ...['daily', 'jobs', 'parts', 'equipment'].map(kind => ({ path: `/reports/${kind}`, name: `welding-${kind}`, component: destination })),
    ],
  })
  await router.push(path)
  const pinia = createPinia()
  useAppStore(pinia).reducedMotion = true
  const wrapper = mount(WeldingHomeView, { global: { plugins: [pinia, router], stubs: { WeldingChart: true } } })
  await flushPromises()
  return { wrapper, router }
}

test('welding overview offers keyboard-accessible tables with the chart data', async () => {
  setDeviceCatalog({ default_device_id: 'weld-a', items: [weldLine] })
  vi.spyOn(globalThis, 'fetch').mockImplementation(async input => {
    const url = new URL(String(input), 'http://localhost')
    if (url.pathname.includes('/cancel')) return Response.json({})
    if (url.pathname !== '/api/v1/welding/dashboard') throw new Error(`Unexpected request ${url.pathname}`)
    return Response.json({ success: true, data: {
      query: Object.fromEntries(url.searchParams),
      summary: { flat_length_m: 12.5 }, total: 0, items: [],
      daily: [{ production_date: '2026-09-01', weld_length_m: 12.5, job_count: 4 }],
      device_distribution: [{ device_no: 0, weld_length_m: 12.5 }],
      meta: { quality: {}, notices: [], pending_metrics: {} },
    } })
  })
  const { wrapper } = await openWeldingOverview('/home?start=2026-09-01&end=2026-09-02')
  const regions = [
    ['查看生产趋势数据', '生产趋势数据', '每日焊接长度与作业次数'],
    ['查看工艺结构数据', '工艺结构数据', '各工艺焊接长度'],
    ['查看设备产出数据', '设备产出数据', '各设备累计焊接长度'],
  ]
  for (const [summary, name, caption] of regions) {
    const table = wrapper.findAll('details').find(details => details.get('summary').text() === summary)
    if (!table) throw new Error(`Data disclosure is missing for ${name}`)
    const region = table.get(`[role="region"][aria-label="${name}"]`)
    expect(region.attributes('tabindex')).toBe('0')
    expect(region.get('caption').text()).toContain(caption)
    expect(region.get('th[scope="row"]').exists()).toBe(true)
    expect(region.get('tbody').text()).toContain('12.500')
  }
  expect(wrapper.get('[aria-label="生产趋势数据"] tbody').text()).toContain('4')
  expect(wrapper.get('[aria-label="设备产出数据"] tbody').text()).toContain('设备 0')
})

test('the welding overview waits for its catalog and retains a successful result through refresh failure', async () => {
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] })
  vi.setSystemTime(new Date('2026-09-06T01:00:00Z'))
  const otherLine = { ...weldLine, id: 'weld-b', name: '焊接B', source_key: 'other-weld-db' }
  const calls: Array<{ line: string | null; query: URLSearchParams; signal: AbortSignal | null | undefined }> = []
  let releaseLate: ((value: Response) => void) | undefined
  const dashboard = (query: URLSearchParams, jobs: number) => Response.json({ success: true, data: {
    query: Object.fromEntries(query), today: { job_count: jobs }, summary: {}, total: 0, items: [],
    meta: { quality: {}, notices: [], pending_metrics: {} },
  } })
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = new URL(String(input), 'http://localhost')
    if (url.pathname.includes('/cancel')) return Response.json({})
    if (url.pathname !== '/api/v1/welding/dashboard') throw new Error(`Unexpected request ${url.pathname}`)
    const line = new Headers(init?.headers).get('X-Production-Line')
    calls.push({ line, query: url.searchParams, signal: init?.signal })
    if (calls.length === 1) return Response.json({ detail: '正在准备焊接统计' }, {
      status: 503, headers: { 'X-Welding-Error': 'snapshot_refresh_busy', 'Retry-After': '1' },
    })
    if (calls.length === 3) return Response.json({ detail: '数据源不可用' }, { status: 502 })
    if (calls.length === 4) return new Promise(resolve => { releaseLate = resolve })
    return dashboard(url.searchParams, line === 'weld-a' ? 0 : 3)
  })
  const { wrapper, router } = await openWeldingOverview()
  const refresh = () => wrapper.findAll('button').find(button => button.text() === '刷新概览')!
  const count = () => wrapper.get('[aria-label^="今日焊接作业次数："]').attributes('aria-label')
  expect(wrapper.get('time').text()).toBe('2026年09月06日')
  expect(calls).toHaveLength(0)
  setDeviceCatalog({ default_device_id: 'weld-a', items: [weldLine, otherLine] })
  await flushPromises()
  expect(calls).toHaveLength(1)
  expect(calls[0].query.get('start')).toBe('2026-08-31')
  expect(calls[0].query.get('page_size')).toBe('10')
  expect(wrapper.text()).toContain('焊接生产数据准备中')
  await vi.advanceTimersByTimeAsync(5000)
  await flushPromises()
  expect(count()).toBe('今日焊接作业次数：0 次')
  await router.push('/home?unrelated=keep')
  await flushPromises()
  expect(calls).toHaveLength(2)
  await refresh().trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('数据源不可用')
  expect(count()).toBe('今日焊接作业次数：0 次')
  await refresh().trigger('click')
  await flushPromises()
  await refresh().trigger('click')
  expect(calls).toHaveLength(4)

  await router.push('/home?start=2026-08-01&end=2026-08-03&device_no=0&unrelated=keep')
  selectDevice('weld-b')
  await flushPromises()
  expect(calls[3].signal?.aborted).toBe(true)
  expect(calls.at(-1)?.line).toBe('weld-b')
  expect(count()).toBe('今日焊接作业次数：3 次')
  expect(wrapper.text()).toContain('区间生产趋势')
  const link = wrapper.get('nav[aria-label="焊接报表快捷入口"] a')
  const query = new URL(link.attributes('href')!, 'http://localhost').searchParams
  expect(Object.fromEntries(query)).toMatchObject({ start: '2026-08-01', end: '2026-08-03', device_no: '0' })
  if (!releaseLate) throw new Error('The original request was not held')
  releaseLate(dashboard(calls[3].query, 999))
  await flushPromises()
  expect(count()).toBe('今日焊接作业次数：3 次')

  const readCount = calls.length
  setDeviceCatalog({ default_device_id: 'weld-b', items: [weldLine, { ...otherLine, enabled: false }] })
  await flushPromises()
  expect(wrapper.text()).toContain('焊接产线未启用')
  expect(wrapper.find('[aria-label^="今日焊接作业次数："]').exists()).toBe(false)
  expect(calls).toHaveLength(readCount)
})
