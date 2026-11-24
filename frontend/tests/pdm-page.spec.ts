// @vitest-environment jsdom

import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { NConfigProvider, NSelect } from 'naive-ui'
import { createPinia } from 'pinia'
import { h } from 'vue'
import { afterEach, expect, test, vi } from 'vitest'
import WeldingChart from '../src/components/WeldingChart.vue'
import PdmCompletionChart from '../src/components/pdm/PdmCompletionChart.vue'
import PdmChartTable from '../src/components/pdm/PdmChartTable.vue'
import PdmStatisticsView from '../src/views/PdmStatisticsView.vue'
import { reportCache } from '../src/api/reportCache'
import { createPdmFilters } from '../src/utils/pdmFilters'
import type { PdmStatisticsQuery, PdmSummaryTable } from '../src/api/generated/types.gen'

enableAutoUnmount(afterEach)
afterEach(() => {
  vi.useRealTimers()
  reportCache.clear()
  localStorage.clear()
})

function report(body: PdmStatisticsQuery, total: number, refreshing = false) {
  const totals = { total, completed: 2, on_time: 1, total_rate: 200 / total, on_time_rate: 100 / total }
  return {
    start_date: body.start_date, end_date: body.end_date, group_by: 'summary',
    summaries: { equipment: totals, drawing: totals },
    tables: [{
      key: 'equipment:summary', kind: 'equipment', title: '设备资料计划完成汇总表', scope_label: '所有项目',
      rows: [{ category: '技术协议-按设备计', group_id: '', group_name: '', subtotal: false, ...totals }], totals,
    }],
    meta: { report_data: { refreshing } },
  }
}

test('PDM submits selected project, profession and owner, then keeps the result through background refresh', async () => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const requests: PdmStatisticsQuery[] = []
  let fail = true
  const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input)
    if (url.includes('/cancel')) return Response.json({})
    if (url.includes('/overview')) return Response.json({ success: true, data: { equipment: {}, drawing: {} } })
    if (url.includes('/options')) {
      return Response.json({ success: true, data: {
        projects: [{ value: 'p1', label: '一号项目' }],
        professions: [{ value: 'f1', label: '船体' }],
        responsible_people: [{ value: 'o1', label: '负责人甲' }, { value: 'o2', label: '负责人乙' }],
      } })
    }
    if (!url.includes('/report') || typeof init?.body !== 'string') throw new Error(`Unexpected request ${url}`)
    const body: PdmStatisticsQuery = JSON.parse(init.body)
    requests.push(body)
    if (body.document_kinds.length === 2) return Response.json({ success: true, data: report(body, 4, true) })
    if (fail) return Response.json({ detail: 'PDM 源暂不可用' }, { status: 502 })
    return Response.json({ success: true, data: report(body, 99) })
  })
  const wrapper = mount(PdmStatisticsView, {
    attachTo: document.body,
    global: { plugins: [createPinia()], stubs: { teleport: true, PdmCompletionChart: true, WeldingChart: true } },
  })
  expect(wrapper.text()).toContain('正在读取当月统计数据')
  await flushPromises()
  const launch = wrapper.findAll('button').find(button => button.attributes('aria-label')?.startsWith('筛选设备资料'))
  if (!launch) throw new Error('Equipment filter button is missing')
  await launch.trigger('click')
  await flushPromises()
  const filter = wrapper.get('form[aria-label="PDM资料筛选"]')
  async function select(label: string, value: string) {
    const select = wrapper.findAllComponents(NSelect).find(control => control.props('inputProps')?.['aria-label'] === label)
    if (!select) throw new Error(`Filter ${label} is missing`)
    select.vm.$emit('update:value', [value])
    await flushPromises()
  }
  await select('设备资料项目', 'p1')
  await select('设备资料专业', 'f1')
  await select('设备资料负责人', 'o1')
  await filter.trigger('submit')
  await flushPromises()
  expect(wrapper.text()).toContain('PDM 源暂不可用')
  expect(requests.at(-1)).toMatchObject({
    document_kinds: ['equipment'],
    equipment: { projects: ['p1'], professions: ['f1'], responsible_people: ['o1'] },
  })

  fail = false
  await wrapper.get('form[aria-label="PDM资料筛选"]').trigger('submit')
  await flushPromises()
  expect(requests.filter(request => request.document_kinds.length === 1)).toHaveLength(2)
  expect(wrapper.text()).not.toContain('PDM 源暂不可用')
  expect(wrapper.text()).toContain('2.02%')
  await vi.advanceTimersByTimeAsync(5000)
  await flushPromises()
  expect(requests.filter(request => request.document_kinds.length === 2)).toHaveLength(2)
  expect(wrapper.text()).toContain('2.02%')
  await launch.trigger('click')
  await flushPromises()
  const reopened = wrapper.get('form[aria-label="PDM资料筛选"]')
  expect(reopened.text()).toContain('一号项目')
  expect(reopened.text()).toContain('船体')
  expect(reopened.text()).not.toContain('负责人甲')
  const cancel = reopened.findAll('button').find(button => button.text() === '取消')
  if (!cancel) throw new Error('Cancel filter button is missing')
  await cancel.trigger('click')
  expect(wrapper.text()).toContain('2.02%')
  wrapper.unmount()
  const calls = fetch.mock.calls.length
  await vi.advanceTimersByTimeAsync(5000)
  expect(fetch.mock.calls).toHaveLength(calls)
})


