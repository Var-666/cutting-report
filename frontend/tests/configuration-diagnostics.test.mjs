import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import * as Vue from 'vue'
import { compileScript, compileStyle, compileTemplate, parse } from '@vue/compiler-sfc'

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8')
const source = read('../src/views/ConfigurationDiagnosticsView.vue')
const { descriptor } = parse(source)
const script = descriptor.scriptSetup.content.replace(/^import .*$/gm, '')
const data = { categories: ['数据库连接配置', 'AI 模型配置'], items: [
  { key: 'SQLSERVER_HOST', category: '数据库连接配置', status: 'pending' },
  { key: 'LLM_MAX_CONCURRENT', category: 'AI 模型配置', status: 'error' },
  { key: 'LLM_ENABLED', category: 'AI 模型配置', status: 'effective' },
] }

function mount(fetcher) {
  let state
  const bindings = { ...Vue, getConfigurationDiagnostics: fetcher }
  const setup = new Function(...Object.keys(bindings), `${script}\nreturn { report, loading, error, category, search, status, rows, load, display }`)
  const renderer = Vue.createRenderer({ createComment: () => ({}), createText: () => ({}), createElement: () => ({}), insert() {}, remove() {}, setText() {}, setElementText() {}, parentNode: () => null, nextSibling: () => null, patchProp() {} })
  const app = renderer.createApp({ setup() { state = setup(...Object.values(bindings)); return () => null } })
  app.mount({})
  return { app, state }
}
const flush = async () => { await Promise.resolve(); await Vue.nextTick() }

test('diagnostics is an admin settings child and uses the shared read-only API', () => {
  const router = read('../src/router/index.js')
  assert.match(router, /path: '\/admin'[\s\S]*meta: \{ role: 'admin' \}[\s\S]*path: 'settings'[\s\S]*path: 'configuration-diagnostics'[\s\S]*ConfigurationDiagnosticsView/)
  assert.match(read('../src/views/SettingsHomeView.vue'), /\/admin\/settings\/configuration-diagnostics/)
  assert.match(read('../src/api/reports.js'), /getConfigurationDiagnostics[\s\S]*request\('\/api\/v1\/configuration\/diagnostics', \{ signal \}\)/)
  assert.doesNotMatch(script, /fetch\(|method:|saveConfiguration|\.snapshot\(/)
})

test('new view compiles and has themed narrow-screen, loading, empty and failure states', () => {
  const compiled = compileScript(descriptor, { id: 'configuration-diagnostics' })
  assert.deepEqual(compileTemplate({ id: 'configuration-diagnostics', source: descriptor.template.content, filename: 'ConfigurationDiagnosticsView.vue', compilerOptions: { bindingMetadata: compiled.bindings } }).errors, [])
  assert.deepEqual(compileStyle({ id: 'configuration-diagnostics', source: descriptor.styles[0].content, scoped: true }).errors, [])
  assert.match(source, /@media\(max-width:560px\)/)
  assert.match(source, /overflow:auto/)
  assert.match(source, /role="alert"/)
  assert.match(source, /v-if="!rows.length"/)
  assert.match(source, /:aria-busy="loading"/)
})

test('category, search and status filters combine without additional requests', async () => {
  let calls = 0
  const page = mount(async () => { calls++; return data })
  try {
    await flush()
    page.state.category.value = 'AI 模型配置'
    assert.equal(page.state.rows.value.length, 2)
    page.state.search.value = '  llm_max  '
    page.state.status.value = 'error'
    assert.deepEqual(page.state.rows.value.map(row => row.key), ['LLM_MAX_CONCURRENT'])
    page.state.status.value = 'pending'
    assert.equal(page.state.rows.value.length, 0)
    assert.equal(calls, 1)
    assert.equal(page.state.display(false), '关闭')
    assert.equal(page.state.display(0), '0')
  } finally { page.app.unmount() }
})

test('loading starts immediately and errors retain the last result without exposing raw errors', async () => {
  let fail = false
  const page = mount(async () => { if (fail) throw new Error('secret-credentials'); return data })
  try {
    assert.equal(page.state.loading.value, true)
    await flush()
    fail = true
    await page.state.load()
    assert.equal(page.state.loading.value, false)
    assert.equal(page.state.report.value.items.length, 3)
    assert.doesNotMatch(page.state.error.value, /secret/)
    fail = false
    await page.state.load()
    assert.equal(page.state.error.value, '')
  } finally { page.app.unmount() }
})

test('superseded and unmounted responses do not replace the current report', async () => {
  const pending = []
  const page = mount(({ signal }) => new Promise(resolve => pending.push({ signal, resolve })))
  const latest = page.state.load()
  assert.equal(pending[0].signal.aborted, true)
  pending[1].resolve(data)
  await latest
  pending[0].resolve({ items: [] })
  await flush()
  assert.equal(page.state.report.value.items.length, 3)
  const last = page.state.load()
  page.app.unmount()
  assert.equal(pending[2].signal.aborted, true)
  pending[2].resolve({ items: [] })
  await last
  assert.equal(page.state.report.value.items.length, 3)
})
