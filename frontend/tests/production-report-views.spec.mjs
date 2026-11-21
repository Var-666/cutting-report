// @vitest-environment jsdom

import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { init } from 'echarts/core'
import { NConfigProvider, darkTheme } from 'naive-ui'
import { createPinia, disposePinia } from 'pinia'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { KeepAlive, h, onUnmounted } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { getLeadershipRange, getLeadershipReport } from '../src/api/cutting'
import { getEnergyDay, getEnergyMonthlyRange, getEnergyRange, getTaskEnergyDay } from '../src/api/energy'
import AnalysisPanel from '../src/components/AnalysisPanel.vue'
import AppDatePicker from '../src/components/AppDatePicker.vue'
import AppSelect from '../src/components/AppSelect.vue'
import StandardReportQuery from '../src/components/StandardReportQuery.vue'
import WorkshopStructureCharts from '../src/components/WorkshopStructureCharts.vue'
import { useThemeStore } from '../src/store/modules/theme'
import EnergyPeriodReportView from '../src/views/EnergyPeriodReportView.vue'
import LeadershipReportView from '../src/views/LeadershipReportView.vue'
import { stubTableLayout } from './table-layout-fixture.mjs'

vi.mock('../src/api/energy')
vi.mock('../src/api/cutting')
vi.mock('../src/api/reportDates', async importOriginal => ({
  ...await importOriginal(), reportToday: () => '2026-11-07',
}))
vi.mock('echarts/core', async importOriginal => ({
  ...await importOriginal(), init: vi.fn(element => ({ getDom: () => element, setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() })),
}))

enableAutoUnmount(afterEach)
beforeEach(() => {
  Object.defineProperty(Element.prototype, 'scrollTo', { configurable: true, value: vi.fn() })
  vi.mocked(init).mockClear()
  const production = { period_start: '2026-11-07', task_count: 3, cut_length_m: 25.1, groove_length_m: 0, work_time_seconds: 900 }
  vi.mocked(getLeadershipReport).mockResolvedValue({
    snapshot: { data_as_of: '2026-11-07' }, analysis_source_version: 'leadership-default',
    analysis_summary_source_version: 'leadership-twelve-months',
    daily_records: [production], monthly_records: [{ ...production, period_start: '2026-11-01' }],
    current_month_total: production, twelve_month_total: production,
  })
  vi.mocked(getEnergyDay).mockResolvedValue({})
  vi.mocked(getTaskEnergyDay).mockResolvedValue({
    records: ['实测', '插值', '平均速率补齐；有效时段 08:00～08:30', '缺失', '计数器重置'].map((note, index) => ({
      task_id: String(index),
      project_code: index ? 'CMYZ0170' : null, ship_no: index ? '非船' : '', segment_name: index ? '804C' : '  ',
      production_time: `2026-11-07T08:0${index}:00`, energy_note: note,
      electricity_consumption: 27.4, gas_consumption: [0, 1, 1, null, null][index],
    })),
    equipment_scope: ['Cutter01'], units: { electricity: 'kWh', gas: 'Nm³' },
    summary: { task_count: 5, total_electricity_consumption: 137, total_gas_consumption: 2 },
  })
  vi.mocked(getEnergyMonthlyRange).mockResolvedValue({
    analysis_source_version: 'energy-twelve-months',
    rows: [{ report_date: '2026-11-01', task_count: 5, electricity: { value: 137 }, gas: { value: 2 } }],
  })
})

afterEach(() => {
  vi.useRealTimers()
  Reflect.deleteProperty(Element.prototype, 'scrollTo')
})

