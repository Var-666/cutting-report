import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import { compileScript, compileTemplate, compileStyle, parse } from '@vue/compiler-sfc'
import { scriptFunction } from './typescript-helpers.mjs'
const source = readFileSync(new URL('../src/components/DatabaseSourcesPanel.vue', import.meta.url), 'utf8')
const { descriptor } = parse(source)
const script = descriptor.scriptSetup.content.replace(/^import .*$/gm, '')
const row = { id: 'quality', name: '质量检测', engine: 'sqlserver', enabled: true, host: 'old-host', port: 1433, database: 'quality', username: 'reader', password_configured: true, configured: true, references: [] }
const flush = async () => { await Promise.resolve(); await Vue.nextTick(); await Promise.resolve() }
function mount(overrides = {}) {
  let state
  const calls = { save: [], test: [], delete: [], toggle: [], list: 0 }
  const bindings = { computed: Vue.computed, ref: Vue.ref, watch: Vue.watch, onMounted: Vue.onMounted,
    useConfirm: () => async () => true, useUnsavedChanges() {},
    listDatabaseSources: async () => { calls.list++; return { items: [{ ...row }] } },
    getDatabaseSource: async () => ({ ...row, host: 'latest-host' }),
    saveDatabaseSource: async (...args) => { calls.save.push(args); return row },
    testDatabaseSource: async body => { calls.test.push(body); return { connected: true, elapsed_ms: 8, message: '参数未保存' } },
    setDatabaseSourceEnabled: async (...args) => { calls.toggle.push(args); return row },
    deleteDatabaseSource: async id => { calls.delete.push(id) }, ...overrides }
  const setup = scriptFunction(...Object.keys(bindings), `${script}\nreturn { sources, loading, busy, error, editorError, result, showEditor, dirty, draft, load, open, changeEngine, close, save, testConnection, toggle, remove }`)
  const renderer = Vue.createRenderer({ createComment: () => ({}), createText: () => ({}), createElement: () => ({}), insert() {}, remove() {}, setText() {}, setElementText() {}, parentNode: () => null, nextSibling: () => null, patchProp() {} })
  const app = renderer.createApp({ setup() { state = setup(...Object.values(bindings)); return () => null } })
  app.mount({})
  return { state, calls, app }
}
test('source UI compiles and includes themed narrow, loading, empty and failure states', () => {
  const compiled = compileScript(descriptor, { id: 'sources' })
  assert.deepEqual(compileTemplate({ id: 'sources', filename: 'DatabaseSourcesPanel.vue', source: descriptor.template.content, compilerOptions: { bindingMetadata: compiled.bindings } }).errors, [])
  assert.deepEqual(compileStyle({ id: 'sources', source: descriptor.styles[0].content, scoped: true }).errors, [])
  for (const pattern of [/@media\(max-width:560px\)/, /var\(--text-color/, /#empty/, /role="alert"/, /AppReportTable/, /row.references.length > 0/]) assert.match(source, pattern)
})
test('shared create/edit draft and tests never echo passwords or save automatically', async () => {
  const { app, state, calls } = mount()
  try {
    await flush()
    state.open(); state.changeEngine('mysql')
    assert.equal(state.draft.value.port, 3306)
    assert.equal(state.draft.value.readonly, true)
    state.open(row)
    assert.equal(state.draft.value.password, '')
    state.draft.value.host = 'edited-host'
    await state.testConnection()
    assert.equal(calls.test[0].host, 'edited-host')
    assert.equal(calls.test[0].password, null)
    assert.equal(calls.save.length, 0)
    await state.save()
    assert.equal(calls.save[0][1], false)
    assert.equal(calls.save[0][0].password, null)
    assert.ok(!('password_configured' in calls.save[0][0]))
    assert.equal(state.showEditor.value, false)
    assert.equal(calls.list, 2)
    await state.testConnection(row)
    assert.equal(calls.test[1].host, 'latest-host')
    await state.toggle(row); await state.remove(row)
    assert.deepEqual(calls.toggle, [['quality', false]])
    assert.deepEqual(calls.delete, ['quality'])
  } finally { app.unmount() }
})
test('pending save blocks duplicate actions; failed save preserves the entire draft', async () => {
  let finish; let count = 0
  const page = mount({ saveDatabaseSource: () => { count++; return new Promise(resolve => { finish = resolve }) } })
  try {
    await flush(); page.state.open(row)
    page.state.draft.value.password = 'private-input'
    const saving = page.state.save()
    await page.state.save(); await page.state.testConnection(); await page.state.close()
    assert.equal(count, 1)
    assert.equal(page.state.showEditor.value, true)
    finish(row); await saving
    assert.equal(page.state.draft.value.password, '')
  } finally { page.app.unmount() }
  const failed = mount({ saveDatabaseSource: async () => { throw Error('保存失败') } })
  try {
    await flush(); failed.state.open(row)
    failed.state.draft.value.password = 'private-input'
    failed.state.draft.value.host = 'unsaved-host'
    failed.state.dirty.value = true
    await failed.state.save()
    assert.equal(failed.state.showEditor.value, true)
    assert.equal(failed.state.dirty.value, true)
    assert.equal(failed.state.draft.value.host, 'unsaved-host')
    assert.equal(failed.state.draft.value.password, 'private-input')
    assert.equal(failed.state.editorError.value, '保存失败')
  } finally { failed.app.unmount() }
})
test('load failure and unsaved close are explicit; engine changes invalidate test results', async () => {
  const { app, state } = mount({ listDatabaseSources: async () => { throw Error('读取失败') }, useConfirm: () => async () => false })
  try {
    await flush()
    assert.equal(state.error.value, '读取失败')
    assert.equal(state.loading.value, false)
    state.open(); state.dirty.value = true
    state.result.value = { connected: true }; state.changeEngine('mysql')
    assert.equal(state.result.value, null)
    await state.close()
    assert.equal(state.showEditor.value, true)
  } finally { app.unmount() }
})
