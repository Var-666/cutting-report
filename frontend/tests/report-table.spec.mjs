// @vitest-environment jsdom

import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { NDataTable, NPagination } from 'naive-ui'
import { afterEach, expect, test, vi } from 'vitest'
import { h } from 'vue'
import AppReportTable from '../src/components/AppReportTable.vue'
import WeldingTable from '../src/components/WeldingTable.vue'

enableAutoUnmount(afterEach)

const columns = [
  { key: 'c0', title: '任务', width: 160, align: 'left' },
  { key: 'c1', title: '长度', width: 120, align: 'right' },
]
const rowKey = row => row.id

test('the table renders escaped values, an operable header, summary and empty rows', async () => {
  const wrapper = mount(AppReportTable, {
    props: { columns, rowKey, autoFit: false, paginate: false, summary: true,
      items: [{ id: 1, program: '<script>bad</script>', length: 12 }] },
    slots: {
      'header-c0': () => h('input', { type: 'checkbox', 'aria-label': '选择当前页' }),
      c0: ({ row }) => row.program, c1: ({ row }) => String(row.length),
      'summary-c0': () => '汇总', 'summary-c1': () => '12', empty: () => '当前无任务',
    },
  })
  expect(wrapper.get('input[aria-label="选择当前页"]').element.type).toBe('checkbox')
  await wrapper.get('input').setValue(true)
  expect(wrapper.get('input').element.checked).toBe(true)
  expect(wrapper.get('tbody').text()).toContain('<script>bad</script>')
  expect(wrapper.find('script').exists()).toBe(false)
  expect(wrapper.get('tbody').text()).toContain('汇总')
  expect(wrapper.getComponent(NDataTable).props('columns').map(column => column.titleAlign)).toEqual(['left', 'right'])
  await wrapper.setProps({ items: [] })
  expect(wrapper.text()).toContain('当前无任务')
})

test('one accessible pager forwards page changes using ten rows and locks during reads', async () => {
  const wrapper = mount(AppReportTable, {
    props: { columns, rowKey, autoFit: false, total: 100, page: 2 },
  })
  expect(wrapper.findAllComponents(NPagination)).toHaveLength(1)
  expect(wrapper.get('button[aria-current="page"]').attributes('aria-label')).toBe('第 2 页')
  expect(wrapper.getComponent(NPagination).props('pageSize')).toBe(10)
  expect(wrapper.getComponent(NPagination).props('showSizePicker')).toBe(false)
  await wrapper.get('button[aria-label="第 3 页"]').trigger('click')
  expect(wrapper.emitted('page')).toEqual([[3]])
  await wrapper.setProps({ loading: true })
  expect(wrapper.attributes('aria-busy')).toBe('true')
  expect(wrapper.get('button[aria-label="第 3 页"]').element.disabled).toBe(true)
  await wrapper.get('button[aria-label="第 3 页"]').trigger('click')
  expect(wrapper.emitted('page')).toEqual([[3]])
})

test('explicit column widths and height controls survive empty and long-valued pages', async () => {
  const fixed = [{ key: 'c0', title: '姓名', width: 96, align: 'left' },
    { key: 'c1', title: '文件名', minWidth: 220, align: 'left' },
    { key: 'c2', title: '状态', width: 104, align: 'center' }]
  const wrapper = mount(AppReportTable, {
    props: { columns: fixed, rowKey, autoFit: false, paginate: false },
    slots: { c1: ({ row }) => row.file },
  })
  const table = wrapper.getComponent(NDataTable)
  for (const items of [[], [{ id: 1, file: 'a.nc' }], [{ id: 2, file: '文件'.repeat(100) }]]) {
    await wrapper.setProps({ items })
    expect(table.props('tableLayout')).toBe('fixed')
    expect(table.props('scrollX')).toBe(420)
    expect(table.props('columns').map(column => [column.width, column.minWidth, column.titleAlign]))
      .toEqual([[96, undefined, 'left'], [undefined, 220, 'left'], [104, undefined, 'center']])
  }
  expect(table.props('maxHeight')).toBe(540)
  await wrapper.setProps({ maxHeight: 330 })
  expect(table.props('maxHeight')).toBe(330)
  await wrapper.setProps({ maxHeight: null })
  expect(table.props('maxHeight')).toBeUndefined()
  await wrapper.setProps({ items: [], flexHeight: true })
  expect(table.props('flexHeight')).toBe(true)
  expect(table.props('maxHeight')).toBeUndefined()
})

