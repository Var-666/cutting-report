// @vitest-environment jsdom

import assert from 'node:assert/strict'
import { enableAutoUnmount, mount } from '@vue/test-utils'
import { init } from 'echarts/core'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import WeldingChart from '../src/components/WeldingChart.vue'
import WeldingCharts from '../src/components/WeldingCharts.vue'
import { useThemeStore } from '../src/store/modules/theme'
import { weldingTrendOption, weldingProcessOption, weldingLengthOption } from '../src/utils/weldingChartOptions'
import { weldingProcessRows, weldingMetricSeries } from '../src/utils/weldingReport'

vi.mock('echarts/core', async importOriginal => {
  const actual = await importOriginal()
  return {
    ...actual,
    // jsdom has no layout. Supply dimensions while retaining ECharts' real SVG and legend state.
    init: vi.fn((_node, theme, options) => actual.init(null, theme, { ...options, ssr: true, width: 600, height: 280 })),
  }
})

enableAutoUnmount(afterEach)
beforeEach(() => {
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({ measureText: text => ({ width: text.length * 8 }) })
})
afterEach(() => {
  vi.mocked(init).mockClear()
  localStorage.clear()
})

function stubResizeObserver() {
  const observers = []
  vi.stubGlobal('ResizeObserver', class {
    observe = vi.fn()
    disconnect = vi.fn()
    constructor(notify) { observers.push({ notify, observe: this.observe, disconnect: this.disconnect }) }
  })
  return observers
}

const report = {
  daily: [{ production_date: '2026-11-17', job_count: 2, weld_length_m: 10 }],
  device_distribution: [{ device_no: 1, weld_length_m: 10 }],
  projects: [{ project_no: 'P1', part_count: 2, cumulative_actual_length_m: 10 }],
  summary: { flat_length_m: 10 },
}

test.each([
  ['daily', '每日焊接趋势', null], ['parts', '项目工件分布', 'P1'],
  ['jobs', '焊接工艺结构', '平焊'], ['equipment', '设备产出对比', null],
])('%s shows its own chart and only the distinct grouped data', (kind, title, groupedText) => {
  stubResizeObserver()
  const wrapper = mount(WeldingCharts, {
    props: { kind, report },
    global: { plugins: [createPinia()] },
  })
  expect(wrapper.findAll('h2').map(heading => heading.text())).toEqual([title])
  expect(wrapper.findAllComponents(WeldingChart)).toHaveLength(1)
  expect(wrapper.findComponent(WeldingChart).attributes('aria-label')).toBe(title)
  expect(wrapper.find('table').exists()).toBe(Boolean(groupedText))
  if (groupedText) expect(wrapper.get('tbody').text()).toContain(groupedText)
})

test('missing process measurements show the empty message without a chart', () => {
  stubResizeObserver()
  const wrapper = mount(WeldingCharts, {
    props: { kind: 'jobs', report: { ...report, summary: {} } },
    global: { plugins: [createPinia()] },
  })
  expect(wrapper.text()).toContain('暂无有效工艺长度数据')
  expect(wrapper.findComponent(WeldingChart).exists()).toBe(false)
})

function trend(date, length, jobs) {
  return weldingTrendOption([{ production_date: date, weld_length_m: length, job_count: jobs }], '#123456', '#abcdef')
}

function mountChart() {
  const observers = stubResizeObserver()
  const pinia = createPinia()
  const theme = useThemeStore(pinia)
  theme.setThemeScheme('light')
  const wrapper = mount(WeldingChart, {
    props: { option: trend('2026-10-26', 12, 2), label: '生产趋势' },
    global: { plugins: [pinia] },
  })
  return { wrapper, theme, observers, chart: vi.mocked(init).mock.results[0].value }
}

test('updating data preserves legend selection and the observed SVG chart instance', async () => {
  const { wrapper, observers, chart } = mountChart()
  expect(init).toHaveBeenCalledWith(wrapper.element, undefined, { renderer: 'svg' })
  chart.dispatchAction({ type: 'legendUnSelect', name: '作业次数' })
  await wrapper.setProps({ option: trend('2026-10-27', 22, 3) })
  expect(init).toHaveBeenCalledOnce()
  const option = chart.getOption()
  expect(option.series.map(series => series.data)).toEqual([[22], [3]])
  expect(option.legend[0].selected['作业次数']).toBe(false)
  const resize = vi.spyOn(chart, 'resize')
  observers[0].notify()
  expect(resize).toHaveBeenCalledOnce()
})

test('changing the theme rebuilds the chart and leaving disconnects its observer', async () => {
  const { wrapper, theme, observers, chart } = mountChart()
  theme.setThemeScheme('dark')
  await wrapper.vm.$nextTick()
  expect(chart.isDisposed()).toBe(true)
  expect(init).toHaveBeenCalledTimes(2)
  expect(init).toHaveBeenLastCalledWith(wrapper.element, 'dark', { renderer: 'svg' })
  expect(observers).toHaveLength(1)
  const currentChart = vi.mocked(init).mock.results[1].value
  wrapper.unmount()
  expect(currentChart.isDisposed()).toBe(true)
  expect(observers[0].disconnect).toHaveBeenCalledOnce()
})

test('process and ranking options preserve unknown types, zero values and missing data without invented trends', () => {
  const rows = weldingProcessRows({ flat_length_m: 0, unknown_length_m: 2 })
  assert.equal(rows.find(row => row.key === 'flat_length_m').length, 0)
  assert.equal(rows.find(row => row.key === 'vertical_length_m').length, null)
  assert.deepEqual(weldingProcessOption(rows, []).series[0].data, [{ name: '未知工艺', value: 2 }])
  const ranking = weldingLengthOption(['设备0 / 0', '设备1 / 0'], [0, 5], '#123456')
  assert.deepEqual(ranking.yAxis.data, ['设备0 / 0', '设备1 / 0'])
  assert.deepEqual(ranking.series[0].data, [0, 5])
  assert.deepEqual(weldingMetricSeries({ daily: [{ production_date: '2026-10-27', job_count: 0 }, { production_date: '2026-10-26', job_count: 3 }] }, 'job_count'), [3, 0])
  assert.deepEqual(weldingMetricSeries({ daily: [{ production_date: '2026-10-27' }] }, 'working_hours'), [null])
})
