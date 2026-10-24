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
  const bindings = { computed: Vue.computed, onBeforeUnmount: Vue.onBeforeUnmount, onMounted: Vue.onMounted, ref: Vue.ref, watch: Vue.watch, getConfigurationDiagnostics: fetcher }
  const setup = new Function(...Object.keys(bindings), `${script}\nreturn { report, loading, error, category, search, rows, visibleRows, page, pageSize, totalPages, load, display }`)
  const renderer = Vue.createRenderer({ createComment: () => ({}), createText: () => ({}), createElement: () => ({}), insert() {}, remove() {}, setText() {}, setElementText() {}, parentNode: () => null, nextSibling: () => null, patchProp() {} })
  const app = renderer.createApp({ setup() { state = setup(...Object.values(bindings)); return () => null } })
  app.mount({})
  return { app, state }
}
const flush = async () => { await Promise.resolve(); await Vue.nextTick() }

test('diagnostics is an admin settings child and uses the shared read-only API', () => {
  const router = read('../src/router/index.js')
  assert.match(router, /path: '\/admin'[\s\S]*meta: \{ role: 'admin' \}[\s\S]*path: 'settings'[\s\S]*path: 'configuration-diagnostics'[\s\S]*ConfigurationDiagnosticsView/)
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

test('only abnormal configurations are shown and category and search filters need no additional requests', async () => {
  let calls = 0
  const page = mount(async () => { calls++; return data })
  try {
    await flush()
    assert.deepEqual(page.state.rows.value.map(row => row.key), ['LLM_MAX_CONCURRENT'])
    page.state.category.value = 'AI 模型配置'
    assert.equal(page.state.rows.value.length, 1)
    page.state.search.value = '  llm_max  '
    assert.deepEqual(page.state.rows.value.map(row => row.key), ['LLM_MAX_CONCURRENT'])
    page.state.category.value = '数据库连接配置'
    assert.equal(page.state.rows.value.length, 0)
    assert.equal(calls, 1)
    assert.equal(page.state.display(false), '关闭')
    assert.equal(page.state.display(0), '0')
  } finally { page.app.unmount() }
})

test('abnormal rows paginate after filtering and filters, page size and refresh reset the page', async () => {
  let result = { ...data, items: [
    ...data.items.filter(item => item.status !== 'error'),
    { key: 'OPTIONAL_KEY', category: '数据库连接配置', status: 'unconfigured' },
    ...Array.from({ length: 25 }, (_, index) => ({ key: `ERROR_${index}`, category: index < 12 ? 'AI 模型配置' : '数据库连接配置', status: 'error' })),
  ] }
  const page = mount(async () => result)
  try {
    await flush()
    assert.equal(page.state.rows.value.length, 25)
    assert.equal(page.state.visibleRows.value.length, 10)
    assert.equal(page.state.totalPages.value, 3)
    page.state.page.value = 3
    assert.deepEqual(page.state.visibleRows.value.map(row => row.key), ['ERROR_20', 'ERROR_21', 'ERROR_22', 'ERROR_23', 'ERROR_24'])
    page.state.category.value = 'AI 模型配置'
    await flush()
    assert.equal(page.state.page.value, 1)
    assert.equal(page.state.rows.value.length, 12)
    page.state.page.value = 2
    page.state.search.value = 'ERROR_1'
    await flush()
    assert.equal(page.state.page.value, 1)
    assert.equal(page.state.rows.value.length, 3)
    page.state.category.value = ''
    page.state.search.value = ''
    page.state.pageSize.value = 20
    await flush()
    assert.equal(page.state.visibleRows.value.length, 20)
    assert.equal(page.state.totalPages.value, 2)
    page.state.page.value = 2
    result = { ...data, items: [] }
    await page.state.load()
    await flush()
    assert.equal(page.state.page.value, 1)
    assert.equal(page.state.totalPages.value, 1)
    assert.deepEqual(page.state.visibleRows.value, [])
  } finally { page.app.unmount() }
})

test('diagnostics omits the explanation, aligns cells left and spaces values, sources and badges', () => {
  assert.doesNotMatch(descriptor.template.content, /report\.precedence|report\.notes|生效状态<\/span>|全部状态/)
  assert.match(descriptor.template.content, /v-for="row in visibleRows"/)
  assert.match(descriptor.template.content, /aria-label="异常配置项分页"/)
  assert.match(descriptor.template.content, /class="diagnostic-value"[\s\S]*class="diagnostic-source"[\s\S]*来源：/)
  assert.match(descriptor.template.content, /<td v-for="role[^>]+><div class="diagnostic-cell"><NTag[^>]+>[\s\S]*?<\/NTag><span class="diagnostic-value"/)
  assert.match(descriptor.styles[0].content, /\.diagnostic-cell\{[^}]*gap:12px/)
  assert.match(descriptor.styles[0].content, /@layer legacy\{[^}]*:is\(th,td,strong,small\)\{text-align:left!important/)
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