test('fitting caps text, reserves controls and preserves user resizing across updates', async () => {
  let observer
  let availableWidth = 0
  vi.stubGlobal('ResizeObserver', class {
    disconnect = vi.fn()
    constructor(callback) { this.notify = callback }
    observe(element) { if (element.getAttribute('role') === 'group') observer = this }
  })
  vi.spyOn(Element.prototype, 'clientWidth', 'get').mockImplementation(() => availableWidth)
  const originalStyle = window.getComputedStyle
  vi.spyOn(window, 'getComputedStyle').mockImplementation((element, ...args) => {
    const style = originalStyle(element, ...args)
    if (!element.matches('th,td')) return style
    return Object.assign(style, { paddingLeft: '14px', paddingRight: '14px' })
  })
  let measures = new Map([['完整文件名', 900], ['完整汇总内容', 320], ['设置并派发', 520], ['故障详情'.repeat(150), 2000]])
  const originalRect = Element.prototype.getBoundingClientRect
  vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function () {
    const rect = originalRect.call(this)
    const text = this.textContent?.trim()
    return text && this.matches('span') ? { ...rect, width: measures.get(text) ?? 0 } : rect
  })
  const send = vi.fn()
  const fixed = [{ key: 'c0', title: '文件名', width: 1000 },
    { key: 'c1', title: '汇总', width: 600, resizable: false },
    { key: 'c2', title: '操作', ellipsis: false }]
  const wrapper = mount(AppReportTable, {
    props: { columns: fixed, rowKey, paginate: false, summary: true,
      items: [{ id: 1 }, { id: 2, warning: true }] },
    slots: {
      c0: () => h('strong', '完整文件名'), c1: () => '',
      c2: ({ row }) => row.warning ? '故障详情'.repeat(150) : h('button', { onClick: send }, '设置并派发'),
      'summary-c1': () => '完整汇总内容',
    },
  })
  const table = wrapper.getComponent(NDataTable)
  const widths = () => table.props('columns').map(column => column.width)
  await flushPromises()
  expect(widths()).toEqual([360, 350, 550])
  expect(table.props('scrollX')).toBe(1260)
  const [file, summary, actions] = table.props('columns')
  expect(file).toMatchObject({ resizable: true, ellipsis: { tooltip: expect.any(Object) } })
  expect(summary.resizable).toBe(false)
  expect(actions).toMatchObject({ resizable: true, ellipsis: false, minWidth: 550 })
  expect(fixed[0].resizable).toBeUndefined()
  expect(wrapper.findAll('[title]').some(item => item.attributes('title') === '故障详情'.repeat(150))).toBe(true)
  await wrapper.get('button').trigger('click')
  expect(send).toHaveBeenCalledOnce()

  // Drive the public Naive table resize event with deterministic jsdom geometry.
  table.vm.$emit('unstable-column-resize', 630, 630, file)
  measures = new Map([['完整文件名', 20], ['完整汇总内容', 40], ['设置并派发', 30]])
  await wrapper.setProps({ items: [{ id: 3 }] })
  await flushPromises()
  expect(widths()).toEqual([630, 70, 60])
  await wrapper.setProps({ loading: true })
  measures.clear()
  observer.notify()
  await flushPromises()
  expect(widths()).toEqual([630, 70, 60])

  measures = new Map([['文件名', 32], ['汇总', 24], ['操作', 24]])
  await wrapper.setProps({ items: [], loading: false })
  await flushPromises()
  expect(widths()).toEqual([630, 54, 54])
  table.vm.$emit('unstable-column-resize', 180, 180, table.props('columns')[2])
  availableWidth = 1202
  observer.notify()
  await flushPromises()
  expect(widths()).toEqual([630, 390, 180])
  expect(table.props('scrollX')).toBe(1200)
  wrapper.unmount()
  expect(observer.disconnect).toHaveBeenCalledOnce()
})

test.each([
  ['daily', { production_date: '2026-10-01' }, '2026-10-01', null],
  ['equipment', { device_no: 0, robot_no: null }, '0:null', null],
  ['jobs', { id: 1, serial_number: 'S-1', weld_type_label: '平焊' }, 1, '作业详情'],
  ['parts', { id: 2, project_no: null }, 2, '工件与焊缝'],
  ['seams', { id: 3, robot_no: null }, 3, null],
])('%s shows the right columns and opens details only for supported rows', async (kind, row, key, detailLabel) => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  const wrapper = mount(WeldingTable, { props: {
    kind, items: detailLabel ? [row, { production_date: '2026-10-02' }] : [row], total: 21,
  } })
  const table = wrapper.getComponent(NDataTable)
  expect(table.props('rowKey')(row)).toBe(key)
  expect(table.props('maxHeight')).toBeUndefined()
  expect(wrapper.getComponent(NPagination).props('pageSize')).toBe(10)
  expect(wrapper.getComponent(NPagination).props('showSizePicker')).toBe(false)
  for (const column of table.props('columns')) {
    expect(column.titleAlign).toBe(column.align || 'left')
    if (/length|hours|count|output|speed|duration|seconds|consumption|rate/.test(column.key)) expect(column.align).toBe('right')
    if (['state_label', 'weld_type_label', 'actions'].includes(column.key)) expect(column.align).toBe('center')
    if (['robot_no', 'welding_seam_no', 'serial_number', 'id'].includes(column.key)) expect(column.align).toBe('left')
  }
  const details = wrapper.findAll('tbody button').filter(button => button.text() === detailLabel)
  expect(details).toHaveLength(detailLabel ? 1 : 0)
  if (detailLabel) {
    await details[0].trigger('click')
    expect(wrapper.emitted('detail')).toEqual([[row]])
  } else {
    expect(table.props('columns').some(column => column.key === 'actions')).toBe(false)
  }
})
