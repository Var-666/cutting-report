// @vitest-environment jsdom

import assert from 'node:assert/strict'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { NButton, NRadioButton, NRadioGroup } from 'naive-ui'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { ref } from 'vue'
import { getAlarmStatus, listAlarmIncidentPage, listAlarmIncidents, listAlarmReportPage, listAlarmReports } from '../src/api/alarms'
import { getLeadershipReport, listReports } from '../src/api/cutting'
import { listCuttingFiles } from '../src/api/dispatch'
import { getEnergyMonth } from '../src/api/energy'
import { useCuttingFileBrowser } from '../src/composables/useCuttingFileBrowser'
import { useAppStore } from '../src/state/app'
import AdminHomeView from '../src/views/AdminHomeView.vue'
import AlarmCenterView from '../src/views/AlarmCenterView.vue'

vi.mock('../src/api/alarms')
vi.mock('../src/api/dispatch')
vi.mock('../src/api/cutting')
vi.mock('../src/api/energy')
vi.mock('echarts/core', async importOriginal => ({
  ...await importOriginal(),
  init: () => ({ setOption() {}, resize() {}, dispose() {} }),
}))

enableAutoUnmount(afterEach)
beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  vi.mocked(getAlarmStatus).mockResolvedValue({ enabled: true, worker_running: true, machine_nos: [] })
  vi.mocked(listAlarmIncidents).mockResolvedValue([])
  vi.mocked(listAlarmReports).mockResolvedValue([])
  vi.mocked(listAlarmIncidentPage).mockResolvedValue({ items: [], page: 1, severity_counts: {} })
  vi.mocked(listAlarmReportPage).mockResolvedValue({ items: [], page: 1 })
})

function directoryQuery(listFiles) {
  vi.mocked(listCuttingFiles).mockImplementation(listFiles)
  const state = useCuttingFileBrowser({ pageActive: ref(true), systemStatus: ref(null), isDispatching: () => false, initialProjectId: '', rememberSelectedProject() {}, notice: ref(''), showNotice() {} })
  state.selectedProject.value = { project_id: 'P1' }
  state.selectedCatalog.value = { catalog_id: 'old' }
  state.rows.value = [{ document_no: 'old.gen' }]
  state.total.value = 40
  return state
}

test('switching directories clears old files immediately and failure cannot restore them', async () => {
  let fail
  const page = directoryQuery(() => new Promise((_resolve, reject) => { fail = reject }))
  const request = page.selectCatalog({ catalog_id: 'new' })
  assert.equal(page.selectedCatalog.value.catalog_id, 'new')
  assert.equal(page.rows.value.length, 0)
  assert.equal(page.total.value, 0)
  assert.equal(page.loading.value, true)
  fail(new Error('PDM unavailable'))
  await request
  assert.equal(page.rows.value.length, 0)
  assert.equal(page.fileError.value, 'PDM unavailable')
  assert.equal(page.loading.value, false)
})

test('file pagination uses applied search until query submission and keeps data on failure', async () => {
  const calls = []
  let rejectQuery = false
  const page = directoryQuery(async query => {
    calls.push(query)
    if (rejectQuery) throw new Error('offline')
    return { items: [{ document_no: `${query.keyword || 'all'}.gen` }], total: 40 }
  })
  page.fileKeyword.value = 'draft'
  await page.changePage(2)
  assert.equal(calls[0].keyword, '')
  assert.equal(page.appliedFileKeyword.value, '')
  assert.equal(page.page.value, 2)
  await page.applyFileSearch()
  assert.equal(calls[1].keyword, 'draft')
  assert.equal(page.appliedFileKeyword.value, 'draft')
  assert.equal(page.page.value, 1)
  page.fileKeyword.value = 'unsubmitted'
  rejectQuery = true
  await page.changePage(2)
  assert.equal(calls[2].keyword, 'draft')
  assert.equal(page.page.value, 1)
  assert.equal(page.rows.value[0].document_no, 'draft.gen')
  assert.equal(page.fileError.value, 'offline')
  rejectQuery = false
  await page.changePage(2)
  assert.equal(page.page.value, 2)
  assert.equal(page.fileError.value, '')
})

function mountAlarm() {
  return mount(AlarmCenterView, { global: { components: { NButton, NRadioButton, NRadioGroup } } })
}

