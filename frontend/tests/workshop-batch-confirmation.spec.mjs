// @vitest-environment jsdom

import assert from 'node:assert/strict'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { NButton, NCheckbox, NDialogProvider } from 'naive-ui'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import * as Vue from 'vue'
import { useWorkshopConfirmation } from '../src/composables/useWorkshopConfirmation.ts'
import { useWorkshopCorrection } from '../src/composables/useWorkshopCorrection.ts'
import { taskProductionDate } from '../src/api/reportDates.ts'
import { confirmWorkshopTasks } from '../src/api/reports.ts'
import { reportCache } from '../src/api/reportCache'
import AppDatePicker from '../src/components/AppDatePicker.vue'
import AppSelect from '../src/components/AppSelect.vue'
import WorkshopCorrectionDialog from '../src/components/workshop/WorkshopCorrectionDialog.vue'
import WorkshopView from '../src/views/WorkshopView.vue'
import { setupSoybeanComponents } from '../src/vendor/soybean-admin/components'

enableAutoUnmount(afterEach)

import { setDeviceCatalog, resetDeviceContext } from '../src/api/deviceContext.ts'
import { stubTableLayout } from './table-layout-fixture.mjs'

beforeEach(() => setDeviceCatalog({ default_device_id: 'Cutter01', items: [{ id: 'Cutter01' }] }))
const originalFetch = globalThis.fetch
afterEach(() => { globalThis.fetch = originalFetch; reportCache.clear(); resetDeviceContext() })

test('batch confirmation sends all selected task ids in one request', async () => {
  const calls = []
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options })
    return Response.json({ success: true, data: { confirmed_count: 2, items: [] } })
  }

  await confirmWorkshopTasks(['task-1', 'task-2'], '2026-09-11')

  assert.equal(calls.length, 1)
  assert.equal(calls[0].url, '/api/v1/workshop/tasks/confirm?device_id=Cutter01')
  assert.equal(calls[0].options.method, 'POST')
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    report_date: '2026-09-11',
    task_ids: ['task-1', 'task-2'],
  })
})

test('task revisions accompany confirmation so a rebuilt scope cannot use an old review', async () => {
  let sent
  globalThis.fetch = async (url, options) => {
    sent = JSON.parse(options.body)
    return Response.json({ success: true, data: { items: [] } })
  }
  await confirmWorkshopTasks(['A', 'B'], '2026-09-30', { A: 2, B: 1 })
  assert.deepEqual(sent.snapshot_revisions, { A: 2, B: 1 })
})

const response = data => Response.json({ success: true, data })

test('confirmation workflow retains selected revisions and keeps confirmed rows confirmed after a stale refresh', async t => {
  const originalWindow = globalThis.window
  globalThis.window = { setTimeout: () => 1, clearTimeout() {} }
  t.onTestFinished(() => { globalThis.window = originalWindow })
  const task = { task_id: 'A', snapshot_revision: 3, confirmed_by: null }
  const result = Vue.ref({ items: [task], confirmed_count: 0, page: 2 })
  const calls = []
  let finish
  globalThis.fetch = (_url, options) => {
    calls.push(JSON.parse(options.body))
    return new Promise(resolve => { finish = resolve })
  }
  const state = useWorkshopConfirmation({
    isWorker: Vue.ref(true), user: () => ({ display_name: '工人甲' }), result,
    filters: { reportDate: '2026-09-30' }, error: Vue.ref(''),
    load: async page => { assert.equal(page, 2); result.value = { items: [task], confirmed_count: 0, page } },
  })
  state.setTaskSelected(task, true)
  state.openBatchConfirmation()
  const pending = state.submitBatchConfirmation()
  await state.submitBatchConfirmation()
  assert.equal(calls.length, 1)
  assert.deepEqual(calls[0].snapshot_revisions, { A: 3 })
  finish(response({ items: [{ task_id: 'A', worker_id: 'u1', worker_name: '工人甲', confirmed_at: '2026-09-30' }] }))
  await pending
  assert.equal(result.value.items[0].confirmed_by, '工人甲')
  assert.equal(result.value.confirmed_count, 1)
  assert.equal(state.selectedTasks.value.size, 0)
  assert.equal(state.batchConfirmation.visible, false)
  assert.equal(state.batchConfirmation.saving, false)
  assert.equal(state.confirmationFeedback.type, 'success')
  state.pauseFeedback()
})

