// @vitest-environment jsdom

import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { NSelect } from 'naive-ui'
import { createPinia } from 'pinia'
import { afterEach, expect, test, vi } from 'vitest'
import PdmStatisticsView from '../src/views/PdmStatisticsView.vue'
import { reportCache } from '../src/api/reportCache'
import type { PdmStatisticsQuery } from '../src/api/generated/types.gen'

enableAutoUnmount(afterEach)
afterEach(() => {
  vi.useRealTimers()
  reportCache.clear()
  localStorage.clear()
})

function report(body: PdmStatisticsQuery, total: number, refreshing = false) {
  const totals = { total, completed: 2, on_time: 1, total_rate: 50, on_time_rate: 25 }
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
    global: { plugins: [createPinia()], stubs: { teleport: true, PdmCompletionChart: true } },
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
  expect(wrapper.text()).toContain('99')
  await vi.advanceTimersByTimeAsync(5000)
  await flushPromises()
  expect(requests.filter(request => request.document_kinds.length === 2)).toHaveLength(2)
  expect(wrapper.text()).toContain('99')
  await launch.trigger('click')
  await flushPromises()
  const reopened = wrapper.get('form[aria-label="PDM资料筛选"]')
  expect(reopened.text()).toContain('一号项目')
  expect(reopened.text()).toContain('船体')
  expect(reopened.text()).not.toContain('负责人甲')
  const cancel = reopened.findAll('button').find(button => button.text() === '取消')
  if (!cancel) throw new Error('Cancel filter button is missing')
  await cancel.trigger('click')
  expect(wrapper.text()).toContain('99')
  wrapper.unmount()
  const calls = fetch.mock.calls.length
  await vi.advanceTimersByTimeAsync(5000)
  expect(fetch.mock.calls).toHaveLength(calls)
})
