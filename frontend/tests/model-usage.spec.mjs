// @vitest-environment jsdom

import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { init } from 'echarts/core'
import { NConfigProvider, NDialogProvider, NSelect, darkTheme } from 'naive-ui'
import { createPinia, disposePinia } from 'pinia'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { h, onUnmounted } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import AppDatePicker from '../src/components/AppDatePicker.vue'
import AppSelect from '../src/components/AppSelect.vue'
import ModelConfiguration from '../src/components/ModelConfiguration.vue'
import { useThemeStore } from '../src/store/modules/theme'
import ModelUsageView from '../src/views/ModelUsageView.vue'
import { stubTableLayout } from './table-layout-fixture.mjs'

vi.mock('echarts/core', async importOriginal => ({
  ...await importOriginal(), init: vi.fn(() => ({ setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() })),
}))
vi.mock('../src/api/reportDates', async importOriginal => ({
  ...await importOriginal(), reportToday: () => '2026-10-12',
}))

const configurationPath = '/api/v1/configuration/llm'
const usagePath = '/api/v1/llm-usage/report'
const config = { enabled: true, base_url: 'https://provider.invalid/v1', model: 'model-a', active_enabled: true,
  active_base_url: 'https://provider.invalid/v1', active_model: 'model-a', api_key_configured: true, restart_required: false }
const response = data => Response.json({ success: true, data })
const result = (count = 21) => ({
  summary: { call_count: count, metered_count: count - 1, failed_count: 0, total_tokens: 0 }, scene: '',
  start_date: '2026-10-01', end_date: '2026-10-12', granularity: 'day', timezone: 'Asia/Shanghai', breakdown: [],
  series: [{ period: '2026-10-12', call_count: count, total_tokens: 0, input_tokens: 0, output_tokens: 0 }],
})

enableAutoUnmount(afterEach)
beforeEach(() => { vi.mocked(init).mockClear() })

async function open({ component = ModelUsageView, fetchResponse, observe = false } = {}) {
  const requests = []
  const observers = []
  stubTableLayout()
  if (observe) vi.stubGlobal('ResizeObserver', class {
    disconnect = vi.fn()
    observe = vi.fn()
    constructor() { observers.push(this) }
  })
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, options) => {
    const { pathname, searchParams } = new URL(String(input), 'http://localhost')
    if (pathname.endsWith('/cancel') || pathname === '/api/v1/operations/browser') return Response.json({})
    expect(options.credentials).toBe('same-origin')
    const call = { pathname, method: options.method ?? 'GET', params: Object.fromEntries(searchParams), signal: options.signal,
      body: options.body && JSON.parse(options.body) }
    requests.push(call)
    const custom = fetchResponse?.(call)
    if (custom) return custom
    if (pathname === configurationPath && call.method === 'GET') return response({ ...config, api_key_configured: false })
    throw new Error(`Unexpected request ${call.method} ${pathname}`)
  })
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/settings/model-usage', component }] })
  await router.push('/settings/model-usage')
  const pinia = createPinia()
  const theme = useThemeStore(pinia)
  theme.setThemeScheme('light')
  const wrapper = mount({
    setup() { onUnmounted(() => disposePinia(pinia)) },
    render: () => h(NConfigProvider, { theme: theme.darkMode ? darkTheme : null, themeOverrides: theme.naiveTheme },
      { default: () => h(NDialogProvider, {}, { default: () => h(RouterView) }) }),
  }, { global: { plugins: [router, pinia], stubs: { teleport: true } } })
  await flushPromises()
  return { wrapper, requests, theme, observers,
    usage: () => requests.filter(call => call.pathname === usagePath),
    async click(label) {
      await wrapper.findAll('button').find(button => button.text() === label).trigger('click')
      await flushPromises()
    },
    async setStart(date) {
      wrapper.findAllComponents(AppDatePicker)[0].vm.$emit('update:modelValue', date)
      await flushPromises()
    },
    async query() {
      await wrapper.get('[aria-label="用量查询条件"] form').trigger('submit')
      await flushPromises()
    },
  }
}

