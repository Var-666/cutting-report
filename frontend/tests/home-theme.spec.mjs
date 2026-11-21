// @vitest-environment jsdom

import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { init } from 'echarts/core'
import { NConfigProvider } from 'naive-ui'
import { createPinia, disposePinia } from 'pinia'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { h, onUnmounted, reactive } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { getAlarmStatus, listAlarmIncidents } from '../src/api/alarms'
import { getLeadershipReport, listReports } from '../src/api/cutting'
import { getEnergyMonth } from '../src/api/energy'
import HomeKpiCard from '../src/components/HomeKpiCard.vue'
import { useAppStore } from '../src/state/app'
import AdminHomeView from '../src/views/AdminHomeView.vue'

vi.mock('../src/api/alarms')
vi.mock('../src/api/cutting')
vi.mock('../src/api/energy')
vi.mock('../src/api/reportDates', async importOriginal => ({
  ...await importOriginal(), reportToday: () => '2026-10-23',
}))
vi.mock('echarts/core', async importOriginal => ({
  ...await importOriginal(), init: vi.fn(() => ({ setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() })),
}))

enableAutoUnmount(afterEach)
beforeEach(() => {
  vi.clearAllMocks()
  getLeadershipReport.mockResolvedValue({ snapshot: { data_as_of: '2026-10-23' }, current_month_total: { task_count: 0 }, daily_records: [] })
  getEnergyMonth.mockResolvedValue({ rows: [], summary: {} })
  listReports.mockResolvedValue({ items: [], total: 0 })
  getAlarmStatus.mockResolvedValue({ enabled: false })
  listAlarmIncidents.mockResolvedValue([])
})
afterEach(() => { vi.useRealTimers() })

async function open({ reducedMotion = true } = {}) {
  let resize
  const disconnected = vi.fn()
  vi.stubGlobal('ResizeObserver', class {
    constructor(callback) { resize = callback }
    observe() {}
    disconnect() { disconnected() }
  })
  const pinia = createPinia()
  const app = useAppStore(pinia)
  app.reducedMotion = reducedMotion
  const colors = reactive({ primaryColor: '#113399', successColor: '#338811' })
  const paths = ['/', '/admin/alarms', ...['leadership', 'workshop', 'equipment', 'center'].map(name => `/admin/reports/${name}`)]
  const router = createRouter({ history: createMemoryHistory(), routes: paths.map(path => ({ path, component: AdminHomeView })) })
  await router.push('/')
  const wrapper = mount({
    setup() { onUnmounted(() => disposePinia(pinia)) },
    render: () => h(NConfigProvider, { themeOverrides: { common: { ...colors } } }, { default: () => h(AdminHomeView) }),
  }, { global: { plugins: [router, pinia] } })
  await flushPromises()
  return {
    wrapper, colors, app, resize: () => resize(), disconnected,
    card: title => wrapper.findAllComponents(HomeKpiCard).find(card => card.props('title') === title),
    chart: label => {
      const index = vi.mocked(init).mock.calls.findIndex(([element]) => element.getAttribute('aria-label').includes(label))
      return vi.mocked(init).mock.results[index].value
    },
  }
}

test('production renders before the meter reply; theme and reduced motion redraw existing charts', async () => {
  let finishProduction, finishMeter
  getLeadershipReport.mockReturnValue(new Promise(resolve => { finishProduction = resolve }))
  getEnergyMonth.mockReturnValue(new Promise(resolve => { finishMeter = resolve }))
  const home = await open({ reducedMotion: false })
  expect(home.card('本月任务').props('loading')).toBe(true)
  expect(home.wrapper.get('[aria-label="电耗"]').get('[role="status"]').text()).toBe('')
  const metrics = { task_count: 232, cut_length_m: 9714.2, work_time_seconds: 78390 }
  finishProduction({ snapshot: { data_as_of: '2026-10-23' }, current_month_total: metrics,
    daily_records: [{ period_start: '2026-10-01', ...metrics }] })
  await flushPromises()
  expect(home.card('本月任务').attributes('aria-label')).toBe('本月任务：232 项')
  expect(home.card('设备工时').attributes('aria-label')).toBe('设备工时：21 小时 46分')
  expect(home.wrapper.get('[aria-label="电耗"]').find('[role="status"]').exists()).toBe(true)
  const trend = home.chart('切割长度')
  const status = home.chart('生产日状态')
  home.resize()
  expect(trend.resize).toHaveBeenCalledOnce()
  expect(status.resize).toHaveBeenCalledOnce()
  expect(status.setOption.mock.lastCall[0].series[0].data[0]).toEqual({ name: '生产日', value: 1 })
  expect(trend.setOption.mock.lastCall[0].color).toEqual(['#113399', '#338811'])
  Object.assign(home.colors, { primaryColor: '#116699', successColor: '#117722' })
  home.app.reducedMotion = true
  await flushPromises()
  expect(trend.setOption.mock.lastCall[0]).toMatchObject({ color: ['#116699', '#117722'], animationDuration: 0 })
  expect(getLeadershipReport).toHaveBeenCalledOnce()
  expect(init).toHaveBeenCalledTimes(2)
  finishMeter({ rows: [], summary: {}, energy_connected: false })
  await flushPromises()
  expect(home.wrapper.get('[aria-label="电耗"]').find('[role="status"]').exists()).toBe(false)
  expect(home.wrapper.get('[aria-label="电耗"]').text()).toContain('暂未接入')
})