test('correction workflow retains ownership checks, production date, reason rules and worker filtering', async t => {
  const calls = [], scope = Vue.effectScope()
  t.onTestFinished(() => scope.stop())
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url: String(url), body: options.body && JSON.parse(options.body) })
    return response({ items: String(url).includes('/users') ? [
      { user_id: 'old', role: 'worker', active: true },
      { user_id: 'new', role: 'worker', active: true, display_name: '工人乙' },
      { user_id: 'inactive', role: 'worker', active: false },
    ] : [] })
  }
  const worker = Vue.ref(true)
  let refreshed = 0
  const state = scope.run(() => useWorkshopCorrection({
    isWorker: worker, user: () => ({ user_id: 'me' }), filters: { reportDate: '2026-10-01' },
    rowDate: row => taskProductionDate(row, '2026-10-01'), showConfirmationFeedback() {}, refresh: async () => { refreshed++ },
  }))
  const row = { task_id: 'A', confirmed_by: '原工人', confirmed_by_id: 'old', production_date: '2026-09-30' }
  await state.openCorrection(row)
  assert.equal(state.correction.visible, false)
  assert.equal(calls.length, 0)
  worker.value = false
  await state.openCorrection(row)
  assert.ok(calls.some(call => call.url.includes('report_date=2026-09-30')))
  assert.deepEqual(state.availableWorkers.value.map(user => user.user_id), ['new'])
  state.selectCorrectionReason(state.visibleCorrectionReasonPresets.value[0])
  state.correction.action = 'reassign'
  await Vue.nextTick()
  assert.equal(state.correction.reason, '')
  state.correction.reason = '现场记录核对'
  await state.submitCorrection()
  assert.match(state.correction.error, /请选择/)
  state.correction.workerId = 'new'
  await state.submitCorrection()
  assert.deepEqual(calls.at(-1).body, { report_date: '2026-09-30', action: 'reassign', worker_id: 'new', reason: '现场记录核对' })
  assert.equal(refreshed, 1)
  assert.equal(state.correction.visible, false)
})

test('correction dialog shows the task safely and lets the user select and edit a preset reason', async () => {
  const wrapper = mount(WorkshopCorrectionDialog, {
    props: {
      correction: { visible: true, saving: false, loading: false, row: { program_no: 'A<1>', confirmed_by: '工人甲' }, error: '请核对', audits: [] },
      isWorker: false, action: 'revoke', workerId: '', reason: '原因甲', availableWorkers: [], visibleCorrectionReasonPresets: ['原因甲', '原因乙'],
    },
    attachTo: document.body,
    global: { plugins: [{ install: setupSoybeanComponents }], components: { AppSelect }, stubs: { teleport: true } },
  })
  const presets = () => wrapper.get('[role="group"][aria-label="常用更正原因"]').findAllComponents(NButton)
  expect(presets().map(button => [button.text(), button.props('type')])).toEqual([['原因甲', 'primary'], ['原因乙', 'default']])
  expect(wrapper.get('[role="dialog"]').text()).toContain('A<1>')
  expect(wrapper.get('[role="alert"]').text()).toBe('请核对')
  await presets()[1].trigger('click')
  expect(wrapper.emitted('reason')).toEqual([['原因乙']])
  await wrapper.setProps({ reason: '原因乙' })
  expect(presets().map(button => button.props('type'))).toEqual(['default', 'primary'])
  await wrapper.get('textarea[name="correction_reason"]').setValue('现场核对')
  expect(wrapper.emitted('update:reason')).toEqual([['现场核对']])
})

