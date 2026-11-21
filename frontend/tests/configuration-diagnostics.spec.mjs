// @vitest-environment jsdom

import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { afterEach, expect, test, vi } from 'vitest'
import AppSelect from '../src/components/AppSelect.vue'
import ConfigurationDiagnosticsView from '../src/views/ConfigurationDiagnosticsView.vue'
import { stubTableLayout } from './table-layout-fixture.mjs'

const categoryNames = ['数据库连接配置', 'AI 模型配置']
const item = (key, category, status = 'error') => ({
  key, category, status, saved_value: false, saved_source: 'settings.env', valid: false, process_mismatch: true,
  rule: '需要重启', processes: { web: { status, source: 'Web快照', value: 0 } },
})
const items = [
  item('SQLSERVER_HOST', categoryNames[0], 'pending'),
  item('LLM_MAX_CONCURRENT', categoryNames[1]),
  item('LLM_ENABLED', categoryNames[1], 'effective'),
]
const report = (items, processes = {}) => Response.json({ success: true, data: {
  items, processes, categories: categoryNames, errors: [],
  notes: ['内部说明不显示'], precedence: { private: '内部优先级不显示' },
} })

enableAutoUnmount(afterEach)

function open(read) {
  const requests = []
  stubTableLayout()
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = new URL(String(input), 'http://localhost')
    if (url.pathname.endsWith('/cancel') || url.pathname === '/api/v1/operations/browser') return Response.json({})
    expect(url.pathname).toBe('/api/v1/configuration/diagnostics')
    expect(init.method ?? 'GET').toBe('GET')
    expect(init.signal).toBeInstanceOf(AbortSignal)
    requests.push(init.signal)
    return read()
  })
  const wrapper = mount(ConfigurationDiagnosticsView)
  const button = label => wrapper.findAll('button').find(candidate => candidate.text() === label)
  return {
    wrapper, requests, button,
    filterCategory: async category => {
      wrapper.getComponent(AppSelect).vm.$emit('update:modelValue', category)
      await flushPromises()
    },
    filterName: value => wrapper.get('input[aria-label="配置项名称"]').setValue(value),
    keys: () => wrapper.findAll('tbody tr').flatMap(row => row.findAll('td:first-child strong').map(cell => cell.text())),
    page: () => wrapper.get('button[aria-current="page"]').attributes('aria-label'),
  }
}

test('configuration diagnostics filter only abnormal rows locally and show saved values and sources', async () => {
  const page = open(() => report(items))
  await nextTick()
  expect(page.wrapper.attributes('aria-busy')).toBe('true')
  expect(page.wrapper.get('[role="status"]').text()).toContain('正在读取配置快照')
  await flushPromises()
  expect(page.keys()).toEqual(['LLM_MAX_CONCURRENT'])
  const row = page.wrapper.get('tbody tr')
  expect(row.text()).toContain('关闭')
  expect(row.text()).toContain('0')
  expect(row.text()).toContain('来源：settings.env')
  expect(row.text()).toContain('来源：Web快照')
  expect(row.text()).toContain('后台进程与 Web 配置不一致')
  expect(page.wrapper.text()).not.toContain('内部说明不显示')
  await page.filterCategory(categoryNames[1])
  await page.filterName('  llm_max  ')
  expect(page.keys()).toEqual(['LLM_MAX_CONCURRENT'])
  await page.filterCategory(categoryNames[0])
  expect(page.keys()).toEqual([])
  expect(page.wrapper.text()).toContain('没有符合条件的异常配置项')
  expect(page.requests).toHaveLength(1)
})

test('each Consumer displays its own version and the unavailable instance keeps its own warning', async () => {
  const page = open(() => report(items, {
    web: { available: true, version: 'web-one' },
    scheduler: { available: true, version: 'scheduler-one' },
    automation: { available: true, version: 'automation-one' },
    consumers: {
      'consumer-1': { state: 'running', available: true, version: 'first-instance' },
      'consumer-2': { state: 'lost', available: false, version: 'second-instance' },
    },
  }))
  await flushPromises()
  const view = page.wrapper.text()
  expect(view).toContain('Consumer consumer-1 · running配置版本：first-instance')
  expect(view).toContain('Consumer consumer-2 · lost配置版本：second-instance')
  expect(view).toContain('此实例配置快照不可用')
  for (const version of ['web-one', 'scheduler-one', 'automation-one']) expect(view).toContain(version)
})

test('diagnostics page and filters keep a ten-row limit and refresh resets to the first page', { timeout: 15_000 }, async () => {
  const candidates = Array.from({ length: 25 }, (_, index) => item(`ERROR_${index}`, categoryNames[index < 12 ? 1 : 0]))
  let rows = [...items.filter(row => row.status !== 'error'), item('OPTIONAL_KEY', categoryNames[0], 'unconfigured'), ...candidates]
  const page = open(() => report(rows))
  await flushPromises()
  expect(page.keys()).toHaveLength(10)
  await page.wrapper.get('button[aria-label="第 3 页"]').trigger('click')
  expect(page.keys()).toEqual(['ERROR_20', 'ERROR_21', 'ERROR_22', 'ERROR_23', 'ERROR_24'])
  await page.filterCategory(categoryNames[1])
  expect(page.page()).toBe('第 1 页')
  await page.wrapper.get('button[aria-label="第 2 页"]').trigger('click')
  await page.filterName('ERROR_1')
  expect(page.keys()).toEqual(['ERROR_1', 'ERROR_10', 'ERROR_11'])
  expect(page.page()).toBe('第 1 页')
  await page.filterCategory('')
  await page.filterName('')
  await page.wrapper.get('button[aria-label="第 2 页"]').trigger('click')
  rows = candidates
  await page.button('刷新快照').trigger('click')
  await flushPromises()
  expect(page.page()).toBe('第 1 页')
  rows = []
  await page.button('刷新快照').trigger('click')
  await flushPromises()
  expect(page.keys()).toEqual([])
  expect(page.wrapper.text()).toContain('暂无异常配置项')
})

test('diagnostics errors hide raw data, preserve successful rows and allow retry', async () => {
  let offline = false
  const page = open(() => offline ? Response.json({ detail: 'secret-credentials' }, { status: 502 }) : report(items))
  await flushPromises()
  offline = true
  await page.button('刷新快照').trigger('click')
  await flushPromises()
  expect(page.wrapper.get('[role="alert"]').text()).toContain('配置诊断读取失败')
  expect(page.wrapper.text()).not.toContain('secret-credentials')
  expect(page.keys()).toEqual(['LLM_MAX_CONCURRENT'])
  offline = false
  await page.button('重试').trigger('click')
  await flushPromises()
  expect(page.requests).toHaveLength(3)
  expect(page.wrapper.text()).not.toContain('配置诊断读取失败')
})

test('a pending refresh locks repeated requests and aborts cleanly when leaving', async () => {
  let finish
  let pending = false
  const page = open(() => pending ? new Promise(resolve => { finish = resolve }) : report(items))
  await flushPromises()
  pending = true
  await page.button('刷新快照').trigger('click')
  await page.button('刷新快照').trigger('click')
  expect(page.requests).toHaveLength(2)
  expect(page.keys()).toEqual(['LLM_MAX_CONCURRENT'])
  page.wrapper.unmount()
  expect(page.requests[1].aborted).toBe(true)
  finish(report([item('LATE', categoryNames[0])]))
  await flushPromises()
})
