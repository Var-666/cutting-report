// @vitest-environment jsdom

import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { NButton, NCheckbox, NDrawer, NInput, NInputNumber } from 'naive-ui'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { h } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { resetDeviceContext, selectDevice, setDeviceCatalog } from '../src/api/deviceContext'
import { analyzeWelding, downloadWelding, getLatestWeldingAnalysis, getWeldingPart, getWeldingReport } from '../src/api/welding'
import AppDatePicker from '../src/components/AppDatePicker.vue'
import AppSelect from '../src/components/AppSelect.vue'
import ReportExportMenu from '../src/components/ReportExportMenu.vue'
import ReportPeriodPicker from '../src/components/ReportPeriodPicker.vue'
import { useAppStore } from '../src/state/app'
import WeldingReportView from '../src/views/WeldingReportView.vue'
import { stubTableLayout } from './table-layout-fixture.mjs'

vi.mock('../src/api/welding', async importOriginal => ({
  ...await importOriginal(), getWeldingReport: vi.fn(), getLatestWeldingAnalysis: vi.fn(), analyzeWelding: vi.fn(),
  downloadWelding: vi.fn(), getWeldingPart: vi.fn(),
}))
vi.mock('../src/api/reportDates', async importOriginal => ({
  ...await importOriginal(), reportToday: () => '2026-10-26',
}))

const dates = { start: '2026-09-01', end: '2026-09-02' }
const line = (id, enabled = true) => ({ id, type: 'welding', name: id, enabled })
const data = (query, label = '当前明细', options = {}) => ({
  query, data_version: label, summary: { job_count: 3 }, total: 25, items: [{ id: 15, serial_number: label, weld_type_label: '角焊' }],
  meta: { quality: {}, notices: [], pending_metrics: {}, devices: [0, 1, 2] }, ...options,
})
const pendingData = (query, label) => data(query, label, {
  meta: { quality: {}, notices: [], pending_metrics: {}, devices: [0, 1, 2], report_data: { refreshing: true, stale: true, data_as_of: '2026-09-02' } },
})

enableAutoUnmount(afterEach)
beforeEach(() => {
  vi.resetAllMocks()
  getWeldingReport.mockImplementation(async (_kind, query) => data(query))
  getLatestWeldingAnalysis.mockResolvedValue(null)
  downloadWelding.mockResolvedValue(undefined)
})
afterEach(() => {
  vi.useRealTimers()
  resetDeviceContext()
})

async function open(kind = 'daily', query = dates, enabled = true) {
  stubTableLayout()
  setDeviceCatalog({ default_device_id: 'weld-a', items: [line('weld-a', enabled), line('weld-b')] })
  const router = createRouter({ history: createMemoryHistory(), routes: [
    ...['dashboard', 'daily', 'jobs', 'equipment', 'parts'].map(kind => ({
      path: `/${kind}`, name: `welding-${kind}`, component: WeldingReportView, props: { kind },
    })),
    { path: '/admin/settings/database', component: { render: () => h('h1', '数据库设置') } },
  ] })
  await router.push({ name: `welding-${kind}`, query })
  const pinia = createPinia()
  useAppStore(pinia).reducedMotion = true
  const wrapper = mount(RouterView, { attachTo: document.body,
    global: { plugins: [router, pinia], components: { AppDatePicker, AppSelect, NButton, NCheckbox, NInput, NInputNumber },
      stubs: { teleport: true, WeldingCharts: true } },
  })
  await flushPromises()
  const button = label => wrapper.findAll('button').find(element => element.text() === label)
  const click = async label => { await button(label).trigger('click'); await flushPromises() }
  return {
    wrapper, router, button, click,
    form: () => wrapper.get('form[aria-label="焊接报表查询"]'),
    async submit() {
      await wrapper.get('form[aria-label="焊接报表查询"]').trigger('submit')
      await flushPromises()
    },
    async select(label, value) {
      const control = wrapper.findAllComponents(AppSelect).find(component => component.find(`[aria-label="${label}"]`).exists())
      control.vm.$emit('update:modelValue', value)
      await flushPromises()
    },
    async number(label, value) {
      const control = wrapper.findAllComponents(NInputNumber).find(component => component.element.closest('label')?.textContent.startsWith(label))
      control.vm.$emit('update:value', value)
      await flushPromises()
    },
    async export(format) {
      wrapper.getComponent(ReportExportMenu).vm.$emit('select', format)
      await flushPromises()
    },
  }
}