test('alarm pagination commits new pages only after success and retains results on failure', async () => {
  let fail = false
  vi.mocked(listAlarmIncidentPage).mockImplementation(async ({ page }) => {
    if (fail) throw new Error('监控数据暂不可用')
    return { items: [{ incident_id: `i${page}`, alarm_desc: `告警第${page}页`, status: 'open', severity: 'warning' }], page, total: 30, page_count: 3, severity_counts: {} }
  })
  vi.mocked(listAlarmReportPage).mockImplementation(async ({ page }) => ({
    items: [{ report_id: `r${page}`, pdf_filename: `告警文件第${page}页.pdf`, incident_ids: [], status: 'completed' }], page, total: 20, page_count: 2,
  }))
  const wrapper = mountAlarm()
  await flushPromises()
  fail = true
  const events = wrapper.get('[aria-label="切割机告警事件"]')
  const reports = wrapper.get('[aria-label="实体告警报告"]')
  await events.get('button[aria-label="第 2 页"]').trigger('click')
  await flushPromises()
  expect(wrapper.get('[role="alert"]').text()).toContain('监控数据暂不可用')
  expect(events.text()).toContain('告警第1页')
  expect(reports.text()).toContain('告警文件第1页')
  expect(events.get('button[aria-current="page"]').attributes('aria-label')).toBe('第 1 页')

  fail = false
  await events.get('button[aria-label="第 2 页"]').trigger('click')
  await flushPromises()
  await reports.get('button[aria-label="第 2 页"]').trigger('click')
  await flushPromises()
  expect(events.text()).toContain('告警第2页')
  expect(reports.text()).toContain('告警文件第2页')
  expect(wrapper.find('[role="alert"]').exists()).toBe(false)
})

test('an initial alarm failure shows unknown states and retry shows a successful empty result', async () => {
  vi.mocked(getAlarmStatus).mockRejectedValueOnce(new Error('监控源离线'))
  const wrapper = mountAlarm()
  await flushPromises()
  expect(wrapper.text()).toContain('状态未知')
  expect(wrapper.text()).toContain('告警事件读取失败')
  expect(wrapper.text()).toContain('告警报告读取失败')
  expect(wrapper.text()).not.toContain('状态正常')
  expect(wrapper.text()).not.toContain('暂无实体告警报告')
  await wrapper.get('[role="alert"]').get('button').trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('运行中')
  expect(wrapper.text()).toContain('当前筛选下暂无告警')
  expect(wrapper.text()).toContain('暂无实体告警报告')
  expect(wrapper.text()).not.toContain('状态未知')
})

test('an empty alarm list preserves the disabled monitoring status', async () => {
  vi.mocked(getAlarmStatus).mockResolvedValue({ enabled: false, worker_running: false, machine_nos: [] })
  const wrapper = mountAlarm()
  await flushPromises()
  expect(wrapper.text()).toContain('未启用')
  expect(wrapper.text()).toContain('当前筛选下暂无告警')
  expect(wrapper.text()).toContain('监控服务状态见页头')
  expect(wrapper.text()).not.toContain('系统将持续监控')
})

test('home overview retries only the failed data and clears its error after recovery', async () => {
  const leadership = vi.mocked(getLeadershipReport)
  leadership.mockRejectedValueOnce(new Error('生产源离线')).mockResolvedValue({
    current_month_total: { task_count: 7 }, snapshot: {}, daily_records: [],
  })
  vi.mocked(getEnergyMonth).mockResolvedValue({ summary: {} })
  vi.mocked(listReports).mockResolvedValue({ items: [], total: 0 })
  const pinia = createPinia()
  useAppStore(pinia).reducedMotion = true
  const wrapper = mount(AdminHomeView, { global: { plugins: [pinia], stubs: { RouterLink: true } } })
  await flushPromises()
  expect(wrapper.text()).toContain('生产统计未能更新')
  expect(wrapper.text()).not.toContain('生产源离线')
  const retry = wrapper.findAll('button').find(button => button.text() === '重试失败项目')
  if (!retry) throw new Error('Retry button is missing')
  await retry.trigger('click')
  await flushPromises()
  expect(leadership).toHaveBeenCalledTimes(2)
  expect(getEnergyMonth).toHaveBeenCalledTimes(1)
  expect(wrapper.text()).not.toContain('生产统计未能更新')
  expect(wrapper.text()).not.toContain('重试失败项目')
})