test('model configuration reads the saved catalog without sending an API key and applies the new model immediately', async () => {
  let failCatalog = false
  const page = await open({ component: ModelConfiguration, fetchResponse: call => {
    if (call.pathname.endsWith('/models')) return failCatalog
      ? Response.json({ detail: '读取模型列表认证失败' }, { status: 403 })
      : response({ models: ['model-a', 'model-b'] })
    return response(call.method === 'PUT' ? { ...config, model: call.body.model, active_model: call.body.model } : config)
  } })
  const model = page.wrapper.getComponent(NSelect)
  expect(model.props('options')).toEqual([{ label: 'model-a', value: 'model-a' }, { label: 'model-b', value: 'model-b' }])
  const catalog = () => page.requests.filter(call => call.pathname.endsWith('/models'))
  expect(catalog()[0].body).toEqual({ base_url: config.base_url })
  await page.click('刷新模型列表')
  expect(catalog().map(call => call.body)).toEqual(Array(2).fill({ base_url: config.base_url }))
  model.vm.$emit('update:value', 'model-b')
  await page.wrapper.get('form').trigger('submit')
  await flushPromises()
  expect(page.requests.at(-1)).toMatchObject({ method: 'PUT', body: { model: 'model-b' } })
  expect(page.requests.at(-1).body).not.toHaveProperty('api_key')
  expect(page.wrapper.text()).toMatch(/当前 Web 模型\s*model-b/)
  expect(page.wrapper.text()).not.toContain('需重启 Web')
  failCatalog = true
  await page.click('刷新模型列表')
  expect(page.wrapper.text()).toContain('读取模型列表认证失败')
  expect(model.props('value')).toBe('model-b')
})

test.each([{ base_url: '', api_key_configured: true }, { base_url: config.base_url, api_key_configured: false }])(
  'model catalog waits for an endpoint and credentials (%j)', async configured => {
    const { wrapper, requests } = await open({ component: ModelConfiguration, fetchResponse: () => response({ ...config, ...configured }) })
    expect(requests.map(call => call.pathname)).toEqual([configurationPath])
    expect(wrapper.text()).toContain('智能分析模型')
  },
)

test('period and scene drafts apply only when submitted while granularity updates the successful scope', async () => {
  const page = await open({ fetchResponse: call => call.pathname === usagePath && response({ ...result(), granularity: call.params.granularity }) })
  page.wrapper.getComponent(AppSelect).vm.$emit('update:modelValue', 'alarm')
  await page.setStart('2026-09-01')
  await page.click('每月')
  expect(page.usage().at(-1).params).toMatchObject({ start_date: '2026-10-01', scene: '', granularity: 'month' })
  await page.query()
  expect(page.usage().at(-1).params).toEqual({ start_date: '2026-09-01', end_date: '2026-10-12', scene: 'alarm', granularity: 'month' })
  await page.click('每日')
  expect(page.usage().at(-1).params).toMatchObject({ start_date: '2026-09-01', scene: 'alarm', granularity: 'day' })
  expect(page.wrapper.text()).toMatch(/消耗趋势与周期汇总.*场景与模型分布/)
  expect(page.wrapper.text()).not.toContain('逐次调用明细')
})

test('invalid or oversized intervals show validation without sending another query', async () => {
  const page = await open({ fetchResponse: call => call.pathname === usagePath && response(result()) })
  await page.setStart('2026-11-01')
  await page.query()
  expect(page.wrapper.get('[role="alert"]').text()).toContain('有效')
  await page.setStart('2020-01-01')
  await page.query()
  expect(page.wrapper.get('[role="alert"]').text()).toContain('366')
  expect(page.usage()).toHaveLength(1)
})

