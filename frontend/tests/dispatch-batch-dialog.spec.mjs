// @vitest-environment jsdom

import assert from 'node:assert/strict'
import { enableAutoUnmount, flushPromises as flush, mount } from '@vue/test-utils'
import { NInputNumber } from 'naive-ui'
import { afterEach, test, vi } from 'vitest'
import { h, reactive, ref } from 'vue'
import AppSelect from '../src/components/AppSelect.vue'
import DispatchBatchDialog from '../src/components/dispatch/DispatchBatchDialog.vue'
import { useCuttingDispatchBatch } from '../src/composables/useCuttingDispatchBatch'
import { setupSoybeanComponents } from '../src/vendor/soybean-admin/components'
import { cuttingFileKey } from '../src/utils/cuttingDispatchBatch'
import { stubTableLayout } from './table-layout-fixture.mjs'

enableAutoUnmount(afterEach)
const response = data => Response.json({ success: true, data })
const file = (id, name = 'Z806C080A001.gen') => ({ document_id: id, version_id: 'v1', version: 'V1', normalized_path: `files/${id}.gen`, document_no: name })

function mountDialog(files = [file('one'), file('two')]) {
  stubTableLayout()
  const inputs = { selectedFiles: ref(new Map(files.map(item => [cuttingFileKey(item), item]))),
    dispatchForm: reactive({ machineIp: '10.147.1.40' }), systemStatus: ref({ can_push: true }), operatingId: ref(''),
    clearSelectedFiles: () => { inputs.selectedFiles.value = new Map() }, loadHistory: async () => {}, refreshVisibleFileStatuses: async () => {},
    loadFiles: async page => { inputs.catalogPage = page }, showNotice() {},
  }
  let state
  const wrapper = mount({
    setup() {
      state = useCuttingDispatchBatch(inputs)
      state.openPushBatch()
      return () => h(DispatchBatchDialog, { pushBatch: state.pushBatch, bulkQuantity: state.bulkQuantity.value, machineIp: inputs.dispatchForm.machineIp,
        machines: [{ machine_ip: '10.147.1.40', online: true }, { machine_ip: '10.147.1.38', online: false }], machineName: ip => ip.endsWith('.40') ? '20kW 切割机' : '30kW 切割机', canPush: inputs.systemStatus.value.can_push,
        'onUpdate:bulkQuantity': value => { state.bulkQuantity.value = value }, 'onUpdate:machineIp': value => { inputs.dispatchForm.machineIp = value },
        onClose: state.closePushBatch, onSubmit: state.submitPushBatch, onApplyQuantity: state.applyBulkQuantity, onParametersChanged: state.invalidateBatchConfirmation,
        onRemove: state.removePushBatchItem, onRetry: state.retryBatchItem, onCheck: state.checkBatchItem, onRefreshCatalog: state.refreshBatchCatalog,
      })
    },
  }, { attachTo: document.body, global: { plugins: [{ install: setupSoybeanComponents }], components: { AppSelect }, stubs: { teleport: true } } })
  const button = label => wrapper.findAll('button').find(node => node.text() === label)
  const field = label => wrapper.find(`input[aria-label="${label}"]`)
  return {
    state, inputs, wrapper, button, field, text: () => wrapper.text(),
    async click(label) { const target = button(label); assert.ok(target, label); await target.trigger('click'); await flush() },
    async fill(label, value) {
      const number = wrapper.findAllComponents(NInputNumber).find(control => {
        const props = control.props('inputProps')
        return props?.name === label || props?.['aria-label'] === label
      })
      if (number) number.vm.$emit('update:value', Number(value))
      else await wrapper.get(`input[aria-label="${label}"]`).setValue(value)
      await flush()
    },
    async check(label) {
      const control = wrapper.findAll('[role="checkbox"]').find(candidate => candidate.text().includes(label))
      assert.ok(control, label)
      await control.trigger('click')
      await flush()
    },
  }
}

