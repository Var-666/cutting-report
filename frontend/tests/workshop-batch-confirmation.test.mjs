import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'
import { readFileSync } from 'node:fs'
import { parse } from '@vue/compiler-sfc'
import { confirmWorkshopTasks } from '../src/api/reports.js'

const originalFetch = globalThis.fetch
afterEach(() => { globalThis.fetch = originalFetch })

test('batch confirmation sends all selected task ids in one request', async () => {
  const calls = []
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options })
    return { ok: true, json: async () => ({ success: true, data: { confirmed_count: 2, items: [] } }) }
  }

  await confirmWorkshopTasks(['task-1', 'task-2'], '2026-09-11')

  assert.equal(calls.length, 1)
  assert.equal(calls[0].url, '/api/v1/workshop/tasks/confirm')
  assert.equal(calls[0].options.method, 'POST')
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    report_date: '2026-09-11',
    task_ids: ['task-1', 'task-2'],
  })
})

test('worker task table exposes multi-select and reviews tasks before confirmation', () => {
  const descriptor = parse(readFileSync(new URL('../src/views/WorkshopView.vue', import.meta.url), 'utf8')).descriptor
  const script = descriptor.scriptSetup.content
  const template = descriptor.template.content

  assert.match(script, /const selectedTasks = ref\(new Map\(\)\)/)
  assert.match(script, /async function submitBatchConfirmation/)
  assert.match(script, /confirmWorkshopTasks\(tasks\.map\(\(row\) => row\.task_id\)/)
  assert.match(template, /aria-label="全选当前页待确认任务"/)
  assert.match(template, /确认选中任务/)
  assert.match(template, /确认以下任务由我完成/)
  assert.match(template, /v-for="\(row, index\) in batchConfirmation\.tasks"/)
  assert.match(template, /工程\/船号[\s\S]*分段名[\s\S]*程序号[\s\S]*生产时间/)
  assert.match(template, /@click="openSingleConfirmation\(row\)"/)
})