async function openReport(kind = 'equipment', query = {}) {
  stubTableLayout()
  vi.stubGlobal('matchMedia', () => Object.assign(new EventTarget(), { matches: false }))
  const observers = []
  vi.stubGlobal('ResizeObserver', class {
    observed = []
    disconnect = vi.fn()
    constructor(callback) { this.notify = callback; observers.push(this) }
    observe(node) { this.observed.push(node) }
  })
  const workshop = { render: () => h(WorkshopStructureCharts, {
    durationDistribution: [{ key: 'short', label: '短任务', count: 4 }],
    statusDistribution: [{ key: 'confirmed', label: '已确认', count: 4 }],
    topCutLengthTasks: [{ task_id: '1', cut_length_m: 12 }],
  }) }
  const component = { equipment: EnergyPeriodReportView, leadership: LeadershipReportView, workshop }[kind]
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: `/${kind}`, name: `${kind}-report`, component },
      { path: '/other', component: { render: () => h('h1', '其他报表') } },
    ],
  })
  await router.push({ path: `/${kind}`, query })
  const pinia = createPinia()
  const theme = useThemeStore(pinia)
  theme.setThemeScheme('light')
  const wrapper = mount({
    setup() { onUnmounted(() => disposePinia(pinia)) },
    render: () => h(NConfigProvider, { theme: theme.darkMode ? darkTheme : null, themeOverrides: theme.naiveTheme }, {
      default: () => h(RouterView, {}, {
        default: ({ Component }) => h(KeepAlive, {}, { default: () => Component }),
      }),
    }),
  }, {
    global: { plugins: [router, pinia], components: { AppSelect, AppDatePicker }, stubs: { AnalysisPanel: true } },
  })
  await flushPromises()
  return { wrapper, router, theme, observers }
}

test('task energy shows numeric zero and missing readings with their evidence and retains rows on summary failure', async () => {
  vi.mocked(getEnergyMonthlyRange).mockRejectedValue(new Error('月度统计不可用'))
  const { wrapper } = await openReport()
  const table = wrapper.get('[aria-label="任务能耗明细表"]')
  expect(table.findAll('thead th').map(cell => cell.text())).toEqual(['序号', '工程号', '船名', '分段名', '任务开始', '切割时长', '电耗', '气耗'])
  const rows = table.findAll('tbody tr').slice(0, 5)
  expect(rows[0].findAll('td').slice(1, 4).map(cell => cell.text())).toEqual(['--', '--', '--'])
  expect(rows[1].findAll('td').slice(1, 4).map(cell => cell.text())).toEqual(['CMYZ0170', '非船', '804C'])
  expect(rows.map(row => row.findAll('td').slice(-2).map(cell => cell.text())))
    .toEqual([['27.4', '0.0'], ['27.4', '1.0'], ['27.4', '1.0'], ['27.4', '--'], ['27.4', '--']])
  expect(rows[2].findAll('td').at(-1).get('span[title^="平均速率补齐"]').attributes('title'))
    .toContain('08:00～08:30')
  expect(wrapper.findAll('[role="alert"]').some(alert => alert.text().includes('月度统计不可用'))).toBe(true)
})

test.each([
  ['equipment', ['任务能耗图', '月度能耗图'], '27.4'],
  ['leadership', ['区间每日趋势图', '近12个月月度趋势图'], '25.1'],
])('%s reconnects chart observers on return and disposes after leaving the cache', async (kind, labels, measurement) => {
  const { wrapper, router, observers } = await openReport(kind)
  const chartObserver = () => observers.filter(observer => observer.observed.some(node => node.getAttribute('role') === 'img')).at(-1)
  const first = chartObserver()
  expect(first.observed.map(node => node.getAttribute('aria-label'))).toEqual(labels)
  const charts = vi.mocked(init).mock.results.map(result => result.value)
  expect(charts).toHaveLength(2)
  charts.forEach(chart => chart.resize.mockClear())
  first.notify()
  for (const chart of charts) expect(chart.resize).toHaveBeenCalledOnce()
  await router.push('/other')
  await flushPromises()
  expect(first.disconnect).toHaveBeenCalledOnce()
  first.notify()
  for (const chart of charts) {
    expect(chart.resize).toHaveBeenCalledOnce()
    expect(chart.dispose).not.toHaveBeenCalled()
  }
  await router.push(`/${kind}`)
  await flushPromises()
  const reopened = chartObserver()
  expect(reopened).not.toBe(first)
  expect(reopened.observed).toHaveLength(2)
  expect(wrapper.findAll('tbody').some(table => table.text().includes(measurement))).toBe(true)
  expect(init).toHaveBeenCalledTimes(2)
  wrapper.unmount()
  expect(reopened.disconnect).toHaveBeenCalledOnce()
  for (const chart of charts) expect(chart.dispose).toHaveBeenCalledOnce()
})