test('jobs filter dialog keeps cancelled and reset edits local and applies numeric zero', async () => {
  const page = await open('jobs')
  await page.click('筛选')
  await page.number('机器人编号', 3)
  await page.click('取消')
  expect(page.wrapper.find('form[aria-label="焊接报表查询"]').exists()).toBe(false)
  await page.click('筛选')
  expect(page.form().findAllComponents(NInputNumber).find(control => control.element.closest('label').textContent.startsWith('机器人编号')).props('value')).toBeNull()
  await page.number('机器人编号', 3)
  await page.click('重置')
  expect(getWeldingReport).toHaveBeenCalledOnce()
  await page.number('机器人编号', 0)
  await page.submit()
  expect(getWeldingReport.mock.lastCall[1]).toMatchObject({ robot_no: 0, page: 1 })
  expect(page.router.currentRoute.value.query.robot_no).toBe('0')
  expect(page.wrapper.text()).toContain('机器人 0')
})

test('parts keeps its own status in the route and export, ignoring a legacy job state', async () => {
  const page = await open('parts', { ...dates, state: '8', part_state: '0', keep: 'notice' })
  expect(page.wrapper.text()).toContain('工件状态代码 0')
  expect(page.wrapper.text()).not.toContain('作业状态代码 8')
  await page.click('筛选')
  await page.number('工件状态代码', 4)
  await page.submit()
  expect(page.router.currentRoute.value.query).toMatchObject({ part_state: '4', keep: 'notice' })
  expect(page.router.currentRoute.value.query).not.toHaveProperty('state')
  await page.export('csv')
  const [path] = downloadWelding.mock.lastCall
  const { searchParams: filters } = new URL(path, 'http://localhost')
  expect(filters.get('part_state')).toBe('4')
  expect(filters.has('state')).toBe(false)
  expect(filters.has('page')).toBe(false)
  await page.click('筛选')
  await page.number('工件状态代码', null)
  await page.submit()
  expect(page.router.currentRoute.value.query).not.toHaveProperty('part_state')
})

test('job details open their matching part with the last successful historical dates', async () => {
  const page = await open('jobs')
  await page.click('筛选')
  page.wrapper.getComponent(ReportPeriodPicker).vm.$emit('update:startDate', '2026-08-01')
  await page.click('取消')
  await page.click('作业详情')
  const drawer = page.wrapper.findAllComponents(NDrawer).find(part => part.props('show'))
  expect(drawer.props('width')).toBe('min(760px, 100vw)')
  expect(page.wrapper.get('[aria-label="焊接作业详情"]').text()).toContain('焊接作业详情')
  await page.click('查询该流水号的工件')
  expect(page.router.currentRoute.value.query).toMatchObject({ ...dates, serial_number: '当前明细' })
  expect(page.router.currentRoute.value.name).toBe('welding-parts')
})

test('a disabled line stays navigable without issuing a report and links to database setup', async () => {
  const page = await open('daily', dates, false)
  expect(getWeldingReport).not.toHaveBeenCalled()
  expect(page.wrapper.text()).toContain('焊接数据库尚未启用')
  expect(page.button('配置数据库')).toBeDefined()
  expect(page.wrapper.find('form[aria-label="焊接报表查询"]').exists()).toBe(false)
  await page.click('配置数据库')
  expect(page.router.currentRoute.value.path).toBe('/admin/settings/database')
})

const analysisResult = (overview, data_version = '当前明细') => ({
  overview, data_version, mode: 'model', status: 'success', facts: [], findings: [], generated_at: '2026-10-26T08:00:00+08:00',
})

test('daily restores a successful analysis from cache and retains it when a model refresh fails', async () => {
  getLatestWeldingAnalysis.mockResolvedValueOnce(analysisResult('历史焊接结论', 'old-version'))
  analyzeWelding.mockRejectedValueOnce(new Error('模型暂不可用'))
  const page = await open()
  expect(page.wrapper.text()).toContain('历史焊接结论')
  expect(page.wrapper.text()).toContain('历史统计')
  expect(getLatestWeldingAnalysis).toHaveBeenCalledOnce()
  expect(analyzeWelding).not.toHaveBeenCalled()
  await page.click('重新生成分析')
  expect(analyzeWelding.mock.lastCall).toEqual(['daily', expect.objectContaining(dates), '当前明细', expect.any(AbortSignal)])
  expect(page.wrapper.text()).toContain('模型暂不可用')
  expect(page.wrapper.text()).toContain('历史焊接结论')
})

