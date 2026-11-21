// @vitest-environment jsdom

import { enableAutoUnmount, mount } from '@vue/test-utils'
import { afterEach, expect, test, vi } from 'vitest'
import ReportTable from '../src/components/ReportTable.vue'
import ReportTrendPanel from '../src/components/ReportTrendPanel.vue'

enableAutoUnmount(afterEach)

test('history distinguishes loading from a confirmed empty result', async () => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  const wrapper = mount(ReportTable, { props: { loading: true } })
  expect(wrapper.text()).toContain('正在加载…')
  expect(wrapper.text()).not.toContain('暂无历史报告')
  await wrapper.setProps({ loading: false })
  expect(wrapper.text()).toContain('暂无历史报告')
  expect(wrapper.text()).not.toContain('正在加载…')
})

test('trend panels announce loading, empty and failure and provide a retry action', async () => {
  const wrapper = mount(ReportTrendPanel, { props: { title: '生产趋势', loading: true } })
  expect(wrapper.get('[role="status"]').text()).toContain('正在读取报表数据')
  expect(wrapper.find('button').exists()).toBe(false)
  await wrapper.setProps({ loading: false, empty: true })
  expect(wrapper.get('[role="status"]').text()).toContain('当前条件下暂无数据')
  await wrapper.setProps({ empty: false, error: '源数据库不可用' })
  const alert = wrapper.get('[role="alert"]')
  expect(alert.text()).toContain('源数据库不可用')
  const button = alert.get('button')
  expect(button.text()).toBe('重新查询')
  await button.trigger('click')
  expect(wrapper.emitted('retry')).toEqual([[]])
})

test('refreshing retains the report and chart-only mode removes the duplicate table', async () => {
  const wrapper = mount(ReportTrendPanel, {
    props: { title: '生产趋势', loading: true, hasData: true, chartLabel: '每日产量' },
    slots: { chart: () => '现有图表', table: () => '现有明细' },
  })
  expect(wrapper.get('[role="status"]').text()).toContain('正在更新')
  expect(wrapper.get('[role="img"][aria-label="每日产量"]').text()).toContain('现有图表')
  expect(wrapper.text()).toContain('现有明细')
  expect(wrapper.text()).not.toContain('正在读取报表数据')
  await wrapper.setProps({ showTable: false })
  expect(wrapper.text()).not.toContain('现有明细')
  expect(wrapper.text()).toContain('现有图表')
})