test.each([
  ['equipment', '月度能耗图', '任务能耗图'],
  ['leadership', '近12个月月度趋势图', '区间每日趋势图'],
])('%s query and export keep the twelve-month summary independent of the selected detail range', async (kind, summaryLabel, detailLabel) => {
  const { wrapper, router } = await openReport(kind)
  const readRange = kind === 'equipment' ? vi.mocked(getEnergyRange) : vi.mocked(getLeadershipRange)
  const baseline = kind === 'equipment' ? vi.mocked(getEnergyMonthlyRange) : vi.mocked(getLeadershipReport)
  const count = baseline.mock.calls.length
  readRange.mockImplementation(async (start, end) => {
    if (kind === 'equipment') {
      const day = await getTaskEnergyDay(start)
      return { task_report: { ...day, period_start: start, period_end: end,
        analysis_source_version: 'applied-detail',
        records: [{ ...day.records[0], production_time: `${start}T09:00:00`, electricity_consumption: 42 }] } }
    }
    return { snapshot: { data_as_of: end }, analysis_source_version: 'applied-detail',
      daily_records: [{ period_start: start, task_count: 3, cut_length_m: 42, groove_length_m: 0, work_time_seconds: 900 }] }
  })
  const area = label => wrapper.get(`[role="img"][aria-label="${label}"]`).element.closest('section')
  expect(area(summaryLabel).textContent).toContain('2025年12月01日 至 2026年11月07日')
  await router.push(`/${kind}?start=2026-09-01&end=2026-09-02`)
  await flushPromises()
  expect(readRange).toHaveBeenLastCalledWith('2026-09-01', '2026-09-02', expect.objectContaining({ signal: expect.any(AbortSignal) }))
  expect(baseline.mock.calls).toHaveLength(count)
  expect(area(detailLabel).textContent).toContain('42.0')
  expect(area(detailLabel).textContent).toContain('2026年09月01日 至 2026年09月02日')
  expect(area(summaryLabel).textContent).toContain('2025年12月01日 至 2026年11月07日')
  const view = wrapper.getComponent(kind === 'equipment' ? EnergyPeriodReportView : LeadershipReportView)
  const query = view.getComponent(StandardReportQuery)
  query.vm.$emit('update:startDate', '2026-08-05')
  query.vm.$emit('update:chartType', 'line')
  await flushPromises()
  expect(query.props('dateDirty')).toBe(true)
  expect(view.getComponent(AnalysisPanel).props()).toMatchObject({
    reportType: kind, periodStart: '2026-09-01', periodEnd: '2026-09-02', dataVersion: 'applied-detail',
    summaryPeriodStart: '2025-12-01', summaryPeriodEnd: '2026-11-07',
    summaryDataVersion: kind === 'equipment' ? 'energy-twelve-months' : 'leadership-twelve-months',
  })
  query.vm.$emit('export', 'html')
  expect(view.emitted('export')).toEqual([[expect.objectContaining({
    report_type: kind, period_start: '2026-09-01', period_end: '2026-09-02',
    parameters: expect.objectContaining({ chart_type: 'line', summary_period_start: '2025-12-01', summary_period_end: '2026-11-07' }),
  }), 'html']])
})

test.each([
  ['equipment', getTaskEnergyDay],
  ['leadership', getLeadershipReport],
])('%s reloads the source when the analysis requests a refreshed version', async (kind, load) => {
  const { wrapper } = await openReport(kind)
  const reads = vi.mocked(load)
  const before = reads.mock.calls.length
  const panel = wrapper.getComponent(AnalysisPanel)
  expect(panel.props('versionCheckEnabled')).toBe(true)
  panel.vm.$emit('refresh-report')
  await flushPromises()
  expect(reads.mock.calls).toHaveLength(before + 1)
  expect(reads.mock.lastCall[1]).toMatchObject({ force: true, signal: expect.any(AbortSignal) })
})

function chartOptions(label) {
  const charts = vi.mocked(init).mock
  const index = charts.calls.findLastIndex(([element]) => element.getAttribute('aria-label') === label)
  return charts.results[index].value.setOption.mock.lastCall[0]
}

