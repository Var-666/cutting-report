import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'
import { readFileSync } from 'node:fs'
import { compileScript, parse } from '@vue/compiler-sfc'
import * as Vue from 'vue'
import { scriptFunction } from './typescript-helpers.mjs'
import { useCuttingDispatchBatch } from '../src/composables/useCuttingDispatchBatch.ts'
import { cuttingFileKey, batchItemError, hasNameMismatch, batchStatusLabel, isStaleFileError } from '../src/utils/cuttingDispatchBatch.ts'

const originalFetch = globalThis.fetch
afterEach(() => { globalThis.fetch = originalFetch })
const flush = async () => { await Vue.nextTick(); await new Promise(resolve => setImmediate(resolve)) }
const response = data => ({ ok: true, json: async () => ({ success: true, data }) })
const file = (id, name = 'Z806C080A001.gen') => ({ document_id: id, version_id: 'v1', version: 'V1', normalized_path: `files/${id}.gen`, document_no: name })

const modal = { props: ['show', 'maskClosable', 'closeOnEsc'], emits: ['update:show'], setup: (props, { slots }) => () => props.show ? slots.default?.() : null }
const button = { props: ['disabled'], emits: ['click'], setup: (props, { attrs, emit, slots }) => () => Vue.h('button', { ...attrs, disabled: props.disabled, onClick: () => { if (!props.disabled) emit('click') } }, slots.default?.()) }
function input(number = false) {
  return { props: ['value', 'inputProps', 'disabled'], emits: ['update:value'], setup: (props, { attrs, emit }) => () => Vue.h('input', {
    ...attrs, ...props.inputProps, value: props.value, disabled: props.disabled,
    onInput: event => { if (!props.disabled) emit('update:value', number ? event.target.value === '' ? null : Number(event.target.value) : event.target.value) },
  }) }
}
const checkbox = { props: ['checked', 'disabled'], emits: ['update:checked'], setup: (props, { attrs, emit, slots }) => () => Vue.h('label', [Vue.h('input', { ...attrs, type: 'checkbox', checked: props.checked, disabled: props.disabled, onChange: event => { if (!props.disabled) emit('update:checked', event.target.checked) } }), slots.default?.()]) }
const select = { props: ['modelValue', 'disabled'], emits: ['update:modelValue'], setup: (props, { emit, slots }) => () => Vue.h('select', { value: props.modelValue, disabled: props.disabled, onChange: event => emit('update:modelValue', event.target.value) }, slots.default?.()) }
const table = { props: ['columns', 'items'], setup: (props, { slots }) => () => Vue.h('table', [
  Vue.h('thead', Vue.h('tr', props.columns.map(column => Vue.h('th', column.title)))),
  Vue.h('tbody', props.items.map((row, index) => Vue.h('tr', props.columns.map(column => Vue.h('td', slots[column.key]?.({ row, index }))))))
]) }
const bindings = { AppReportTable: table, NProgress: { render: () => Vue.h('progress') }, batchItemError, hasNameMismatch, batchStatusLabel, isStaleFileError }
const descriptor = parse(readFileSync(new URL('../src/components/dispatch/DispatchBatchDialog.vue', import.meta.url), 'utf8')).descriptor
const source = compileScript(descriptor, { id: 'dispatch-batch-dialog-test', inlineTemplate: true }).content
  .replace(/import\s*\{([^}]+)\}\s*from\s*['"]vue['"]/g, (_, names) => {
    for (const name of names.split(',')) {
      const [original, alias = original] = name.trim().split(/\s+as\s+/)
      bindings[alias] = Vue[original]
    }
    return ''
  }).replace(/^import .*$/gm, '').replace('export default', 'return')
const Dialog = scriptFunction(...Object.keys(bindings), source)(...Object.values(bindings))