test('a same-scope refresh keeps the old analysis and a later filter aborts superseded cache and model requests', async () => {
  getLatestWeldingAnalysis.mockResolvedValueOnce(analysisResult('首次分析'))
  const page = await open()
  let finishCache
  getWeldingReport.mockImplementationOnce(async (_kind, query) => data(query, '刷新统计'))
  getLatestWeldingAnalysis.mockImplementationOnce(() => new Promise(resolve => { finishCache = resolve }))
  await page.submit()
  expect(page.wrapper.text()).toContain('首次分析')
  expect(page.wrapper.text()).toContain('历史统计')
  expect(getLatestWeldingAnalysis.mock.lastCall[2]).toBe('刷新统计')
  const cacheSignal = getLatestWeldingAnalysis.mock.lastCall[3]
  let finishModel
  analyzeWelding.mockImplementationOnce(() => new Promise(resolve => { finishModel = resolve }))
  await page.click('重新生成分析')
  expect(cacheSignal.aborted).toBe(true)
  const modelSignal = analyzeWelding.mock.lastCall[3]
  await page.select('设备', 0)
  await page.submit()
  expect(modelSignal.aborted).toBe(true)
  finishModel(analysisResult('旧请求生成的分析', '刷新统计'))
  finishCache(analysisResult('旧请求的缓存', '刷新统计'))
  await flushPromises()
  expect(page.wrapper.text()).not.toContain('旧请求生成的分析')
  expect(page.wrapper.text()).not.toContain('旧请求的缓存')
  expect(page.wrapper.text()).not.toContain('首次分析')
  expect(getLatestWeldingAnalysis.mock.lastCall[1].device_no).toBe(0)
})

test('switching welding lines and leaving discard pending results and keep report data scoped', async () => {
  let finishOldCache
  getLatestWeldingAnalysis.mockImplementationOnce(() => new Promise(resolve => { finishOldCache = resolve }))
  const page = await open()
  const oldSignal = getLatestWeldingAnalysis.mock.lastCall[3]
  getWeldingReport.mockImplementationOnce(async (_kind, query) => data(query, '另一条线的作业', { total: 37 }))
  let finishNewCache
  getLatestWeldingAnalysis.mockImplementationOnce(() => new Promise(resolve => { finishNewCache = resolve }))
  selectDevice('weld-b')
  await flushPromises()
  expect(oldSignal.aborted).toBe(true)
  expect(page.wrapper.text()).toContain('37 条记录')
  finishOldCache(analysisResult('旧产线历史数据'))
  await flushPromises()
  expect(page.wrapper.text()).not.toContain('旧产线历史数据')
  const newSignal = getLatestWeldingAnalysis.mock.lastCall[3]
  page.wrapper.unmount()
  expect(newSignal.aborted).toBe(true)
  finishNewCache(analysisResult('离开后的缓存', '另一条线的作业'))
  await flushPromises()
})

test.each(['dashboard', 'daily', 'parts', 'jobs', 'equipment'])('%s replaces a retained snapshot in a background read without submitting filter drafts', async kind => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  getWeldingReport.mockImplementationOnce(async (_kind, query) => pendingData(query, '当前快照'))
    .mockImplementationOnce(async (_kind, query) => data(query, '后台完成', { total: 36 }))
  const page = await open(kind, { ...dates, device_no: '1' })
  expect(page.wrapper.text()).toContain('当前结果：')
  expect(page.wrapper.text()).toContain('设备 1')
  if (page.button('筛选')) await page.click('筛选')
  await page.select('设备', 2)
  await vi.advanceTimersByTimeAsync(5000)
  expect(getWeldingReport.mock.lastCall).toEqual([kind, expect.objectContaining({ device_no: 1 }), expect.any(AbortSignal), false, { mode: 'poll' }])
  expect(page.router.currentRoute.value.query.device_no).toBe('1')
  expect(page.wrapper.text()).toContain('36 条记录')
  const control = page.wrapper.findAllComponents(AppSelect).find(component => component.find('[aria-label="设备"]').exists())
  expect(control.props('modelValue')).toBe(2)
  await vi.advanceTimersByTimeAsync(5000)
  expect(getWeldingReport).toHaveBeenCalledTimes(2)
})