test('leadership chart choices retain daily work hours and monthly completion totals on their own axes', async () => {
  const rows = [
    { period_start: '2026-10-23', task_count: 4, cut_length_m: 12.4, groove_length_m: 0, work_time_seconds: 3600 },
    { period_start: '2026-10-22', task_count: 8, cut_length_m: 42.5, groove_length_m: 2.6, work_time_seconds: 7200 },
  ]
  const original = structuredClone(rows)
  const monthly = rows.map((row, index) => ({ ...row, period_start: index ? '2026-09-01' : '2026-10-01' }))
  getLeadershipReport.mockResolvedValue({ snapshot: {}, daily_records: rows, monthly_records: monthly })
  getLeadershipRange.mockResolvedValue({ snapshot: {}, daily_records: rows })
  const { wrapper, router } = await openReport('leadership')
  await router.push('/leadership?start=2026-10-22&end=2026-10-23')
  await flushPromises()
  for (const mode of ['bar_line', 'bar', 'line', 'area']) {
    wrapper.getComponent(StandardReportQuery).vm.$emit('update:chartType', mode)
    await flushPromises()
    const daily = chartOptions('区间每日趋势图')
    const summary = chartOptions('近12个月月度趋势图')
    expect(daily.legend.data).toEqual(['切割长度', '坡口长度', '切割工时'])
    expect(summary.legend.data).toEqual(['切割长度', '坡口长度', '完成任务'])
    expect(daily.series.map(series => series.data)).toEqual([[42.5, 12.4], [2.6, 0], [2, 1]])
    expect(summary.series.map(series => series.data)).toEqual([[42.5, 12.4], [2.6, 0], [8, 4]])
    expect(daily.yAxis.map(axis => axis.name)).toEqual(['长度（米）', '工时（小时）'])
    expect(summary.yAxis.map(axis => axis.name)).toEqual(['长度（米）', '任务数'])
    const types = mode === 'bar_line' ? ['bar', 'bar', 'line'] : Array(3).fill(mode === 'bar' ? 'bar' : 'line')
    for (const option of [daily, summary]) {
      expect(option.series.map(series => series.type)).toEqual(types)
      expect(option.series.map(series => series.yAxisIndex ?? 0)).toEqual([0, 0, 1])
    }
  }
  expect(rows).toEqual(original)
})

test('leadership charts pad missing calendar dates without treating them as verified production zero', async () => {
  const rows = [
    { period_start: '2026-10-01', task_count: 3, cut_length_m: 30, groove_length_m: 0, work_time_seconds: 3600 },
    { period_start: '2026-10-03', task_count: 4, cut_length_m: 40, groove_length_m: 2, work_time_seconds: 7200 },
  ]
  getLeadershipRange.mockResolvedValue({ snapshot: {}, daily_records: rows })
  const { wrapper, router } = await openReport('leadership')
  await router.push('/leadership?start=2026-10-01&end=2026-10-28')
  await flushPromises()
  const daily = () => chartOptions('区间每日趋势图')
  expect(daily().xAxis.data).toHaveLength(28)
  expect(daily().xAxis.data.slice(0, 4)).toEqual(['10-01', '10-02', '10-03', '10-04'])
  expect(daily().xAxis.data.at(-1)).toBe('10-28')
  expect(daily().series.map(series => series.data.slice(0, 4))).toEqual([[30, null, 40, null], [0, null, 2, null], [1, null, 2, null]])
  const panel = wrapper.get('[role="img"][aria-label="区间每日趋势图"]').element.closest('section')
  const days = [...panel.querySelectorAll('tbody tr')].flatMap(row => row.textContent.match(/2026年10月\d{2}日/g) ?? [])
  expect(days.toSorted()).toEqual(['2026年10月01日', '2026年10月03日'])
  await router.push('/leadership?start=2024-02-28&end=2024-03-01')
  await flushPromises()
  expect(daily().xAxis.data).toEqual(['02-28', '02-29', '03-01'])
  await router.push('/leadership?start=2025-12-31&end=2026-02-01')
  await flushPromises()
  expect(daily().xAxis.data).toHaveLength(33)
  expect(daily().series[0].data).toHaveLength(33)
})