function mountDialog(t, files = [file('one'), file('two')]) {
  const inputs = { selectedFiles: Vue.ref(new Map(files.map(item => [cuttingFileKey(item), item]))),
    dispatchForm: Vue.reactive({ machineIp: '10.147.1.40' }), systemStatus: Vue.ref({ can_push: true }), operatingId: Vue.ref(''),
    clearSelectedFiles: () => { inputs.selectedFiles.value = new Map() }, loadHistory: async () => {}, refreshVisibleFileStatuses: async () => {},
    loadFiles: async page => { inputs.catalogPage = page }, showNotice() {} }
  const state = useCuttingDispatchBatch(inputs)
  state.openPushBatch()
  const renderer = Vue.createRenderer({
    createElement: tag => ({ tag, props: {}, children: [] }), createComment: text => ({ tag: '#comment', text }), createText: text => ({ tag: '#text', text }),
    insert(node, parent, anchor) { if (node.parent) this.remove(node); node.parent = parent; const index = parent.children.indexOf(anchor); parent.children.splice(index < 0 ? parent.children.length : index, 0, node) },
    remove(node) { if (node.parent) { const siblings = node.parent.children; siblings.splice(siblings.indexOf(node), 1); node.parent = null } },
    setText: (node, text) => { node.text = text }, setElementText: (node, text) => { node.text = text; node.children = [] },
    parentNode: node => node.parent, nextSibling: node => node.parent?.children[node.parent.children.indexOf(node) + 1],
    patchProp: (node, key, previous, next) => { node.props[key] = next },
  })
  const root = { children: [] }
  const app = renderer.createApp({ render: () => Vue.h(Dialog, { pushBatch: state.pushBatch, bulkQuantity: state.bulkQuantity.value, machineIp: inputs.dispatchForm.machineIp,
    machines: [{ machine_ip: '10.147.1.40', online: true }, { machine_ip: '10.147.1.38', online: false }], machineName: ip => ip.endsWith('.40') ? '20kW 切割机' : '30kW 切割机', canPush: inputs.systemStatus.value.can_push,
    'onUpdate:bulkQuantity': value => { state.bulkQuantity.value = value }, 'onUpdate:machineIp': value => { inputs.dispatchForm.machineIp = value },
    onClose: state.closePushBatch, onSubmit: state.submitPushBatch, onApplyQuantity: state.applyBulkQuantity, onParametersChanged: state.invalidateBatchConfirmation,
    onRemove: state.removePushBatchItem, onRetry: state.retryBatchItem, onCheck: state.checkBatchItem, onRefreshCatalog: state.refreshBatchCatalog,
  }) })
  for (const [name, component] of Object.entries({ NModal: modal, NButton: button, NInput: input(), NInputNumber: input(true), NCheckbox: checkbox, AppSelect: select })) app.component(name, component)
  app.mount(root)
  t.after(() => app.unmount())
  const all = node => [node, ...(node.children || []).flatMap(all)]
  const text = node => `${node.text || ''}${(node.children || []).map(text).join('')}`
  const find = predicate => all(root).find(predicate)
  const byText = label => find(node => node.tag === 'button' && text(node) === label)
  return { state, inputs, text: () => text(root), nodes: () => all(root),
    button: byText, field: label => find(node => node.props?.['aria-label'] === label),
    async click(label) { const target = byText(label); assert.ok(target, label); target.props.onClick(); await flush() },
    async fill(label, value) { const target = find(node => node.props?.['aria-label'] === label || node.props?.name === label); assert.ok(target, label); target.props.onInput({ target: { value } }); await flush() },
    async check(name, checked) { const target = find(node => node.tag === 'input' && node.props?.name === name); assert.ok(target, name); target.props.onChange({ target: { checked } }); await flush() },
  }
}

test('batch dialog renders recognition/results separately and edits quantities and machine selection', async t => {
  const dialog = mountDialog(t)
  for (const label of ['文件名', '命名检查', '识别材质', '识别板厚（mm）', '分段名', '参数核对', '派发状态', '任务编号', '处理说明', '20kW 切割机', '30kW 切割机']) assert.ok(dialog.text().includes(label), label)
  assert.ok(dialog.field('Z806C080A001.gen 材质'))
  assert.ok(dialog.field('Z806C080A001.gen 板厚'))
  assert.ok(dialog.field('Z806C080A001.gen 数量'))
  const machine = dialog.nodes().find(node => node.tag === 'select')
  machine.props.onChange({ target: { value: '10.147.1.38' } }); await flush()
  assert.equal(dialog.inputs.dispatchForm.machineIp, '10.147.1.38')
  await dialog.fill('bulk_quantity', '3'); await dialog.click('应用到全部')
  assert.deepEqual(dialog.state.pushBatch.items.map(item => item.quantity), [3, 3])
  await dialog.fill('Z806C080A001.gen 板厚', '9')
  assert.equal(dialog.button('确认并顺序派发').props.disabled, true)
  await dialog.check('standard_override_confirmed', true)
  assert.equal(dialog.button('确认并顺序派发').props.disabled, false)
  await dialog.fill('Z806C080A001.gen 板厚', '10')
  assert.equal(dialog.state.pushBatch.overrideConfirmed, false)
  assert.equal(dialog.button('确认并顺序派发').props.disabled, true)
})