test.each(['day', 'month'])('%s table and chart omit empty periods while distinguishing missing and zero tokens', async granularity => {
  const dates = granularity === 'day' ? ['2026-10-01', '2026-10-03', '2026-10-05', '2026-10-07'] : ['2026-07', '2026-08', '2026-09', '2026-10']
  const series = [
    { period: dates[0], before_tracking: true, call_count: null, total_tokens: null },
    { period: dates[1], call_count: 1, metered_count: 0, failed_count: 1, total_tokens: null },
    { period: dates[2], call_count: 0, total_tokens: 0 },
    { period: dates[3], call_count: 1, metered_count: 1, total_tokens: 0 },
  ]
  let empty = false
  const page = await open({ observe: true, fetchResponse: call => call.pathname === usagePath &&
    response({ ...result(2), granularity, series: empty ? [series[0], series[2]] : series }) })
  const chart = vi.mocked(init).mock.results[0].value
  const [option] = chart.setOption.mock.lastCall
  expect(option.xAxis.data).toEqual([dates[1], dates[3]].map(period => granularity === 'day' ? period.slice(5) : period))
  expect(option.series[0].data).toEqual([null, 0])
  const rows = page.wrapper.get('[aria-label="每日或每月 token 汇总"]').findAll('tbody tr')
  expect(rows.map(row => row.findAll('td')[4].text())).toEqual(['0', '—'])
  expect(rows[1].findAll('td')[6].text()).toBe('1')
  empty = true
  await page.query()
  expect(page.wrapper.text()).toContain('当前区间暂无用量记录')
  expect(page.wrapper.find('[aria-label="每日或每月 token 汇总"]').exists()).toBe(false)
  expect(page.wrapper.get('[role="img"][aria-label^="总 token"]').isVisible()).toBe(false)
  expect(chart.dispose).toHaveBeenCalledOnce()
  expect(page.observers.some(observer => observer.disconnect.mock.calls.length === 1)).toBe(true)
})

test('a superseded usage request cannot replace the current result and unmount aborts the active read', async () => {
  const pending = []
  const page = await open({ fetchResponse: call => call.pathname === usagePath && new Promise(resolve => pending.push({ ...call, resolve })) })
  await page.setStart('2026-09-01')
  await page.query()
  expect(pending[0].signal.aborted).toBe(true)
  pending[1].resolve(response(result(32)))
  await flushPromises()
  pending[0].resolve(response(result(11)))
  await flushPromises()
  const overview = page.wrapper.get('[aria-label="区间用量概览"]')
  expect(overview.text()).toContain('32')
  expect(overview.text()).not.toContain('11')
  await page.query()
  page.wrapper.unmount()
  expect(pending[2].signal.aborted).toBe(true)
  pending[2].resolve(response(result(11)))
  await flushPromises()
  expect(init).toHaveBeenCalledOnce()
})

test('query failure keeps displayed data and retry reloads the successful scope', async () => {
  let fail = false
  const page = await open({ fetchResponse: call => call.pathname === usagePath && (fail
    ? Response.json({ detail: '用量读取失败' }, { status: 500 }) : response(result(32))) })
  fail = true
  await page.query()
  expect(page.wrapper.get('[role="alert"]').text()).toContain('用量读取失败')
  expect(page.wrapper.get('[aria-label="区间用量概览"]').text()).toContain('32')
  fail = false
  await page.click('重试')
  expect(page.wrapper.text()).not.toContain('用量读取失败')
  expect(page.usage().map(call => call.params)).toEqual(Array(3).fill(page.usage()[0].params))
})

test('usage chart redraws for dark mode and the selected color without a new query', async () => {
  const page = await open({ observe: true, fetchResponse: call => call.pathname === usagePath && response(result()) })
  const firstChart = vi.mocked(init).mock.results[0].value
  const originalColor = page.theme.themeColor
  page.theme.setThemeScheme('dark')
  await flushPromises()
  expect(firstChart.dispose).toHaveBeenCalledOnce()
  expect(vi.mocked(init).mock.lastCall[1]).toBe('dark')
  page.theme.updateThemeColors('primary', '#cc4400')
  await flushPromises()
  const current = vi.mocked(init).mock.results.at(-1).value
  expect(current.setOption.mock.lastCall[0]).toMatchObject({ backgroundColor: 'transparent', color: [page.theme.themeColor, expect.any(String), expect.any(String)] })
  expect(page.usage()).toHaveLength(1)
  page.theme.updateThemeColors('primary', originalColor)
  page.theme.setThemeScheme('light')
  await flushPromises()
  expect(vi.mocked(init).mock.lastCall[1]).toBeUndefined()
  page.wrapper.unmount()
  expect(vi.mocked(init).mock.results.every(({ value }) => value.dispose.mock.calls.length > 0)).toBe(true)
  expect(page.observers.every(observer => observer.disconnect.mock.calls.length > 0)).toBe(true)
})