test.each(['leadership', 'equipment', 'workshop'])('%s charts recover the theme when a cached page is shown again', async kind => {
  const { router, theme } = await openReport(kind)
  const reads = [getLeadershipReport, getLeadershipRange, getEnergyDay, getTaskEnergyDay, getEnergyMonthlyRange]
  const before = reads.map(read => read.mock.calls.length)
  const beforeTheme = vi.mocked(init).mock.results.map(({ value }) => value)
  expect(beforeTheme).toHaveLength(2)
  theme.setThemeScheme('dark')
  await flushPromises()
  expect(beforeTheme.every(chart => chart.dispose.mock.calls.length === 1)).toBe(true)
  expect(vi.mocked(init).mock.calls.slice(-2).map(call => call[1])).toEqual(['dark', 'dark'])
  expect(reads.map(read => read.mock.calls.length)).toEqual(before)
  const original = theme.themeColor
  theme.updateThemeColors('primary', '#cc4400')
  await flushPromises()
  expect(vi.mocked(init).mock.results.slice(-2).some(({ value }) =>
    JSON.stringify(value.setOption.mock.lastCall[0]).includes(theme.themeColor))).toBe(true)
  expect(reads.map(read => read.mock.calls.length)).toEqual(before)
  await router.push('/other')
  await flushPromises()
  const count = vi.mocked(init).mock.calls.length
  theme.setThemeScheme('light')
  theme.updateThemeColors('primary', original)
  await flushPromises()
  expect(init).toHaveBeenCalledTimes(count)
  await router.push(`/${kind}`)
  await flushPromises()
  expect(vi.mocked(init).mock.calls.slice(-2).map(call => call[1])).toEqual([undefined, undefined])
})

test('equipment resumes the cached monthly poll and updates its chart when the meter summary is ready', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const initial = { syncing: true, missing_day_count: 2, rows: [{ report_date: '2026-11-01', electricity: { value: 9 }, gas: { value: 2 } }] }
  const completed = { syncing: false, missing_day_count: 0, rows: [{ report_date: '2026-11-01', electricity: { value: 42 }, gas: { value: 8 } }] }
  let finish
  const summary = getEnergyMonthlyRange.mockImplementation(async (_start, _end, options) => options?.mode === 'poll'
    ? new Promise(resolve => { finish = resolve }) : initial)
  const { wrapper, router } = await openReport()
  const old = summary.mock.calls.length
  const polls = () => summary.mock.calls.slice(old).filter(([, , options]) => options?.mode === 'poll')
  const electricity = () => chartOptions('月度能耗图').series[0].data
  expect(electricity()).toEqual([9])
  await router.push('/other')
  await vi.advanceTimersByTimeAsync(5000)
  expect(polls()).toHaveLength(0)
  await router.push('/equipment')
  await flushPromises()
  await vi.advanceTimersByTimeAsync(5000)
  expect(polls()).toHaveLength(1)
  wrapper.getComponent(StandardReportQuery).vm.$emit('update:chartType', 'line')
  await vi.advanceTimersByTimeAsync(5000)
  expect(polls()).toHaveLength(1)
  finish(completed)
  await flushPromises()
  expect(electricity()).toEqual([42])
  await vi.advanceTimersByTimeAsync(10_000)
  expect(polls()).toHaveLength(1)
})

const rangeA = { start: '2026-09-01', end: '2026-09-02' }
const rangeB = { start: '2026-09-03', end: '2026-09-04' }
const rangeC = { start: '2026-09-05', end: '2026-09-06' }

function reportRangeResult(kind, start, end, measurement) {
  if (kind === 'equipment') {
    return { task_report: { period_start: start, period_end: end,
      equipment_scope: ['Cutter01'], units: { electricity: 'kWh', gas: 'Nm³' }, summary: { task_count: 1 },
      records: [{ task_id: 'manual-range', production_time: `${start}T08:00:00`, electricity_consumption: measurement }],
    } }
  }
  return { snapshot: { data_as_of: end }, daily_records: [{ period_start: start, cut_length_m: measurement }] }
}

