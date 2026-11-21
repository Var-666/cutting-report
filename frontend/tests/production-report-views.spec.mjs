// @vitest-environment jsdom

import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { init } from 'echarts/core'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { KeepAlive, h } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { getLeadershipRange, getLeadershipReport } from '../src/api/cutting'
import { getEnergyDay, getEnergyMonthlyRange, getEnergyRange, getTaskEnergyDay } from '../src/api/energy'
import AnalysisPanel from '../src/components/AnalysisPanel.vue'
import AppDatePicker from '../src/components/AppDatePicker.vue'
import AppSelect from '../src/components/AppSelect.vue'
import StandardReportQuery from '../src/components/StandardReportQuery.vue'
import EnergyPeriodReportView from '../src/views/EnergyPeriodReportView.vue'
import LeadershipReportView from '../src/views/LeadershipReportView.vue'

vi.mock('../src/api/energy')
vi.mock('../src/api/cutting')
vi.mock('../src/api/reportDates', async importOriginal => ({
  ...await importOriginal(), reportToday: () => '2026-11-07',
}))
vi.mock('echarts/core', async importOriginal => ({
  ...await importOriginal(), init: vi.fn(() => ({ setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() })),
}))

enableAutoUnmount(afterEach)
beforeEach(() => {
  Object.defineProperty(Element.prototype, 'scrollTo', { configurable: true, value: vi.fn() })
  vi.mocked(init).mockClear()
  const production = { period_start: '2026-11-07', task_count: 3, cut_length_m: 25.1, groove_length_m: 0, work_time_seconds: 900 }
  vi.mocked(getLeadershipReport).mockResolvedValue({
    snapshot: { data_as_of: '2026-11-07' },
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
    rows: [{ report_date: '2026-11-01', task_count: 5, electricity: { value: 137 }, gas: { value: 2 } }],
  })
})

afterEach(() => { Reflect.deleteProperty(Element.prototype, 'scrollTo') })

async function openReport(kind = 'equipment') {
  const observers = []
  vi.stubGlobal('ResizeObserver', class {
    observed = []
    disconnect = vi.fn()
    constructor(callback) { this.notify = callback; observers.push(this) }
    observe(node) { this.observed.push(node) }
  })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: `/${kind}`, name: `${kind}-report`, component: kind === 'equipment' ? EnergyPeriodReportView : LeadershipReportView },
      { path: '/other', component: { render: () => h('h1', '其他报表') } },
    ],
  })
  await router.push(`/${kind}`)
  const wrapper = mount({
    render: () => h(RouterView, {}, {
      default: ({ Component }) => h(KeepAlive, {}, { default: () => Component }),
    }),
  }, {
    global: { plugins: [router, createPinia()], components: { AppSelect, AppDatePicker }, stubs: { AnalysisPanel: true } },
  })
  await flushPromises()
  return { wrapper, router, observers }
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
        records: [{ ...day.records[0], production_time: `${start}T09:00:00`, electricity_consumption: 42 }] } }
    }
    return { snapshot: { data_as_of: end },
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
  view.getComponent(StandardReportQuery).vm.$emit('export', 'html')
  expect(view.emitted('export')).toEqual([[expect.objectContaining({
    report_type: kind, period_start: '2026-09-01', period_end: '2026-09-02',
    parameters: expect.objectContaining({ summary_period_start: '2025-12-01', summary_period_end: '2026-11-07' }),
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