test('production failure leaves report history and alarm status visible with real quick links', async () => {
  getLeadershipReport.mockRejectedValue(new Error('生产数据读取失败'))
  listReports.mockResolvedValue({ items: [{ report_id: 'r1', report_title: '已生成的班组周报', status: 'completed' }], total: 1 })
  const { wrapper, card } = await open()
  expect(wrapper.get('[role="alert"]').text()).toContain('生产统计未能更新')
  expect(wrapper.text()).toContain('已生成的班组周报')
  expect(wrapper.text()).toContain('告警监控未启用')
  expect(card('本月任务').text()).toContain('暂无数据')
  expect(wrapper.find('[aria-label="正在获取能耗数据"]').exists()).toBe(false)
  expect(wrapper.get('nav[aria-label="核心报表快速入口"]').findAll('a').map(link => [link.text(), link.attributes('href')]))
    .toEqual([['领导报表', '/admin/reports/leadership'], ['车间报表', '/admin/reports/workshop'], ['设备报表', '/admin/reports/equipment']])
})

test('retry reloads only the failed meter module and distinguishes measured zero from unknown work time', async () => {
  getLeadershipReport.mockResolvedValue({ snapshot: { data_as_of: '2026-10-23' },
    current_month_total: { task_count: 1, cut_length_m: 0, work_time_seconds: null } })
  getEnergyMonth.mockRejectedValue(new Error('能源接口不可用'))
  const { wrapper, card } = await open()
  const retry = wrapper.findAll('button').find(button => button.text() === '重试失败项目')
  expect(wrapper.get('[role="alert"]').text()).toContain('能耗未能更新')
  expect(card('设备工时').text()).toContain('暂无数据')
  getEnergyMonth.mockResolvedValue({ summary: { electricity: { value: 0 } } })
  await retry.trigger('click')
  await retry.trigger('click')
  await flushPromises()
  expect(wrapper.get('[aria-label="电耗"]').text()).toContain('0.0')
  expect(wrapper.text()).not.toContain('能耗未能更新')
  expect(getLeadershipReport).toHaveBeenCalledOnce()
  expect(getEnergyMonth.mock.calls.map(([, options]) => options.mode)).toEqual(['read', 'refresh'])
})

test('a cold placeholder remains in preparation and leaving aborts the active production poll', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  let finishPoll
  getLeadershipReport.mockResolvedValueOnce({
    current_month_total: { task_count: 0 }, snapshot: { stale: true, refreshing: true, data_as_of: null },
  }).mockImplementationOnce(() => new Promise(resolve => { finishPoll = resolve }))
  const { wrapper, card } = await open()
  expect(card('本月任务').props('loading')).toBe(true)
  expect(card('本月任务').props('value')).toBeNull()
  expect(init).not.toHaveBeenCalled()
  await vi.advanceTimersByTimeAsync(5000)
  const [, { mode, signal }] = getLeadershipReport.mock.lastCall
  expect(mode).toBe('poll')
  wrapper.unmount()
  expect(signal.aborted).toBe(true)
  finishPoll({ current_month_total: { task_count: 8 }, snapshot: { data_as_of: '2026-10-23' } })
  await flushPromises()
  expect(init).not.toHaveBeenCalled()
})

test('daily records keep chronological values and the KPI comparisons use the prior period independently', async () => {
  getLeadershipReport.mockResolvedValue({
    snapshot: { data_as_of: '2026-10-23' },
    current_month_total: { task_count: 25, cut_length_m: 50, work_time_seconds: 39600 },
    comparison_total: { task_count: 20, cut_length_m: 100, work_time_seconds: 36000 },
    daily_records: [
      { period_start: '2026-10-03', task_count: 10, cut_length_m: 5, work_time_seconds: 7200 },
      { period_start: '2026-10-01', task_count: 4, cut_length_m: 20, work_time_seconds: 3600 },
      { period_start: '2026-10-02', task_count: 1, cut_length_m: 10, work_time_seconds: null },
    ],
  })
  const { card, chart, wrapper } = await open()
  expect(card('本月任务').props('series')).toEqual([4, 1, 10])
  expect(card('本月任务').find('[aria-label="↑ 25% 较上月"]').exists()).toBe(true)
  expect(card('切割长度').find('[aria-label="↓ 50% 较上月"]').exists()).toBe(true)
  expect(card('设备工时').find('[aria-label="↑ 10% 较上月"]').exists()).toBe(true)
  expect(chart('切割长度').setOption.mock.lastCall[0].series.map(series => series.data))
    .toEqual([['20.0', '10.0', '5.0'], ['1.0', null, '2.0']])
  expect(wrapper.text()).toContain('切割长度低于上月 50%')
})

test.each([
  [100.01, 100, '↑ <0.1% 较上月'],
  [99.99, 100, '↓ <0.1% 较上月'],
  [100, 100, '与上月持平'],
  [0, 100, '↓ 100% 较上月'],
  [10, 0, '暂无上月对比'],
  [10, null, '暂无上月对比'],
])('cut length %s compared with %s shows %s', async (cut_length_m, previous, expected) => {
  getLeadershipReport.mockResolvedValue({ snapshot: { data_as_of: '2026-10-23' },
    current_month_total: { task_count: 1, cut_length_m }, comparison_total: { cut_length_m: previous } })
  const { card } = await open()
  expect(card('切割长度').find(`[aria-label="${expected}"]`).exists()).toBe(true)
})
