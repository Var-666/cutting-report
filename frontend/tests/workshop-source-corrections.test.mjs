import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import * as Vue from 'vue'
import { parse } from '@vue/compiler-sfc'

const source = readFileSync(new URL('../src/components/WorkshopSourceCorrections.vue', import.meta.url), 'utf8')
const script = parse(source).descriptor.scriptSetup.content.replace(/^import .*$/gm, '')
const plan = { version: 'a'.repeat(64), total_changes: 1, items: [{ task_id: 'A', fields: ['work_time'],
  old: { work_time: '60' }, new: { work_time: '90' }, previous_confirmation: { worker_name: '工人' } }], audits: [] }

function mount({ read = async () => plan, apply = async () => ({ corrected_count: 1, revoked_count: 1 }) } = {}) {
  const props = Vue.reactive({ reportDate: '2026-09-30', active: true })
  let state
  const events = []
  const bindings = { ...Vue, defineProps: () => props, defineEmits: () => (...args) => events.push(args),
    previewWorkshopSourceCorrections: read, applyWorkshopSourceCorrections: apply,
    useDialogFocus: () => {}, formatChineseDateTime: (value) => value }
  const setup = new Function(...Object.keys(bindings), `${script}\nreturn { visible, reportDate, preview, reason, reviewed, loading, saving, error, feedback, open, close, loadPreview, submit }`)
  const renderer = Vue.createRenderer({ createComment: () => ({}), createText: () => ({}), createElement: () => ({}),
    insert() {}, remove() {}, setText() {}, setElementText() {}, parentNode: () => null, nextSibling: () => null, patchProp() {} })
  const app = renderer.createApp({ setup() { state = setup(...Object.values(bindings)); return () => null } })
  app.mount({})
  return { app, state, props, events }
}

test('correction requires a reviewed preview and reason; saves the reviewed version only once', async () => {
  let finish
  const calls = []
  const { app, state, events } = mount({ apply: (payload) => { calls.push(payload); return new Promise((resolve) => { finish = resolve }) } })
  try {
    state.open()
    await state.submit()
    await state.loadPreview()
    state.reason.value = '核对生产记录'
    await state.submit()
    assert.equal(calls.length, 0)
    state.reviewed.value = true
    const pending = state.submit()
    await state.submit()
    state.close()
    assert.equal(state.visible.value, true)
    assert.deepEqual(calls, [{ reportDate: '2026-09-30', expectedVersion: plan.version, reason: '核对生产记录' }])
    finish({ corrected_count: 1, revoked_count: 1 })
    await pending
    assert.equal(state.preview.value, null)
    assert.match(state.feedback.value, /需要重新确认/)
    assert.equal(events.filter(([name]) => name === 'updated').length, 1)
  } finally { app.unmount() }
})

test('conflict preserves reason but invalidates preview and prevents blind retry', async () => {
  let calls = 0
  const { app, state } = mount({ apply: async () => { calls++; throw new Error('源记录已变化') } })
  try {
    state.open()
    await state.loadPreview()
    state.reason.value = '核对后更正'
    state.reviewed.value = true
    await state.submit()
    assert.equal(state.reason.value, '核对后更正')
    assert.equal(state.preview.value, null)
    assert.equal(state.reviewed.value, false)
    assert.match(state.error.value, /重新预览/)
    await state.submit()
    assert.equal(calls, 1)
    await state.loadPreview()
    assert.equal(state.reviewed.value, false)
  } finally { app.unmount() }
})

test('date changes and deactivation abort old previews and ignore delayed responses', async () => {
  let finish, signal
  const { app, state, props } = mount({ read: (date, options) => { signal = options.signal; return new Promise((resolve) => { finish = resolve }) } })
  try {
    state.open()
    const pending = state.loadPreview()
    state.reportDate.value = '2026-10-01'
    await Vue.nextTick()
    assert.equal(signal.aborted, true)
    finish(plan)
    await pending
    assert.equal(state.preview.value, null)
    const inactive = state.loadPreview()
    props.active = false
    await Vue.nextTick()
    assert.equal(signal.aborted, true)
    assert.equal(state.visible.value, false)
    finish(plan)
    await inactive
    assert.equal(state.preview.value, null)
  } finally { app.unmount() }
})

test('unmount ignores delayed mutation completion; reactivation refreshes committed data', async () => {
  let finish
  const { app, state, props, events } = mount({ apply: () => new Promise((resolve) => { finish = resolve }) })
  state.open()
  await state.loadPreview()
  state.reason.value = '核对记录'
  state.reviewed.value = true
  const pending = state.submit()
  props.active = false
  await Vue.nextTick()
  finish({ corrected_count: 1, revoked_count: 1 })
  await pending
  assert.equal(events.length, 0)
  props.active = true
  await Vue.nextTick()
  assert.equal(events.length, 1)
  state.open()
  await state.loadPreview()
  state.reason.value = '再次核对'
  state.reviewed.value = true
  const unmounted = state.submit()
  app.unmount()
  finish({ corrected_count: 1, revoked_count: 1 })
  await unmounted
  assert.equal(events.length, 1)
})