test('a new filter cancels the old snapshot poll, and the report stops checking at its cap', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  let snapshot
  let finishOld
  getWeldingReport.mockImplementationOnce(async (_kind, query) => pendingData(query, '旧快照'))
    .mockImplementationOnce((_kind, query) => new Promise(resolve => { finishOld = () => resolve(data(query, '迟到的快照')) }))
    .mockImplementation(async (_kind, query) => snapshot ??= pendingData(query, '新范围快照'))
  const page = await open('daily')
  await vi.advanceTimersByTimeAsync(5000)
  const oldSignal = getWeldingReport.mock.lastCall[2]
  await page.select('设备', 0)
  await page.submit()
  expect(oldSignal.aborted).toBe(true)
  finishOld()
  await flushPromises()
  expect(page.wrapper.text()).not.toContain('迟到的快照')
  expect(getWeldingReport.mock.lastCall[1].device_no).toBe(0)
  await vi.advanceTimersByTimeAsync(76 * 5000)
  const polls = getWeldingReport.mock.calls.filter(([, query, , , options]) => query.device_no === 0 && options.mode === 'poll')
  expect(polls).toHaveLength(75)
  await vi.advanceTimersByTimeAsync(5000)
  expect(getWeldingReport).toHaveBeenCalledTimes(78)
})

test('draft filters are visible beside the applied query and failures leave the applied scope for exports', async () => {
  const page = await open('daily', { ...dates, device_no: '1' })
  await page.select('设备', 2)
  expect(page.wrapper.text()).toContain('筛选条件已修改')
  let fail
  getWeldingReport.mockImplementationOnce(() => new Promise((resolve, reject) => { fail = reject }))
  await page.submit()
  expect(page.wrapper.text()).toContain('设备 1')
  expect(page.router.currentRoute.value.query.device_no).toBe('1')
  fail(new Error('来源暂不可用'))
  await flushPromises()
  expect(page.wrapper.get('[role="alert"]').text()).toContain('来源暂不可用')
  expect(page.wrapper.text()).toContain('设备 1')
  await page.export('csv')
  expect(new URL(downloadWelding.mock.lastCall[0], 'http://localhost').searchParams.get('device_no')).toBe('1')
  expect(getWeldingReport.mock.lastCall[4]).toEqual({ mode: 'refresh' })
  analyzeWelding.mockResolvedValueOnce(analysisResult('已应用区间的分析'))
  await page.click('生成当前区间分析')
  expect(analyzeWelding.mock.lastCall).toEqual(['daily', expect.objectContaining({ ...dates, device_no: 1 }), '当前明细', expect.any(AbortSignal)])
  expect(page.wrapper.text()).toContain('已应用区间的分析')
})

test('part details request their own seam page and unresolved associations cannot be exported', async () => {
  const part = { id: 22, project_no: 'P1', serial_number: 'PART-22' }
  getWeldingReport.mockImplementation(async (_kind, query) => data(query, '工件', { items: [part] }))
  getWeldingPart.mockResolvedValue({ part, items: [], total: 25, association_status: 'pending_verification', notice: '归属待核实' })
  const page = await open('parts')
  await page.click('工件与焊缝')
  expect(getWeldingPart.mock.lastCall).toEqual([22, 1, expect.any(AbortSignal)])
  const drawer = page.wrapper.get('[aria-label="工件与焊缝详情"]')
  expect(drawer.text()).toContain('归属待核实')
  expect(drawer.findAll('button').find(button => button.text() === '导出该工件全部焊缝 CSV').element.disabled).toBe(true)
  await drawer.get('button[aria-label="第 2 页"]').trigger('click')
  await flushPromises()
  expect(getWeldingPart.mock.lastCall).toEqual([22, 2, expect.any(AbortSignal)])
  expect(downloadWelding).not.toHaveBeenCalled()
})

test.each([
  ['dashboard', { device_no: 0, robot_no: '', part_state: '', page: 1, sort_by: 'time', descending: true }],
  ['daily', { device_no: 0, robot_no: '', part_state: '' }],
  ['parts', { device_no: 0, state: '', part_state: 4, project_no: 'P' }],
  ['jobs', { device_no: 0, robot_no: 0, state: 3, part_state: '', anomalies_only: true }],
  ['equipment', { device_no: 0, robot_no: 0, part_state: '' }],
])('%s restores supported linked filters exactly once', async (kind, expected) => {
  const query = { ...dates, device_no: '0', robot_no: '0', weld_type: '2', state: '3', part_state: '4', serial_number: 'S',
    project_no: 'P', section_no: 'SEC', part_no: 'PART', anomalies_only: 'true', sort_by: 'length', descending: 'false',
    page: '3', page_size: '50', unrelated: 'keep' }
  const page = await open(kind, query)
  expect(getWeldingReport).toHaveBeenCalledOnce()
  expect(getWeldingReport.mock.lastCall[1]).toMatchObject({ page: 3, page_size: 10, sort_by: 'length', descending: false, ...expected })
  expect(page.router.currentRoute.value.query).toMatchObject({ ...dates, device_no: '0', unrelated: 'keep' })
  expect(page.wrapper.text()).toContain('统计口径与数据核查')
  if (kind !== 'daily') expect(getLatestWeldingAnalysis).not.toHaveBeenCalled()
})

