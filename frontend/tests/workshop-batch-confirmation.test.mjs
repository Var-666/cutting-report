import assert from 'node:assert/strict'
import { afterEach, beforeEach, test } from 'node:test'
import { readFileSync } from 'node:fs'
import { parse } from '@vue/compiler-sfc'
import { confirmWorkshopTasks } from '../src/api/reports.ts'

import { setDeviceCatalog, resetDeviceContext } from '../src/api/deviceContext.ts'

beforeEach(() => setDeviceCatalog({ default_device_id: 'Cutter01', items: [{ id: 'Cutter01' }] }))
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
  const script = descriptor.scriptSetup.content
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
  assert.match(template, /确认以下任务由我完成/)
  assert.match(template, /:items="batchConfirmation\.tasks"/)
  assert.match(script, /工程\/船号[\s\S]*分段名[\s\S]*程序号[\s\S]*生产时间/)
  assert.match(template, /@click="openSingleConfirmation\(row\)"/)
  assert.match(template, /:disabled="loading \|\| correction.saving"/)
  assert.match(template, /:disabled="loading \|\| batchConfirmation.saving"/)
  assert.doesNotMatch(script, /title: [\"']备注[\"']/)
  assert.doesNotMatch(template, /row\.remark/ )
  assert.match(script, /key: 'c8', title: "切割时长", width: 104, align: 'center'/)
  assert.match(template, /<AppReportTable :columns="taskTableColumns"/)
  const shared = parse(readFileSync(new URL('../src/components/AppReportTable.vue', import.meta.url), 'utf8')).descriptor
  const styles = shared.styles.map(style => style.content).join('\n')
  assert.match(styles, /n-data-table-td\)\{[^}]*padding:6px 14px;/)
  assert.match(styles, /border-right-color:color-mix\(in srgb, var\(--n-merged-border-color\) 35%, transparent\)/)
})

test('workshop keeps confirmation corrections while the source correction entry is removed', () => {
  const descriptor = parse(readFileSync(new URL('../src/views/WorkshopView.vue', import.meta.url), 'utf8')).descriptor
  assert.doesNotMatch(descriptor.scriptSetup.content, /WorkshopSourceCorrections/)
  assert.doesNotMatch(descriptor.template.content, /更正日报源记录|WorkshopSourceCorrections/)
  assert.match(descriptor.scriptSetup.content, /await correctWorkshopTask\(row.task_id/)
  assert.match(descriptor.scriptSetup.content, /await revokeWorkshopTask\(row.task_id/)
})
