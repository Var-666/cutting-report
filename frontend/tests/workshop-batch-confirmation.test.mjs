import assert from 'node:assert/strict'
import { afterEach, beforeEach, test } from 'node:test'
import { readFileSync } from 'node:fs'
import { parse, compileScript } from '@vue/compiler-sfc'
import * as Vue from 'vue'
import { renderToString } from '@vue/server-renderer'
import { scriptFunction } from './typescript-helpers.mjs'
import { useWorkshopConfirmation } from '../src/composables/useWorkshopConfirmation.ts'
import { useWorkshopCorrection } from '../src/composables/useWorkshopCorrection.ts'
import { taskProductionDate } from '../src/api/reportDates.ts'
import { formatChineseDateTime } from '../src/utils/dateDisplay.ts'
import { confirmWorkshopTasks } from '../src/api/reports.ts'

import { setDeviceCatalog, resetDeviceContext } from '../src/api/deviceContext.ts'

beforeEach(() => setDeviceCatalog({ default_device_id: 'Cutter01', items: [{ id: 'Cutter01' }] }))
const read = path => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8')
const confirmationSource = read('composables/useWorkshopConfirmation.ts')
const correctionSource = read('composables/useWorkshopCorrection.ts')
const batchDialogSource = read('components/workshop/WorkshopBatchConfirmationDialog.vue')
const originalFetch = globalThis.fetch
afterEach(() => { globalThis.fetch = originalFetch; resetDeviceContext() })

test('batch confirmation sends all selected task ids in one request', async () => {
  const calls = []
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options })
    return { ok: true, json: async () => ({ success: true, data: { confirmed_count: 2, items: [] } }) }
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
    return { ok: true, json: async () => ({ success: true, data: { items: [] } }) }
  }
  await confirmWorkshopTasks(['A', 'B'], '2026-09-30', { A: 2, B: 1 })
  assert.deepEqual(sent.snapshot_revisions, { A: 2, B: 1 })
})

test('worker task table exposes multi-select and reviews tasks before confirmation', () => {
  const descriptor = parse(readFileSync(new URL('../src/views/WorkshopView.vue', import.meta.url), 'utf8')).descriptor
  const script = confirmationSource
  const template = descriptor.template.content

  assert.match(script, /const selectedTasks = ref\(new Map<string, WorkshopTask>\(\)\)/)
  assert.match(script, /async function submitBatchConfirmation/)
  assert.match(script, /const confirmationResult = await confirmWorkshopTasks\(tasks\.map\(\(row\) => row\.task_id\)/)
  assert.match(script, /function applyConfirmedTasks\(confirmations: TaskConfirmation\[\] = \[\]\)/)
  assert.match(script, /confirmed_by_id: confirmation\.worker_id/)
  assert.match(script, /confirmed_by: confirmation\.worker_name/)
  assert.match(script, /confirmed_at: confirmation\.confirmed_at/)
  assert.equal((script.match(/applyConfirmedTasks\(confirmations\)/g) || []).length, 2)
  assert.match(template, /aria-label="全选当前页待确认任务"/)
  assert.match(template, /确认选中任务/)
  assert.match(batchDialogSource, /确认以下任务由我完成/)
  assert.match(batchDialogSource, /:items="batchConfirmation\.tasks"/)
  assert.match(batchDialogSource, /工程\/船号[\s\S]*分段名[\s\S]*程序号[\s\S]*生产时间/)
  assert.match(template, /@click="openSingleConfirmation\(row\)"/)
  assert.match(template, /:disabled="loading \|\| correction.saving"/)
  assert.match(template, /:disabled="loading \|\| batchConfirmation.saving"/)
  assert.doesNotMatch(script, /title: [\"']备注[\"']/)
  assert.doesNotMatch(template, /row\.remark/ )
  assert.match(descriptor.scriptSetup.content, /key: 'c8', title: "切割时长", width: 104, align: 'center'/)
  assert.match(template, /<AppReportTable :columns="taskTableColumns"/)
  const shared = parse(readFileSync(new URL('../src/components/AppReportTable.vue', import.meta.url), 'utf8')).descriptor
  const styles = shared.styles.map(style => style.content).join('\n')
  assert.match(styles, /n-data-table-td\)\{[^}]*padding:var\(--table-cell-padding\);/)
  assert.match(read('assets/style.css'), /--table-cell-padding: 6px 14px;/)
  assert.match(styles, /border-right-color:color-mix\(in srgb, var\(--n-merged-border-color\) 35%, transparent\)/)
})

test('workshop keeps confirmation corrections while the source correction entry is removed', () => {
  const descriptor = parse(readFileSync(new URL('../src/views/WorkshopView.vue', import.meta.url), 'utf8')).descriptor
  assert.doesNotMatch(descriptor.scriptSetup.content, /WorkshopSourceCorrections/)
  assert.doesNotMatch(descriptor.template.content, /更正日报源记录|WorkshopSourceCorrections/)
  assert.match(correctionSource, /await correctWorkshopTask\(row.task_id/)
  assert.match(correctionSource, /await revokeWorkshopTask\(row.task_id/)
})

const response = data => ({ ok: true, json: async () => ({ success: true, data }) })

test('confirmation workflow retains selected revisions and keeps confirmed rows confirmed after a stale refresh', async t => {
  const originalWindow = globalThis.window
  globalThis.window = { setTimeout: () => 1, clearTimeout() {} }
  t.after(() => { globalThis.window = originalWindow })
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
  t.after(() => scope.stop())
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

test('correction dialog renders only the selected reason as active and preserves its task and error text', async () => {
  const descriptor = parse(read('components/workshop/WorkshopCorrectionDialog.vue')).descriptor
  const script = compileScript(descriptor, { id: 'correction-dialog-test', inlineTemplate: true }).content
    .replace(/^import .*$/gm, '').replace('export default', 'return')
  const bindings = { ...Vue, ...Object.fromEntries(Object.entries(Vue).map(([key, value]) => [`_${key}`, value])), time: formatChineseDateTime }
  const component = scriptFunction(...Object.keys(bindings), script)(...Object.values(bindings))
  const app = Vue.createSSRApp({ render: () => Vue.h(component, {
    correction: { visible: true, saving: false, loading: false, row: { program_no: 'A<1>', confirmed_by: '工人甲' }, error: '请核对', audits: [] },
    isWorker: false, action: 'revoke', workerId: '', reason: '原因甲', availableWorkers: [], visibleCorrectionReasonPresets: ['原因甲', '原因乙'],
  }) })
  app.component('NModal', { inheritAttrs: false, props: ['show'], setup: (props, { slots }) => () => props.show ? slots.default?.() : null })
  app.component('NButton', { setup: (_, { attrs, slots }) => () => Vue.h('button', attrs, slots.default?.()) })
  app.component('NInput', { setup: (_, { attrs }) => () => Vue.h('textarea', attrs) })
  app.component('AppSelect', { setup: (_, { slots }) => () => Vue.h('select', {}, slots.default?.()) })
  const html = await renderToString(app)
  assert.equal((html.match(/class="active"/g) || []).length, 1)
  assert.match(html, /A&lt;1&gt;/)
  assert.match(html, /原因乙/)
  assert.match(html, /请核对/)
})