test('an old job response cannot replace a parts report from the newly selected line', async () => {
  const page = await open('jobs')
  const pending = []
  getWeldingReport.mockImplementation((kind, query, signal) => new Promise(resolve => pending.push({ kind, query, signal, resolve })))
  await page.wrapper.get('button[aria-label="第 2 页"]').trigger('click')
  await flushPromises()
  await page.router.push({ name: 'welding-parts', query: { ...dates, project_no: 'P', robot_no: '0' } })
  await flushPromises()
  expect(pending[0].signal.aborted).toBe(true)
  expect(pending[1]).toMatchObject({ kind: 'parts', query: { robot_no: '', project_no: 'P' } })
  selectDevice('weld-b')
  await flushPromises()
  expect(pending[1].signal.aborted).toBe(true)
  expect(pending).toHaveLength(3)
  for (const index of [2, 1, 0]) {
    const request = pending[index]
    request.resolve(data(request.query, `记录-${index}`, { items: [{ id: 15, serial_number: `记录-${index}`, project_no: 'P' }] }))
    await flushPromises()
  }
  expect(page.wrapper.text()).toContain('记录-2')
  expect(page.wrapper.text()).not.toContain('记录-1')
  expect(page.wrapper.text()).not.toContain('记录-0')
  expect(page.router.currentRoute.value.query.project_no).toBe('P')
})

test('the daily view invalidates impossible date drafts, deduplicates an active submit and still allows explicit refresh', async () => {
  const page = await open()
  const range = page.wrapper.getComponent(ReportPeriodPicker)
  for (const [start, end] of [
    ['', dates.end], [dates.start, ''], ['2026-02-30', dates.end],
    ['2026-10-27', '2026-10-26'], ['2025-01-01', '2026-10-26'],
  ]) {
    range.vm.$emit('update:startDate', start)
    range.vm.$emit('update:endDate', end)
    await page.submit()
    expect(page.wrapper.text()).toContain('请选择有效日期区间')
    expect(getWeldingReport).toHaveBeenCalledOnce()
  }
  range.vm.$emit('update:startDate', dates.start)
  range.vm.$emit('update:endDate', dates.end)
  const pending = []
  getWeldingReport.mockImplementationOnce((_kind, query, signal) => new Promise(resolve => pending.push({ query, signal, resolve })))
  await page.submit()
  await page.submit()
  expect(getWeldingReport).toHaveBeenCalledTimes(2)
  expect(pending[0].signal.aborted).toBe(false)
  pending[0].resolve(data(pending[0].query, '第一次刷新'))
  await flushPromises()
  await page.submit()
  expect(getWeldingReport).toHaveBeenCalledTimes(3)
  expect(getWeldingReport.mock.lastCall[4]).toEqual({ mode: 'refresh' })
  await page.wrapper.get('button[aria-label="第 2 页"]').trigger('click')
  await flushPromises()
  expect(getWeldingReport.mock.lastCall[1].page).toBe(2)
  expect(getWeldingReport.mock.lastCall[4]).toEqual({ mode: 'read' })
})

test('daily keeps verified rows through failure and distinguishes the next empty result', async () => {
  const page = await open()
  expect(page.wrapper.text()).toContain('25 条记录')
  getWeldingReport.mockRejectedValueOnce(new Error('来源不可用'))
  await page.submit()
  expect(page.wrapper.text()).toContain('来源不可用')
  expect(page.wrapper.text()).toContain('25 条记录')
  getWeldingReport.mockImplementationOnce(async (_kind, query) =>
    data(query, '空区间', { items: [], total: 0, summary: { job_count: 0 } }))
  await page.click('重试')
  expect(page.wrapper.text()).not.toContain('来源不可用')
  expect(page.wrapper.text()).toContain('当前区间暂无可归属的作业记录')
})