test('project details support multiple selections and default charts to the first five', async () => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  const totals = { total: 24, completed: 18, on_time: 12, total_rate: 75, on_time_rate: 50 }
  const workTotals = { total: 6, completed: 3, on_time: 1, total_rate: 50, on_time_rate: 16.67 }
  const table: PdmSummaryTable = {
    key: 'equipment:all:project', kind: 'equipment', title: '设备资料计划完成汇总表',
    scope_label: '所有项目－分项目', group_label: '项目',
    totals: { total: 30, completed: 21, on_time: 13, total_rate: 70, on_time_rate: 43.33 },
    rows: [
      ...Array.from({ length: 12 }, (_, index) => ({
        category: '设备认可图', group_id: `p${index}`, group_name: `项目${index + 1}`, subtotal: false,
        total: 2, completed: index % 2 + 1, on_time: 1, total_rate: index % 2 ? 100 : 50, on_time_rate: 50,
      })),
      { category: '设备认可图', group_name: '总计', subtotal: true, ...totals },
      { category: '设备工作资料', group_id: 'p0', group_name: '项目1', subtotal: false, total: 1, completed: 1, on_time: 0, total_rate: 100, on_time_rate: 0 },
      { category: '设备工作资料', group_id: 'p11', group_name: '项目12', subtotal: false, ...workTotals, total: 3, completed: 1, total_rate: 33.33, on_time_rate: 33.33 },
      { category: '设备工作资料', group_name: '总计', subtotal: true, ...workTotals },
    ],
  }
  const wrapper = mount(PdmChartTable, {
    props: { panel: { table, filters: createPdmFilters() }, loading: false },
    attachTo: document.body,
    global: { plugins: [createPinia()], stubs: { teleport: true, WeldingChart: true } },
  })
  const pies = wrapper.findAll('.pdm-completion-pies').flatMap(pie => pie.findAllComponents(WeldingChart))
  expect(pies.map(pie => pie.props('label'))).toEqual([
    '按时完成率 50.00%，按项目分布', '总完成率 70.00%，按项目分布',
    '按时完成率 16.67%，按项目分布', '总完成率 50.00%，按项目分布',
  ])
  expect(pies[1].props('option').series).toMatchObject([{ data: [
    { name: '项目1', value: 1 }, { name: '项目2', value: 2 }, { name: '项目3', value: 1 },
    { name: '项目4', value: 2 }, { name: '项目5', value: 1 },
  ] }])
  expect(pies[1].props('option').tooltip?.valueFormatter?.(2, 1)).toBe('2 份（28.57%）')
  expect(pies[3].props('option').series).toMatchObject([{
    type: 'pie', data: [{ name: '项目1', value: 1 }, { name: '项目12', value: 1 }, { name: '未分配', value: 1 }],
  }])
  const charts = wrapper.findAll('section[aria-label$="图表"]')
  expect(charts.map(chart => chart.get('h5').text())).toEqual(['设备认可图', '设备工作资料'])
  expect(charts.map(chart => chart.findAllComponents(WeldingChart).length)).toEqual([3, 3])
  expect(charts[0].find('.pdm-completion-pies__legend').exists()).toBe(false)
  const bar = charts[0].getComponent(PdmCompletionChart)
  expect(bar.getComponent(WeldingChart).props('option').xAxis).toMatchObject({ data: ['项目1', '项目2', '项目3', '项目4', '项目5'] })
  expect(wrapper.find('input[type="range"]').exists()).toBe(false)
  expect(wrapper.find('table').exists()).toBe(false)
  await bar.trigger('click')
  const dialog = wrapper.get('[aria-label="所有项目－分项目 · 设备认可图 · 完整明细"]')
  const details = dialog.get('[aria-label="所有项目－分项目表格滚动区域"]')
  expect(details.classes()).toContain('pdm-summary-wrap--projects')
  expect(details.text()).toContain('项目12')
  expect(details.text()).toContain('总计')
  expect(details.text()).not.toContain('设备工作资料')
  const checkboxes = details.findAll('input[type="checkbox"]')
  expect(checkboxes).toHaveLength(12)
  await checkboxes[0].setValue(true)
  await checkboxes[11].setValue(true)
  expect(details.findAll('input:checked').map(input => input.attributes('aria-label'))).toEqual([
    '选择项目1（设备认可图）', '选择项目12（设备认可图）',
  ])
  expect(dialog.get('h5').text()).toBe('已选 2 个项目')
  for (const chart of [dialog, ...charts]) {
    expect(chart.getComponent(PdmCompletionChart).getComponent(WeldingChart).props('option').xAxis)
      .toMatchObject({ data: ['项目1', '项目12'] })
  }
  expect(pies[1].props('label')).toContain('70.00%')
  await charts[1].getComponent(PdmCompletionChart).trigger('click')
  const nextDialog = wrapper.get('[aria-label="所有项目－分项目 · 设备工作资料 · 完整明细"]')
  const checked = nextDialog.findAll('input:checked')
  expect(checked.map(input => input.attributes('aria-label'))).toEqual(['选择项目1（设备工作资料）', '选择项目12（设备工作资料）'])
  await checked[0].setValue(false)
  expect(bar.getComponent(WeldingChart).props('option').xAxis).toMatchObject({ data: ['项目12'] })
  await checked[1].setValue(false)
  expect(bar.getComponent(WeldingChart).props('option').xAxis).toMatchObject({ data: ['项目1', '项目2', '项目3', '项目4', '项目5'] })

  await wrapper.setProps({ panel: { table: { ...table, key: 'equipment:profession:船体' }, filters: createPdmFilters() } })
  expect(wrapper.findAll('input[type="checkbox"]')).toHaveLength(0)
  const regrouped = wrapper.get('section[aria-label="设备认可图图表"]').getComponent(PdmCompletionChart)
  expect(regrouped.getComponent(WeldingChart).props('option').xAxis).toMatchObject({
    data: Array.from({ length: 5 }, (_, index) => `项目${index + 1}`),
  })
})