test('worker can review several tasks before confirming once and blank fields remain missing', async () => {
  stubTableLayout()
  const stamp = '2026-09-30'
  const tasks = [
    { task_id: 'A', program_no: 'OP-027', project_code: null, ship_no: '  ', segment_name: '', production_date: stamp, snapshot_revision: 2 },
    { task_id: 'B', program_no: 'OP-028', project_code: 'CMYZ0170', ship_no: '非船', segment_name: '804C', production_date: stamp, snapshot_revision: 1 },
    { task_id: 'C', program_no: 'OP-029', confirmed_by: '其他工人', confirmed_by_id: 'u2', production_date: stamp },
  ]
  const user = { user_id: 'w1', username: 'w1', display_name: '当前工人', role: 'worker', active: true }
  const confirmations = []
  const day = { report_date: stamp, period_start: stamp, period_end: stamp, item_count: 3, confirmed_count: 1 }
  globalThis.fetch = async (input, init) => {
    const path = new URL(String(input), 'http://localhost').pathname
    if (path.endsWith('/cancel')) return response({})
    if (path === '/api/v1/workshop/latest-date') return response({ report_date: stamp })
    if (path === '/api/v1/workshop/reports') return response(day)
    if (path === '/api/v1/workshop/tasks') return response({ report: day, items: tasks, total: 3, page: 1,
      page_size: 10, project_names: [], confirmed_count: 1 })
    if (path === '/api/v1/workshop/tasks/confirm') {
      const payload = JSON.parse(init.body)
      confirmations.push(payload)
      return response({ confirmed_count: 2, items: payload.task_ids.map(task_id => ({
        task_id, worker_id: user.user_id, worker_name: user.display_name, confirmed_at: '2026-09-30T08:00:00+08:00',
      })) })
    }
    throw new Error(`Unexpected request ${path}`)
  }
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/worker/tasks', name: 'worker-tasks', component: WorkshopView, props: { user, mode: 'worker' } },
  ] })
  await router.push('/worker/tasks')
  const wrapper = mount({ render: () => Vue.h(NDialogProvider, {}, { default: () => Vue.h(RouterView) }) }, {
    attachTo: document.body,
    global: { plugins: [router, createPinia(), { install: setupSoybeanComponents }], components: { AppSelect, AppDatePicker }, stubs: { teleport: true } },
  })
  await flushPromises()
  const rows = wrapper.findAll('tbody tr')
  const first = rows.find(row => row.text().includes('OP-027'))
  const second = rows.find(row => row.text().includes('OP-028'))
  const closed = rows.find(row => row.text().includes('OP-029'))
  expect(first.findAll('td').slice(2, 5).map(cell => cell.text())).toEqual(['--', '--', '--'])
  expect(second.findAll('td').slice(2, 5).map(cell => cell.text())).toEqual(['CMYZ0170', '非船', '804C'])
  expect(closed.getComponent(NCheckbox).props('disabled')).toBe(true)
  expect(wrapper.text()).not.toContain('更正日报源记录')
  await wrapper.get('[role="checkbox"][aria-label="全选当前页待确认任务"]').trigger('click')
  const review = wrapper.findAll('button').find(button => button.text() === '确认选中任务')
  await review.trigger('click')
  await flushPromises()
  const dialog = wrapper.get('[role="dialog"][aria-modal="true"]')
  expect(dialog.text()).toContain('确认以下任务由我完成')
  expect(dialog.text()).not.toContain('OP-029')
  expect(dialog.findAll('tbody tr')).toHaveLength(2)
  expect(dialog.findAll('tbody tr')[0].findAll('td').slice(1, 3).map(cell => cell.text())).toEqual(['--', '--'])
  expect(confirmations).toHaveLength(0)
  await dialog.findAll('button').find(button => button.text() === '确认这 2 项任务').trigger('click')
  await flushPromises()
  expect(confirmations).toEqual([{
    report_date: '2026-09-30', task_ids: ['A', 'B'], snapshot_revisions: { A: 2, B: 1 },
  }])
  expect(wrapper.text()).toContain('撤销我的确认')
  expect(tasks[0]).toMatchObject({ project_code: null, ship_no: '  ', segment_name: '' })
})