test('manual files require all parameters and explicit review; removing a row updates selected files', async t => {
  const dialog = mountDialog(t, [file('manual', 'custom.gen')])
  assert.equal(dialog.button('确认派发').props.disabled, true)
  await dialog.fill('custom.gen 分段名', '410S'); await dialog.fill('custom.gen 材质', 'B0'); await dialog.fill('custom.gen 板厚', '12')
  assert.equal(dialog.button('确认派发').props.disabled, true)
  await dialog.check('non_standard_confirmed', true)
  assert.equal(dialog.button('确认派发').props.disabled, false)
  await dialog.click('移除')
  assert.equal(dialog.inputs.selectedFiles.value.size, 0)
  assert.equal(dialog.state.pushBatch.visible, false)
})

test('saving locks controls and executes in order, then shows independent dispatch results', async t => {
  const calls = [], pending = []
  globalThis.fetch = async (value, options) => {
    const url = new URL(value, 'http://localhost')
    calls.push({ path: url.pathname, body: JSON.parse(options.body || '{}') })
    if (url.pathname.endsWith('/batch')) return response({ items: calls.at(-1).body.items.map((item, index) => ({ ...item, status: 'created', dispatch: { dispatch_id: `d${index + 1}` } })) })
    if (url.pathname.endsWith('/execute')) return new Promise(resolve => pending.push(() => resolve(response({}))))
    throw new Error(`Unexpected request ${url.pathname}`)
  }
  const dialog = mountDialog(t)
  await dialog.click('确认并顺序派发')
  assert.deepEqual(calls.map(call => call.path), ['/api/v1/cutting/dispatches/batch', '/api/v1/cutting/dispatches/d1/execute'])
  assert.equal(dialog.nodes().find(node => node.props?.['aria-label'] === '关闭批量推送窗口').props.disabled, true)
  await dialog.click('顺序派发中…')
  assert.equal(calls.length, 2)
  pending[0](); await flush()
  assert.equal(calls.at(-1).path, '/api/v1/cutting/dispatches/d2/execute')
  pending[1](); await flush()
  assert.equal(dialog.state.pushBatch.saving, false)
  assert.ok(dialog.text().includes('已推送 2'))
  assert.ok(dialog.text().includes('d1') && dialog.text().includes('d2'))
  assert.equal(dialog.inputs.selectedFiles.value.size, 0)
  await dialog.click('完成')
  assert.equal(dialog.state.pushBatch.visible, false)
})

test('uncertain results expose status checking and only permit an explicitly verified matching retry', async t => {
  const dialog = mountDialog(t, [file('one')])
  const item = dialog.state.pushBatch.items[0]
  Object.assign(item, { status: 'failed', dispatchId: 'existing', error: '上传结果待核对', retryable: false })
  dialog.state.pushBatch.resultsReady = true; await flush()
  assert.equal(dialog.button('重试当前任务'), undefined)
  assert.ok(dialog.text().includes('上传结果待核对的任务须先检查魔盒'))
  let executions = 0
  globalThis.fetch = async (url, options) => {
    if (options.method === 'POST') { executions++; return response({}) }
    return response({ document_id: 'one', version_id: 'v1', relative_path: 'files/one.gen', machine_ip: '10.147.1.40', material: 'A0', thickness: 8, quantity: 1, can_execute: true, status: 'push_failed' })
  }
  await dialog.click('核对状态')
  assert.equal(executions, 0)
  assert.ok(dialog.button('重试当前任务'))
  await dialog.click('重试当前任务')
  assert.equal(executions, 1)
  item.status = 'failed'; item.error = '版本已变化'; await flush()
  assert.equal(dialog.button('重试当前任务'), undefined)
  await dialog.click('刷新清单重选')
  assert.equal(dialog.inputs.catalogPage, 1)
  assert.equal(dialog.state.pushBatch.visible, false)
})