test('summary bar opens all category rows while pie tooltips show counts and shares', async () => {
  const totals = { total: 14, completed: 11, on_time: 5, total_rate: 78.57, on_time_rate: 35.71 }
  const table: PdmSummaryTable = {
    key: 'drawing:summary', kind: 'drawing', title: '详设图纸计划完成汇总表', scope_label: '所有项目',
    group_label: null, totals,
    rows: [
      { category: '预审图', subtotal: false, total: 10, completed: 8, on_time: 3, total_rate: 80, on_time_rate: 30 },
      { category: '送审图', subtotal: false, total: 0, completed: 0, on_time: 0, total_rate: null, on_time_rate: null },
      { category: '完工图', subtotal: false, total: 4, completed: 3, on_time: 2, total_rate: 75, on_time_rate: 50 },
    ],
  }
  const wrapper = mount(NConfigProvider, {
    props: { themeOverrides: { common: { primaryColor: '#3529b3' } } },
    slots: { default: () => h(PdmChartTable, { panel: { table, filters: createPdmFilters() }, loading: false }) },
    attachTo: document.body,
    global: { plugins: [createPinia()], stubs: { teleport: true, WeldingChart: true } },
  })
  const pies = wrapper.findAll('.pdm-completion-pies').flatMap(pie => pie.findAllComponents(WeldingChart))
  expect(pies.map(pie => pie.props('label'))).toEqual([
    '按时完成率 35.71%，按资料类型分布', '总完成率 78.57%，按资料类型分布',
  ])
  expect(pies[0].props('option').tooltip?.valueFormatter?.(3, 0)).toBe('3 份（60.00%）')
  expect(pies[1].props('option').tooltip?.valueFormatter?.(8, 0)).toBe('8 份（72.73%）')
  for (const [index, values] of [[3, 0, 2], [8, 0, 3]].entries()) {
    const pie = pies[index].props('option')
    expect(pie.tooltip).toMatchObject({ trigger: 'item', renderMode: 'html', appendTo: 'body' })
    expect(pie.series).toMatchObject([{
      type: 'pie', label: { show: false }, labelLine: { show: false },
      data: table.rows.flatMap((row, i) => values[i] ? [{ name: row.category, value: values[i] }] : []),
    }])
  }
  for (const pie of pies) {
    expect(pie.props('option').series).toMatchObject([{ data: [
      { itemStyle: { color: '#3529b3' } }, {},
    ] }])
  }
  await wrapper.setProps({ themeOverrides: { common: { primaryColor: '#faad14' } } })
  for (const pie of pies) {
    expect(pie.props('option').series).toMatchObject([{ data: [
      { itemStyle: { color: '#faad14' } }, {},
    ] }])
  }
  await pies[1].trigger('click')
  expect(wrapper.find('table').exists()).toBe(false)
  expect(wrapper.find('.pdm-completion-pies button').exists()).toBe(false)

  await wrapper.getComponent(PdmCompletionChart).trigger('keydown', { key: 'Enter' })
  const details = wrapper.get('[aria-label="所有项目 · 全部明细"] table')
  expect(details.findAll('tbody th[scope="rowgroup"]').map(cell => cell.text())).toEqual(['预审图', '送审图', '完工图'])
  expect(details.get('tbody').text()).toContain('—')
  expect(details.get('tfoot').text()).toContain('78.57%')
})
