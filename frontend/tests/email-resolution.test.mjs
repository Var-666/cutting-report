import { scriptFunction } from './typescript-helpers.mjs'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import * as Vue from 'vue'
import { parse } from '@vue/compiler-sfc'

const source = readFileSync(new URL('../src/components/EmailDeliveryResolution.vue', import.meta.url), 'utf8')
const script = parse(source).descriptor.scriptSetup.content.replace(/^import .*$/gm, '')
function mount({ resolve = async () => ({}), list = async () => ({ items: [] }), delivery = {} } = {}) {
  let state
  const events = []
  const bindings = {
    ...Vue, useConfirm: () => async () => false,
    defineProps: () => ({ delivery: { delivery_id: 'd1', status: 'unknown', message_id: '<stable@host>',
      recipients: ['a@example.com', 'b@example.com'], recipient_results: {}, ...delivery } }),
    defineEmits: () => (...args) => events.push(args),
    listEmailDeliveryResolutions: list,
    resolveEmailDelivery: resolve,
    formatChineseDateTime: (value) => value,
  }
  const setup = scriptFunction(...Object.keys(bindings), `${script}\nreturn { outcomes, evidence, submit, error, audits, auditError, saving, resolved }`)
  const renderer = Vue.createRenderer({
    createComment: () => ({}), createText: () => ({}), createElement: () => ({}),
    insert: () => {}, remove: () => {}, setText: () => {}, setElementText: () => {},
    parentNode: () => null, nextSibling: () => null, patchProp: () => {},
  })
  const app = renderer.createApp({ setup() { state = setup(...Object.values(bindings)); return () => null } })
  app.mount({})
  return { app, state, events }
}

test('verification requires every recipient and evidence, and preserves drafts after failure', async () => {
  const submitted = []
  const { app, state } = mount({ resolve: async (...args) => { submitted.push(args); throw new Error('状态冲突') } })
  try {
    await state.submit()
    state.outcomes['a@example.com'] = 'accepted'
    state.evidence.value = '服务器日志编号 123'
    await state.submit()
    assert.equal(submitted.length, 0)
    state.outcomes['b@example.com'] = 'not_accepted'
    await state.submit()
    assert.deepEqual(submitted[0], ['d1', { message_id: '<stable@host>', evidence_note: '服务器日志编号 123',
      outcomes: [{ recipient: 'a@example.com', outcome: 'accepted' }, { recipient: 'b@example.com', outcome: 'not_accepted' }] }])
    assert.equal(state.evidence.value, '服务器日志编号 123')
    assert.equal(state.outcomes['b@example.com'], 'not_accepted')
    assert.equal(state.error.value, '状态冲突')
    assert.equal(state.resolved.value, false)
  } finally { app.unmount() }
})

test('double submission saves once and closes verification without sending', async () => {
  let finish
  let calls = 0
  const { app, state, events } = mount({ resolve: () => { calls++; return new Promise((resolve) => { finish = resolve }) } })
  try {
    Object.assign(state.outcomes, { 'a@example.com': 'accepted', 'b@example.com': 'not_accepted' })
    state.evidence.value = '确认 SMTP 日志'
    const pending = state.submit()
    await state.submit()
    assert.equal(calls, 1)
    finish({ resolved: true })
    await pending
    assert.equal(state.resolved.value, true)
    assert.ok(events.some(([name]) => name === 'resolved'))
    await state.submit()
    assert.equal(calls, 1)
  } finally { app.unmount() }
})

test('unmount aborts audit read and ignores late records and submission completion', async () => {
  let finishAudit, finishSave, signal
  const { app, state, events } = mount({
    list: (id, options) => { signal = options.signal; return new Promise((resolve) => { finishAudit = resolve }) },
    resolve: () => new Promise((resolve) => { finishSave = resolve }),
  })
  Object.assign(state.outcomes, { 'a@example.com': 'accepted', 'b@example.com': 'accepted' })
  state.evidence.value = '已核对'
  const pending = state.submit()
  app.unmount()
  assert.equal(signal.aborted, true)
  finishAudit({ items: [{ resolution_id: 'late' }] })
  finishSave({ resolved: true })
  await pending
  assert.deepEqual(state.audits.value, [])
  assert.equal(events.filter(([name]) => name === 'resolved').length, 0)
})


test('already accepted recipients stay outside verification payload and audit failure remains visible', async () => {
  const submitted = []
  const { app, state } = mount({
    delivery: { recipient_results: { 'a@example.com': { status: 'accepted' } } },
    resolve: async (id, payload) => submitted.push(payload),
    list: async () => { throw new Error('审计暂时不可用') },
  })
  try {
    await Vue.nextTick()
    assert.equal(state.auditError.value, '审计暂时不可用')
    state.outcomes['b@example.com'] = 'accepted'
    state.evidence.value = '服务器确认接收'
    await state.submit()
    assert.deepEqual(submitted[0].outcomes, [{ recipient: 'b@example.com', outcome: 'accepted' }])
    assert.equal(state.resolved.value, true)
    assert.equal(state.auditError.value, '审计暂时不可用', 'audit read failure must not turn a committed verification into failure')
  } finally { app.unmount() }
})