test('batch dialog renders recognition/results separately and edits quantities and machine selection', async () => {
  const dialog = mountDialog()
  assert.equal(dialog.state.pushBatchDirty.value, false)
  for (const label of ['文件名', '命名检查', '识别材质', '识别板厚（mm）', '分段名', '参数核对', '派发状态', '任务编号', '处理说明', '20kW 切割机', '30kW 切割机']) assert.ok(dialog.text().includes(label), label)
  assert.ok(dialog.field('Z806C080A001.gen 材质'))
  assert.ok(dialog.field('Z806C080A001.gen 板厚'))
  assert.ok(dialog.field('Z806C080A001.gen 数量'))
  dialog.wrapper.getComponent(AppSelect).vm.$emit('update:modelValue', '10.147.1.38')
  await flush()
  assert.equal(dialog.inputs.dispatchForm.machineIp, '10.147.1.38')
  await dialog.fill('bulk_quantity', '3'); await dialog.click('应用到全部')
  assert.deepEqual(dialog.state.pushBatch.items.map(item => item.quantity), [3, 3])
  await dialog.fill('Z806C080A001.gen 板厚', '9')
  assert.equal(dialog.button('确认并顺序派发').element.disabled, true)
  await dialog.check('与标准名不一致')
  assert.equal(dialog.button('确认并顺序派发').element.disabled, false)
  await dialog.fill('Z806C080A001.gen 板厚', '10')
  assert.equal(dialog.state.pushBatch.overrideConfirmed, false)
  assert.equal(dialog.button('确认并顺序派发').element.disabled, true)
  assert.equal(dialog.state.pushBatchDirty.value, true)
  dialog.state.closePushBatch()
  dialog.state.openPushBatch()
  await flush()
  assert.equal(dialog.state.pushBatchDirty.value, false)
  assert.equal(dialog.inputs.dispatchForm.machineIp, '10.147.1.40')
  assert.deepEqual(dialog.state.pushBatch.items.map(item => item.quantity), [1, 1])
})

test('manual files require all parameters and explicit review; removing a row updates selected files', async () => {
  const dialog = mountDialog([file('manual', 'custom.gen')])
  assert.equal(dialog.button('确认派发').element.disabled, true)
  await dialog.fill('custom.gen 分段名', '410S'); await dialog.fill('custom.gen 材质', 'B0'); await dialog.fill('custom.gen 板厚', '12')
  assert.equal(dialog.button('确认派发').element.disabled, true)
  await dialog.check('非标准文件名')
  assert.equal(dialog.button('确认派发').element.disabled, false)
  await dialog.click('移除')
  assert.equal(dialog.inputs.selectedFiles.value.size, 0)
  assert.equal(dialog.state.pushBatch.visible, false)
})

test('saving locks controls and executes in order, then shows independent dispatch results', async () => {
  const calls = [], pending = []
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (value, options) => {
    const url = new URL(value, 'http://localhost')
    calls.push({ path: url.pathname, body: JSON.parse(options.body || '{}') })
    if (url.pathname.endsWith('/batch')) return response({ items: calls.at(-1).body.items.map((item, index) => ({ ...item, status: 'created', dispatch: { dispatch_id: `d${index + 1}` } })) })
    if (url.pathname.endsWith('/execute')) return new Promise(resolve => pending.push(() => resolve(response({}))))
    throw new Error(`Unexpected request ${url.pathname}`)
  })
  const dialog = mountDialog()
  await dialog.click('确认并顺序派发')
  assert.deepEqual(calls.map(call => call.path), ['/api/v1/cutting/dispatches/batch', '/api/v1/cutting/dispatches/d1/execute'])
  assert.equal(dialog.wrapper.get('button[aria-label="关闭批量推送窗口"]').element.disabled, true)
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

test('uncertain results expose status checking and only permit an explicitly verified matching retry', async () => {
  const dialog = mountDialog([file('one')])
  const item = dialog.state.pushBatch.items[0]
  Object.assign(item, { status: 'failed', dispatchId: 'existing', error: '上传结果待核对', retryable: false })
  dialog.state.pushBatch.resultsReady = true; await flush()
  assert.equal(dialog.button('重试当前任务'), undefined)
  assert.ok(dialog.text().includes('上传结果待核对的任务须先检查魔盒'))
  let executions = 0
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (url, options) => {
    if (options.method === 'POST') { executions++; return response({}) }
    return response({ document_id: 'one', version_id: 'v1', relative_path: 'files/one.gen', machine_ip: '10.147.1.40', material: 'A0', thickness: 8, quantity: 1, can_execute: true, status: 'push_failed' })
  })
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