test.each([
  ['leadership', getLeadershipRange, '区间每日趋势图'],
  ['equipment', getEnergyRange, '任务能耗图'],
])('%s back and forward restore dated results, ignore obsolete replies and preserve unsubmitted dates on chart changes', async (kind, read, chartLabel) => {
  const pending = []
  let hold = false
  read.mockImplementation(async (start, end, { signal }) => hold
    ? new Promise(resolve => pending.push({ start, end, signal, finish: value => resolve(reportRangeResult(kind, start, end, value)) }))
    : reportRangeResult(kind, start, end, 19))
  const { wrapper, router } = await openReport(kind, { ...rangeA, chart: 'bar' })
  const chart = () => chartOptions(chartLabel).series[0].data
  const control = () => wrapper.getComponent(StandardReportQuery)
  expect(chart()).toContain(19)
  hold = true
  await router.push({ query: { ...rangeB, chart: 'line' } })
  await flushPromises()
  expect(pending).toHaveLength(1)
  expect(control().props('loading')).toBe(true)
  expect(chart()).toContain(19)
  expect(router.currentRoute.value.query).toMatchObject({ ...rangeB, chart: 'line' })
  router.back()
  await flushPromises()
  expect(pending[0].signal.aborted).toBe(true)
  pending[0].finish(59)
  await flushPromises()
  expect(chart()).not.toContain(59)
  expect(control().props('loading')).toBe(false)
  expect(control().props('chartType')).toBe('bar')

  router.forward()
  await flushPromises()
  expect(pending).toHaveLength(2)
  pending[1].finish(29)
  await flushPromises()
  expect(chart()).toContain(29)
  expect(control().props('chartType')).toBe('line')
  control().vm.$emit('update:startDate', '2026-08-17')
  await flushPromises()
  control().vm.$emit('update:chartType', 'bar')
  await flushPromises()
  expect(control().props('startDate')).toBe('2026-08-17')
  expect(control().props('dateDirty')).toBe(true)
  expect(router.currentRoute.value.query).toEqual({ ...rangeB, chart: 'bar' })
  expect(pending).toHaveLength(2)
})

test.each([
  ['leadership', getLeadershipRange, '区间每日趋势图'],
  ['equipment', getEnergyRange, '任务能耗图'],
])('%s a newer linked range wins when the old and new requests resolve in reverse order', async (kind, read, chartLabel) => {
  const pending = []
  let hold = false
  read.mockImplementation(async (start, end, { signal }) =>
    hold ? new Promise(resolve => pending.push({ start, end, signal, finish: value => resolve(reportRangeResult(kind, start, end, value)) }))
      : reportRangeResult(kind, start, end, 19))
  const { router, wrapper } = await openReport(kind, rangeA)
  hold = true
  await router.push({ query: rangeB })
  await flushPromises()
  await router.push({ query: { ...rangeC, chart: 'line' } })
  await flushPromises()
  expect(pending.map(call => call.start)).toEqual([rangeB.start, rangeC.start])
  expect(pending[0].signal.aborted).toBe(true)
  pending[1].finish(32)
  await flushPromises()
  pending[0].finish(11)
  await flushPromises()
  expect(chartOptions(chartLabel).series[0].data).toContain(32)
  expect(chartOptions(chartLabel).series[0].data).not.toContain(11)
  expect(router.currentRoute.value.query).toEqual({ ...rangeC, chart: 'line' })
  expect(wrapper.getComponent(StandardReportQuery).props('loading')).toBe(false)
})

test.each([
  ['leadership', getLeadershipRange],
  ['equipment', getEnergyRange],
])('%s choosing a chart while a submitted request is pending applies its dates only after success', async (kind, read) => {
  read.mockImplementation(async (start, end) => reportRangeResult(kind, start, end, 19))
  const { wrapper, router } = await openReport(kind, rangeA)
  const before = read.mock.calls.length
  let finish
  read.mockImplementationOnce((start, end) => new Promise(resolve => { finish = () => resolve(reportRangeResult(kind, start, end, 36)) }))
  const control = wrapper.getComponent(StandardReportQuery)
  control.vm.$emit('update:startDate', rangeB.start)
  control.vm.$emit('update:endDate', rangeB.end)
  await flushPromises()
  control.vm.$emit('submit')
  await flushPromises()
  control.vm.$emit('update:chartType', 'line')
  await flushPromises()
  expect(router.currentRoute.value.query).toEqual({ ...rangeA, chart: 'line' })
  expect(read.mock.calls.slice(before)).toEqual([[rangeB.start, rangeB.end, expect.objectContaining({ signal: expect.any(AbortSignal) })]])
  expect(read.mock.lastCall[2].signal.aborted).toBe(false)
  finish()
  await flushPromises()
  expect(router.currentRoute.value.query).toEqual({ ...rangeB, chart: 'line' })
  expect(control.props('loading')).toBe(false)
})
