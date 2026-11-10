import { renderComponent } from './vue-component-helpers.mjs'
import { scriptFunction } from './typescript-helpers.mjs'
import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { parse } from '@vue/compiler-sfc'
import { selectedLineType } from '../src/utils/productionLines.ts'
import * as Vue from 'vue'

function descriptor(name, folder = 'views') {
  const source = readFileSync(new URL(`../src/${folder}/${name}.vue`, import.meta.url), 'utf8')
  return parse(source).descriptor
}

function automationHooks(bindings) {
  return Object.fromEntries(['useAutomationConfiguration', 'useAutomationSchedules', 'useAutomationDeliveries'].map(name => {
    const source = readFileSync(new URL(`../src/composables/${name}.ts`, import.meta.url), 'utf8').replace(/^import .*$/gm, '').replace('export function', 'function')
    return [name, scriptFunction(...Object.keys(bindings), `${source}; return ${name}`)(...Object.values(bindings))]
  }))
}

const flushAutomation = async () => { await new Promise(resolve => setImmediate(resolve)); await Vue.nextTick() }
async function mountAutomation(t, { confirm = async () => true } = {}) {
  const calls = { status: 0, schedules: 0, emails: [], actions: [] }
  let held = false
  const result = (page, total = 60) => ({ items: total ? [{ delivery_id: `page-${page}` }] : [], total, page, page_size: 20 })
  const bindings = {
    ...Vue, useConfirm: () => confirm, useUnsavedChanges() {}, deviceSelection: Vue.reactive({ id: 'Cutter02', items: [{ id: 'Cutter02' }] }),
    getAutomationConfiguration: async () => ({}),
    getAutomationStatus: async () => { calls.status++; return { revision: calls.status } },
    listAutomationSchedules: async () => { calls.schedules++; return { items: [{ schedule_id: 'saved', name: '每日车间报表' }, { schedule_id: 'monthly', name: '月报 Summary' }] } },
    listEmailDeliveries: (query, options) => {
      if (!held) { calls.emails.push({ ...query, signal: options.signal }); return Promise.resolve(result(query.page)) }
      return new Promise((resolve, reject) => calls.emails.push({ ...query, signal: options.signal, resolve: (total = 60) => resolve(result(query.page, total)), reject }))
    },
    saveAutomationSchedule: async () => calls.actions.push('save'), retryEmailDelivery: async () => calls.actions.push('retry'),
    deleteAutomationSchedule: async () => calls.actions.push('delete'), runAutomationSchedule: async () => { calls.actions.push('run'); return { status: 'queued' } },
    saveAutomationConfiguration: async () => { calls.actions.push('config'); return { message: 'saved' } },
  }
  Object.assign(bindings, automationHooks(bindings))
  const code = descriptor('AutomationView').scriptSetup.content.replace(/^import .*$/gm, '')
  const setup = scriptFunction(...Object.keys(bindings), `${code}; return { loadDeliveries, changeDeliveryPage, deliveryPage, deliveries, deliveryTotal, deliveryLoading, deliverySearch, appliedDeliverySearch, searchDeliveries, clearDeliverySearch, scheduleSearch, appliedScheduleSearch, visibleSchedules, searchSchedules, clearScheduleSearch, pageCount, error, loadAll, retry, submit, submitConfiguration, remove, runNow, deliveryResolved, openDelivery, selectedDelivery, resolutionDirty, form }`)
  let state
  const renderer = Vue.createRenderer({ createComment: () => ({}), createText: () => ({}), createElement: () => ({}), insert() {}, remove() {}, setText() {}, setElementText() {}, parentNode: () => null, nextSibling: () => null, patchProp() {} })
  const app = renderer.createApp({ setup() { state = setup(...Object.values(bindings)); return () => null } })
  app.mount({})
  let mounted = true
  const unmount = () => { if (mounted) { mounted = false; app.unmount() } }
  t.after(unmount)
  await flushAutomation()
  return { state, calls, unmount, hold() { held = true } }
}

test('automation search applies task names and keeps delivery search across paging and refresh', async t => {
  const { state, calls } = await mountAutomation(t)
  state.scheduleSearch.value = ' summary '
  state.searchSchedules()
  assert.deepEqual(state.visibleSchedules.value.map(item => item.schedule_id), ['monthly'])
  state.clearScheduleSearch()
  assert.equal(state.visibleSchedules.value.length, 2)
  await state.changeDeliveryPage(2)
  state.deliverySearch.value = ' TARGET@example.com '
  await state.searchDeliveries()
  assert.equal(calls.emails.at(-1).page, 1)
  assert.equal(calls.emails.at(-1).search, 'TARGET@example.com')
  assert.equal(state.appliedDeliverySearch.value, 'TARGET@example.com')
  state.deliverySearch.value = 'not applied'
  await state.changeDeliveryPage(2)
  await state.loadAll()
  assert.equal(calls.emails.at(-1).page, 2)
  assert.equal(calls.emails.at(-1).search, 'TARGET@example.com')
  await state.clearDeliverySearch()
  assert.equal(calls.emails.at(-1).search, '')
  assert.equal(state.deliveryPage.value, 1)
})

test('automation search rejects stale results and preserves the applied query on failure', async t => {
  const { state, calls, hold } = await mountAutomation(t)
  hold()
  state.deliverySearch.value = 'old'
  const old = state.searchDeliveries()
  state.deliverySearch.value = 'new'
  const current = state.searchDeliveries()
  assert.equal(calls.emails.at(-2).signal.aborted, true)
  calls.emails.at(-1).resolve()
  await current
  calls.emails.at(-2).resolve()
  await old
  assert.equal(state.appliedDeliverySearch.value, 'new')
  state.deliverySearch.value = 'failed'
  const failure = state.searchDeliveries()
  calls.emails.at(-1).reject(new Error('search failed'))
  await failure
  assert.equal(state.appliedDeliverySearch.value, 'new')
  assert.match(state.error.value, /search failed/)
})

test('automation recipients appear once and keep accepted recipients after partial refusal', async () => {
  const html = await renderComponent('components/automation/AutomationDeliveryTable', {
    deliveries: [{ delivery_id: 'd1', schedule_name: '日报', subject: '自定义主题', recipients: ['retry@example.com'],
      recipient_results: { 'accepted@example.com': { status: 'accepted' }, 'retry@example.com': { status: 'refused', code: 450 } }, status: 'failed' }],
    deliveryTotal: 1, deliveryPage: 1, deliveryLoading: false, paginate: true, deliveryLabels: { failed: '发送失败' },
  })
  assert.equal((html.match(/retry@example.com/g) || []).length, 1)
  assert.equal((html.match(/accepted@example.com/g) || []).length, 1)
  assert.ok(html.includes('服务器已接受') && html.includes('服务器拒收 450'))
  assert.ok(html.includes('title="自定义主题"'))
  assert.doesNotMatch(html, /<small[^>]*>自定义主题/)
})

test('email paging: latest request wins and page is committed together with records', async t => {
  const { state, calls, hold } = await mountAutomation(t)
  hold()
  const second = state.loadDeliveries(2)
  const third = state.loadDeliveries(3)
  assert.equal(state.deliveryPage.value, 1)
  assert.equal(state.deliveries.value[0].delivery_id, 'page-1')
  assert.equal(calls.emails[1].signal.aborted, true)
  calls.emails[2].resolve(); await third
  calls.emails[1].resolve(); await second
  assert.equal(state.deliveryPage.value, 3)
  assert.equal(state.deliveries.value[0].delivery_id, 'page-3')
  assert.equal(state.deliveryLoading.value, false)
  assert.equal(calls.status, 1)
  assert.equal(calls.schedules, 1)
})

test('email paging: repeated clicks and invalid targets are ignored; failure keeps the applied page', async t => {
  const { state, calls, hold } = await mountAutomation(t)
  hold()
  const pending = state.changeDeliveryPage(2)
  state.changeDeliveryPage(3)
  assert.equal(calls.emails.length, 2)
  calls.emails[1].reject(new Error('email read failed')); await pending
  assert.equal(state.deliveryPage.value, 1)
  assert.equal(state.deliveries.value[0].delivery_id, 'page-1')
  assert.match(state.error.value, /email read failed/)
  state.changeDeliveryPage(0); state.changeDeliveryPage(4); state.changeDeliveryPage(1)
  assert.equal(calls.emails.length, 2)
})

test('email paging: a shrinking final page is re-read at a valid page, including zero records', async t => {
  const { state, calls, hold } = await mountAutomation(t)
  await state.changeDeliveryPage(3)
  hold()
  const pending = state.loadDeliveries()
  calls.emails[2].resolve(21)
  await flushAutomation()
  assert.equal(calls.emails[3].page, 2)
  assert.equal(state.deliveryPage.value, 3, 'keep the valid old page until replacement succeeds')
  calls.emails[3].resolve(0)
  await flushAutomation()
  assert.equal(calls.emails[4].page, 1)
  calls.emails[4].resolve(0); await pending
  assert.equal(state.deliveryPage.value, 1)
  assert.equal(state.pageCount.value, 1)
  assert.deepEqual(state.deliveries.value, [])
})

test('email paging: unmount cancels reads and ignores late failures', async t => {
  const { state, calls, hold, unmount } = await mountAutomation(t)
  hold()
  const pending = state.loadDeliveries(2)
  unmount()
  assert.equal(calls.emails[1].signal.aborted, true)
  calls.emails[1].reject(new Error('late')); await pending
  assert.equal(state.deliveryPage.value, 1)
  assert.equal(state.error.value, '')
})

test('automation mutations still refresh status, schedules and current email page', async t => {
  const previous = globalThis.window
  globalThis.window = { confirm: () => true }
  t.after(() => { if (previous === undefined) delete globalThis.window; else globalThis.window = previous })
  const { state, calls } = await mountAutomation(t)
  await state.changeDeliveryPage(2)
  await state.retry({ delivery_id: 'failed' })
  await state.submit()
  await state.submitConfiguration()
  await state.runNow({ schedule_id: 'saved', name: 'saved' })
  await state.remove({ schedule_id: 'saved', name: 'saved' })
  await state.deliveryResolved()
  assert.deepEqual(calls.actions, ['retry', 'save', 'config', 'run', 'delete'])
  assert.equal(calls.status, 7)
  assert.equal(calls.schedules, 7)
  assert.ok(calls.emails.slice(1).every(call => call.page === 2))
})

test('settings entry redirects to database and omits the repeated overview page', () => {
  const router = readFileSync(new URL('../src/router/index.ts', import.meta.url), 'utf8')
  assert.match(router, /path: '',\s*name: 'settings',\s*redirect: '\/admin\/settings\/database'/)
  assert.doesNotMatch(router, /SettingsHomeView/)
})

test('management hero renders its public status and optional description safely', async () => {
  const props = { eyebrow: 'DATABASE', title: '数据库配置', statusValue: '运行中',
    statusLabel: '数据库状态', statusNote: '<script>unsafe</script>' }
  const html = await renderComponent('components/ManagementPageHero', props)
  for (const value of ['数据库配置', '数据库状态', '运行中', '&lt;script&gt;unsafe&lt;/script&gt;']) assert.ok(html.includes(value))
  assert.ok(!html.includes('<script>unsafe</script>'))
  assert.ok((await renderComponent('components/ManagementPageHero', { ...props, description: '只读连接' })).includes('只读连接'))
})

test('login uses the supplied illustration without cropping and keeps the authentication form', () => {
  const login = descriptor('LoginView')
  assert.match(login.scriptSetup.content, /import loginIllustration from '..\/assets\/login-illustration\.png'/)
  const image = readFileSync(new URL('../src/assets/login-illustration.png', import.meta.url))
  assert.equal(image.subarray(1, 4).toString(), 'PNG')
  assert.match(login.template.content, /class="login-scene__illustration" :src="loginIllustration" alt="[^"]+"/)
  assert.doesNotMatch(login.template.content, /login-scene__copy|数据驱动生产|<ul>/)
  const styles = readFileSync(new URL('../src/assets/style.css', import.meta.url), 'utf8')
  assert.match(styles, /\.login-scene__illustration\{[^}]*object-fit:contain/)
  assert.match(styles, /@media\(max-width:900px\)\{[^\n]*\.login-scene__illustration\{height:auto;max-height:260px\}/)
  assert.match(login.template.content, /class="login-scene"/)
  assert.match(login.template.content, /@submit\.prevent="submit"/)
})

test('database source selector offers SQL Server and demo without the removed Excel source', () => {
  const database = descriptor('DatabaseView')
  const selector = database.template.content.match(/<AppSelect[^>]*name="data_source_mode"[^>]*>(.*?)<\/AppSelect>/s)[1]
  assert.deepEqual([...selector.matchAll(/<option value="([^"]+)"/g)].map((match) => match[1]), ['demo', 'sqlserver'])
})

test('database connection table edits isolated drafts and preserves SQL and welding save contracts', async () => {
  const connection = { host: 'saved-host', port: 1433, database: 'production', username: 'reader', password_configured: true,
    driver: 'ODBC Driver 18 for SQL Server', encrypt: true, trust_server_certificate: false }
  const current = { data_source_mode: 'demo', worker_database_auth_enabled: false, file_database_enabled: false,
    production: { ...connection }, worker: { ...connection }, file: { ...connection },
    welding: { enabled: false, host: 'welding-host', port: 3306, database: 'weld', username: 'reader', password_configured: true, timezone: 'Asia/Shanghai' } }
  const selection = Vue.reactive({ id: 'Cutter01', items: [{ id: 'Cutter01', type: 'cutting' }, { id: 'welding', type: 'welding' }] })
  const calls = { sql: [], welding: [] }
  let permitClose = false
  let failSave = false
  let guard
  const bindings = { ...Vue, onMounted() {}, onBeforeUnmount() {}, useUnsavedChanges() {},
    registerDeviceSwitchGuard: (_name, handler) => { guard = handler; return () => {} },
    useConfirm: () => async () => permitClose, selectedLineType, formatTableDateValue: String,
    deviceSelection: selection, loadDevices: async () => {},
    getDatabaseConfiguration: async () => structuredClone(current),
    getDatabaseStatus: async () => ({ mode: 'demo', driver: connection.driver }),
    saveDatabaseConfiguration: async body => { calls.sql.push(structuredClone(body)); if (failSave) throw Error('保存失败'); return { message: '已保存' } },
    saveWeldingConfiguration: async body => { calls.welding.push(structuredClone(body)); return { message: '已保存' } },
  }
  const script = descriptor('DatabaseView').scriptSetup.content.replace(/^import[\s\S]*?from ['"][^'"]+['"]\s*$/gm, '')
  const page = scriptFunction(...Object.keys(bindings), `${script}; return { databaseConfiguration, configurationError, savedConnections, editor, editorDirty, editConnection, closeEditor, saveEditor, loadConfiguration }`)(...Object.values(bindings))
  await page.loadConfiguration()
  assert.deepEqual(page.savedConnections.value.map(row => row.label), ['生产报表库', '工人 HR 库', '切割文件库', '小组立焊接线'])
  page.editConnection('production')
  assert.equal(page.editor.value.profile.password, '')
  page.editor.value.profile.host = 'unsaved-host'
  page.editorDirty.value = true
  assert.equal(page.databaseConfiguration.value.production.host, 'saved-host')
  assert.equal(await guard(), false)
  await page.closeEditor()
  assert.ok(page.editor.value)
  permitClose = true
  await page.closeEditor()
  assert.equal(page.editor.value, null)
  assert.equal(calls.sql.length, 0)
  page.editConnection('worker')
  page.editor.value.profile.host = 'edited-worker'
  await page.saveEditor()
  assert.equal(calls.sql[0].worker.host, 'edited-worker')
  assert.equal(calls.sql[0].production.host, 'saved-host')
  assert.equal(calls.sql[0].worker.password, null)
  assert.equal(page.editor.value, null)
  page.editConnection('welding')
  page.editor.value.profile.timezone = 'UTC'
  page.editor.value.profile.password = 'new-private'
  await page.saveEditor()
  assert.equal(calls.welding[0].timezone, 'UTC')
  assert.equal(calls.welding[0].password, 'new-private')
  assert.equal(calls.sql.length, 1)
  assert.equal(page.editor.value, null)
  failSave = true
  page.editConnection('production')
  page.editor.value.profile.host = 'failed-host'
  page.editorDirty.value = true
  await page.saveEditor()
  assert.equal(page.editor.value.profile.host, 'failed-host')
  assert.equal(page.databaseConfiguration.value.production.host, 'saved-host')
  assert.equal(page.configurationError.value, '保存失败')
  await page.closeEditor()
  selection.id = 'welding'
  await Vue.nextTick()
  assert.deepEqual(page.savedConnections.value.map(row => row.engine), ['MySQL'])
})

test('database save reloads active settings and status without a restart prompt', async () => {
  const { computed, reactive, ref } = await import('vue')
  const view = descriptor('DatabaseView')
  const calls = []
  const connection = { host: 'new-db', port: 1433, database: 'production', username: 'reader', password_configured: true,
    driver: 'ODBC Driver 18 for SQL Server', encrypt: true, trust_server_certificate: false }
  const current = { data_source_mode: 'sqlserver', worker_database_auth_enabled: true, file_database_enabled: true,
    production: { ...connection }, worker: { ...connection }, file: { ...connection } }
  const bindings = { computed, reactive, ref, watch() {}, onMounted() {}, onBeforeUnmount() {}, registerDeviceSwitchGuard: () => () => {}, useConfirm: () => async () => true, useUnsavedChanges() {}, formatTableDateValue: String,
    deviceSelection: { id: 'Cutter01' }, loadDevices: async () => { calls.push('catalog') },
    getDatabaseStatus: async () => { calls.push('status'); return { mode: 'sqlserver', driver: connection.driver } },
    getDatabaseConfiguration: async () => { calls.push('configuration'); return structuredClone(current) },
    saveDatabaseConfiguration: async (body) => { calls.push(body); return { saved: true, restart_required: false, message: '配置已保存并热加载' } },
  }
  const script = view.scriptSetup.content.replace(/^import[\s\S]*?from ['"][^'"]+['"]\s*$/gm, '')
  const page = scriptFunction(...Object.keys(bindings), `${script}\nreturn { databaseConfiguration, configurationSaving, configurationResult, configurationError, hasUnsavedChanges, saveConfiguration }`)(...Object.values(bindings))
  page.databaseConfiguration.value = structuredClone(current)
  for (const source of ['production', 'worker', 'file']) page.databaseConfiguration.value[source].password = 'new-private'
  page.hasUnsavedChanges.value = true
  await page.saveConfiguration()
  assert.equal(page.configurationResult.value.restart_required, false)
  assert.equal(page.configurationSaving.value, false)
  assert.equal(page.configurationError.value, '')
  assert.equal(page.hasUnsavedChanges.value, false)
  assert.equal(calls[0].production.password, 'new-private')
  assert.deepEqual(calls.slice(1).sort(), ['catalog', 'configuration', 'status'])
  assert.ok(['production', 'worker', 'file'].every(source => page.databaseConfiguration.value[source].password === ''))
  assert.match(view.template.content, /保存后自动热加载 · 无需重启容器/)
  assert.doesNotMatch(view.template.content, /重启服务后生效/)
})

for (const method of ['saveDatabaseConfiguration', 'saveWeldingConfiguration', 'saveDatabaseSource', 'setDatabaseSourceEnabled', 'deleteDatabaseSource']) {
  test(`successful ${method} clears report reads and a failed save preserves them`, async () => {
    const { reportCache } = await import('../src/api/reportCache.ts')
    const configuration = await import('../src/api/configuration.ts')
    const mutate = () => method === 'saveDatabaseSource' ? configuration[method]({ id: 'cutting-production' }, false)
      : method === 'setDatabaseSourceEnabled' ? configuration[method]('cutting-production', true)
      : method === 'deleteDatabaseSource' ? configuration[method]('quality') : configuration[method]({})
    const originalFetch = globalThis.fetch
    try {
      await reportCache.read('database-runtime-test', () => 'old')
      globalThis.fetch = async () => ({ ok: false, json: async () => ({ detail: '保存失败' }) })
      await assert.rejects(mutate(), /保存失败/)
      assert.equal(await reportCache.read('database-runtime-test', () => 'unexpected'), 'old')
      globalThis.fetch = async () => ({ ok: true, json: async () => ({ success: true, data: { restart_required: false } }) })
      await mutate()
      assert.equal(await reportCache.read('database-runtime-test', () => 'new'), 'new')
    } finally { globalThis.fetch = originalFetch; reportCache.clear() }
  })
}

for (const fail of [false, true]) {
  test(`late database configuration ${fail ? 'failure' : 'response'} cannot overwrite another line`, async () => {
    const { computed, reactive, ref } = await import('vue')
    let settle
    const selection = { id: 'Cutter01' }
    const bindings = { computed, reactive, ref, watch() {}, onMounted() {}, onBeforeUnmount() {}, registerDeviceSwitchGuard: () => () => {}, useConfirm: () => async () => true, useUnsavedChanges() {}, formatTableDateValue: String,
      deviceSelection: selection, getDatabaseConfiguration: () => new Promise((resolve, reject) => { settle = fail ? reject : resolve }) }
    const script = descriptor('DatabaseView').scriptSetup.content.replace(/^import[\s\S]*?from ['"][^'"]+['"]\s*$/gm, '')
    const page = scriptFunction(...Object.keys(bindings), `${script}\nreturn { databaseConfiguration, configurationError, configurationLoading, loadConfiguration }`)(...Object.values(bindings))
    const reading = page.loadConfiguration()
    selection.id = 'Cutter02'
    page.databaseConfiguration.value = { selected: 'Cutter02' }
    page.configurationError.value = 'current-error'
    settle(fail ? Error('old-error') : { production: {}, worker: {}, file: {} })
    await reading
    assert.deepEqual(page.databaseConfiguration.value, { selected: 'Cutter02' })
    assert.equal(page.configurationError.value, 'current-error')
    assert.equal(page.configurationLoading.value, true)
  })
}

test('database configuration retains sections 01–03 and omits mapping, preview and SQL workbench', () => {
  const database = descriptor('DatabaseView')
  const template = database.template.content
  assert.deepEqual([...template.matchAll(/<div class="panel-title"><div><span>(\d+)<\/span>/g)].map(match => match[1]), ['01', '02', '03'])
  assert.match(template, /@submit.prevent="saveConfiguration\(weldingScope\)"/)
  assert.match(template, /@submit.prevent="runConnectionTest"/)
  assert.match(template, /aria-label="数据库状态"/)
  assert.doesNotMatch(template, /生产查询映射|固定数据集预览|SQL 工作台|mapping-panel|preview-panel|sql-panel/)
  assert.doesNotMatch(database.scriptSetup.content, /previewDatabaseDataset|executeWorkbenchSql|validateWorkbenchSql|runPreview|setSqlExample|previewResult|sqlResult|query_mappings/)
})

test('shared connection test selects SQL Server or MySQL without saving either draft', async () => {
  const { computed, reactive, ref } = await import('vue')
  const view = descriptor('DatabaseView')
  const calls = []
  const selection = reactive({ id: 'Cutter01', items: [{ id: 'Cutter01', type: 'cutting' }, { id: 'welding', type: 'welding', source_key: 'welding' }] })
  const bindings = { computed, reactive, ref, watch() {}, onMounted() {}, onBeforeUnmount() {}, registerDeviceSwitchGuard: () => () => {}, useConfirm: () => async () => true, useUnsavedChanges() {}, formatTableDateValue: String,
    deviceSelection: selection, selectedLineType,
    testDatabaseConnection: async body => { calls.push(['sqlserver', body]); return { database: body.database, elapsed_ms: 1 } },
    testWeldingConnection: async body => { calls.push(['welding', body]); return { connected: true, elapsed_ms: 2 } },
  }
  const script = view.scriptSetup.content.replace(/^import[\s\S]*?from ['"][^'"]+['"]\s*$/gm, '')
  const page = scriptFunction(...Object.keys(bindings), `${script}\nreturn { connection, weldingConnection, testSource, runConnectionTest, testResult, testError, testing, databaseConfiguration, dataSources, status, hasUnsavedChanges }`)(...Object.values(bindings))
  Object.assign(page.connection, { host: 'sql.test', database: 'cutting', username: 'reader', password: 'test-sql-only' })
  await page.runConnectionTest()
  assert.equal(calls[0][0], 'sqlserver')
  page.databaseConfiguration.value = { welding: { host: 'saved.test', database: 'saved-weld', enabled: false } }
  Object.assign(page.weldingConnection, { host: 'mysql.test', database: 'welding', username: 'reader' })
  page.testSource.value = 'welding'
  await page.runConnectionTest()
  assert.equal(calls[1][0], 'welding')
  assert.equal(calls[1][1].password, null)
  assert.equal(calls[1][1].port, 3306)
  assert.equal(page.testResult.value.database, 'welding')
  assert.equal(page.databaseConfiguration.value.welding.host, 'saved.test')
  assert.equal(page.databaseConfiguration.value.welding.enabled, false)
  assert.equal(page.hasUnsavedChanges.value, false)
  assert.equal(page.testing.value, false)
  page.status.value = { data_sources: [{ source: 'production' }, { source: 'welding' }] }
  assert.equal(page.dataSources.value.length, 2)
  selection.id = 'welding'
  page.testSource.value = 'sqlserver'
  assert.deepEqual(page.dataSources.value, [{ source: 'welding' }])
  await page.runConnectionTest()
  assert.equal(calls[2][0], 'welding')
  const testSection = view.template.content.slice(view.template.content.indexOf('class="panel db-panel"'))
  assert.match(testSection, /name="test_source"[\s\S]*value="sqlserver"[\s\S]*value="welding"/)
  assert.match(testSection, /v-model:value="activeTestConnection.host"/)
  assert.match(testSection, /v-if="testIsWelding"[\s\S]*name="test_timezone"/)
})

for (const [reportType, period] of [['leadership', 'current_month'], ['leadership', 'previous_month'], ['workshop', 'current_day'], ['equipment', 'previous_day']]) {
  test(`editing ${reportType}/${period} preserves the stored period through a name-only save`, async () => {
    const Vue = await import('vue')
    const saved = []
    const scope = Vue.effectScope()
    const script = descriptor('AutomationView').scriptSetup.content.replace(/^import .*$/gm, '')
    const bindings = {
      ...Vue, onMounted() {}, onBeforeUnmount() {}, useConfirm: () => async () => true, useUnsavedChanges() {}, deviceSelection: Vue.reactive({ id: 'Cutter02' }),
      saveAutomationSchedule: async (body, id) => saved.push({ body, id }),
      getAutomationStatus: async () => ({}), listAutomationSchedules: async () => ({ items: [] }),
      listEmailDeliveries: async () => ({ items: [], total: 0, page: 1, page_size: 20 }),
    }
    Object.assign(bindings, automationHooks(bindings))
    const page = scope.run(() => scriptFunction(...Object.keys(bindings), `${script}\nreturn { form, editSchedule, changeReportType, submit, hasUnsavedChanges }`)(...Object.values(bindings)))
    try {
      const item = { schedule_id: 'schedule-1', device_id: 'Cutter02', name: 'Saved plan', report_type: reportType, report_period: period, frequency: 'weekly', run_time: '16:30', weekday: 2, month_day: null, recipients: ['worker@example.com'], subject_template: '{report_name}', chart_type: 'line', attach_report: true, max_attempts: 3, enabled: true }
      // Make every case cross a type boundary before editing, then flush real watchers.
      page.form.report_type = reportType === 'leadership' ? 'workshop' : 'leadership'
      await Vue.nextTick()
      page.editSchedule(item)
      await Vue.nextTick()
      assert.equal(page.form.report_period, period)
      page.form.name = 'Renamed plan'
      await Vue.nextTick()
      await page.submit()
      assert.equal(saved.length, 1)
      assert.equal(saved[0].id, item.schedule_id)
      assert.deepEqual(saved[0].body, {
        device_id: item.device_id, name: 'Renamed plan', report_type: reportType, report_period: period,
        frequency: 'weekly', run_time: item.run_time, weekday: 2, month_day: null,
        recipients: item.recipients, subject_template: item.subject_template, chart_type: item.chart_type,
        attach_report: true, max_attempts: 3, enabled: true,
      })
      for (const [type, current, expected] of [
        ['equipment', 'current_day', 'current_day'], ['workshop', 'previous_day', 'previous_day'],
        ['leadership', 'current_month', 'current_month'], ['leadership', 'previous_month', 'previous_month'],
        ['leadership', 'current_day', 'previous_month'], ['workshop', 'current_month', 'previous_day'],
      ]) {
        Object.assign(page.form, { report_type: type, report_period: current })
        page.changeReportType()
        await Vue.nextTick()
        assert.equal(page.form.report_period, expected)
        assert.equal(page.hasUnsavedChanges.value, true)
      }
    } finally { scope.stop() }
  })
}

test('all seven migrated settings tables render their fields and controls through Naive UI', async () => {
  const [{ compile, parse: parseTemplate }, { renderToString }, { setup: setupCssr }, { SharedDataTable }, dateDisplay] = await Promise.all([
    import('@vue/compiler-dom'), import('@vue/server-renderer'), import('@css-render/vue3-ssr'), import('./data-table-helpers.mjs'), import('../src/utils/dateDisplay.ts'),
  ])
  const { NTag, NButton } = await import('naive-ui')
  const cases = [
    ['AdminAccountsView', 'adminTableColumns', { admins: [
      { user_id: 'self', display_name: '本人', group_no: 'G001', company_no: '001', auth_source: 'hr_database' },
      { user_id: 'other', display_name: '同事', group_no: 'G002', company_no: '002', auth_source: 'hr_database' },
      { user_id: 'local', display_name: '应急管理员', username: 'admin', auth_source: 'local' },
    ], session: { user: { user_id: 'self' } }, formatTime: () => '2026年10月28日', openDialog() {} }, ['本人', '同事', 'G001', 'G002', '应急管理员', 'admin', 'HR 数据库', '本地应急账号', '受保护']],
    ...[1, 2].map(count => {
      const consumers = Object.fromEntries(Array.from({ length: count }, (_, index) => [`consumer-${index + 1}`, {
        status: 'pending', value: `consumer-${index + 1}-host<script>unsafe</script>`, source: `Consumer ${index + 1}快照`,
      }]))
      return ['ConfigurationDiagnosticsView', 'diagnosticsTableColumns', {
        roles: ['web', 'worker', 'scheduler', 'automation'],
        visibleRows: [{ key: 'TEST_HOST', category: '数据库', saved_value: false, saved_source: 'settings.env', status: 'error', rule: '需要重启', valid: false, process_mismatch: true, processes: {
          web: { status: 'error', value: 'web-host', source: 'Web快照' },
          worker: { status: 'pending', value: 'aggregate-host', source: '聚合快照' },
          scheduler: { status: 'pending', value: 'scheduler-host', source: 'Scheduler快照' },
          automation: { status: 'pending', value: 'automation-host', source: 'Automation快照' }, consumers,
        } }], rows: Array(21), page: 2, pageSize: 10, category: '', search: '', display: value => String(value),
        tones: { error: 'error', pending: 'warning' }, statuses: { error: '配置异常', pending: '待重启' },
      }, ['TEST_HOST', 'web-host', 'Web快照', 'scheduler-host', 'Scheduler快照', 'automation-host', 'Automation快照',
        ...Object.keys(consumers).flatMap((id, index) => [`${id}-host&lt;script&gt;unsafe&lt;/script&gt;`, `Consumer ${index + 1}快照`]),
        'settings.env', '需要重启', '后台进程与 Web 配置不一致']]
    }),
    ['ModelUsageView', 'periodTableColumns', { periods: [{ period: '2026-10-28', call_count: 1240, input_tokens: null, output_tokens: 0, total_tokens: 0, metered_count: 1200, failed_count: 40 }], report: { granularity: 'day' }, number: value => value == null ? '—' : value.toLocaleString('zh-CN') }, ['2026年10月28日', '1,240', '1,200', '—', '40']],
    ['ModelUsageView', 'breakdownTableColumns', { report: { breakdown: [{ scene: 'workshop', model: '<script>unsafe</script>', call_count: 10, input_tokens: 0, output_tokens: null, total_tokens: 0, metered_count: 8 }] }, sceneName: () => '车间报表', number: value => value == null ? '—' : String(value) }, ['车间报表', '&lt;script&gt;unsafe&lt;/script&gt;', '—']],
    ['AutomationView', 'scheduleTableColumns', { schedules: [{ schedule_id: 'schedule', enabled: true, name: '每日车间', device_id: 'Cutter01', report_type: 'workshop', frequency: 'daily', run_time: '18:00', recipients: ['one@example.com'], sent_count: 12, failed_count: 1 }], reportLabels: { workshop: '车间报表' }, scheduleLabels: {} }, ['每日车间', '20KW切割机', '每日 18:00', '立即执行', '编辑', '删除']],
    ['AutomationView', 'deliveryTableColumns', { deliveries: [
      { delivery_id: 'failed', schedule_name: '邮件失败', recipients: ['one@example.com'], recipient_results: {}, status: 'failed', attempt_count: 1, max_attempts: 3, last_error: '连接失败' },
      { delivery_id: 'unknown', schedule_name: '邮件待核对', recipients: ['two@example.com'], recipient_results: { 'two@example.com': { status: 'unknown' } }, status: 'unknown', message_id: '<mail-id>' },
    ], status: {}, deliveryTotal: 41, deliveryPage: 2, deliveryLoading: false, deliveryLabels: { failed: '发送失败', unknown: '结果待核对' } }, ['one@example.com', 'two@example.com', '连接失败', '重新发送', '核对记录', '核对结果', '结果待核对']],
    ['DesignerView', 'previewTableColumns', { queryPreview: Vue.ref({ columns: [{ field: 'name', label: '名称', type: 'text' }, { field: 'date', label: '日期', type: 'date' }, { field: 'time', label: '时间', type: 'datetime' }, { field: 'value', label: '数值', type: 'number' }, { field: 'duration', label: '时长', type: 'duration' }], items: [{ name: '<b>工件</b>', date: '2026-10-28', time: '2026-10-28T08:00:00', value: 0, duration: 12.5 }] }) }, ['&lt;b&gt;工件&lt;/b&gt;', '2026年10月28日', '08:00:00', '12.5']],
  ]
  for (const [viewName, columnName, state, expected] of cases) {
    if (viewName === 'AutomationView') {
      const name = columnName === 'scheduleTableColumns' ? 'AutomationScheduleTable' : 'AutomationDeliveryTable'
      const html = await renderComponent(`components/automation/${name}`, { ...state, loading: false, paginate: true })
      for (const value of expected) assert.ok(html.includes(value), `${name} shows ${value}`)
      assert.doesNotMatch(html, /max-height:(340|440)px/, 'automation tables grow with the page')
      continue
    }
    const view = viewName === 'AutomationView'
      ? descriptor(`automation/${columnName === 'scheduleTableColumns' ? 'AutomationScheduleTable' : 'AutomationDeliveryTable'}`, 'components')
      : viewName === 'DesignerView' ? descriptor('designer/DesignerQueryPreview', 'components')
      : descriptor(viewName)
    const script = view.scriptSetup.content
    const bindings = { ...Vue, ...dateDisplay, ...state, loading: false, paginate: true }
    if (viewName === 'DesignerView') {
      bindings.props = { queryPreview: state.queryPreview.value }
      bindings.formatPreviewValue = scriptFunction(...Object.keys(bindings), `${script.slice(script.indexOf('function formatPreviewValue'), script.indexOf('const previewTableColumns'))}; return formatPreviewValue`)(...Object.values(bindings))
    }
    const columns = scriptFunction(...Object.keys(bindings), `${script.slice(script.indexOf(`const ${columnName} =`))}; return ${columnName}`)(...Object.values(bindings))
    const nodes = []
    function collect(node) { if (node.tag === 'AppReportTable') nodes.push(node); for (const child of node.children || []) collect(child) }
    collect(parseTemplate(view.template.content))
    const table = nodes.find(node => node.loc.source.includes(`:columns="${columnName}"`))
    assert.ok(table, `${viewName} table ${columnName} is mounted`)
    const render = scriptFunction('Vue', compile(table.loc.source, { prefixIdentifiers: true, expressionPlugins: ['typescript'] }).code)(Vue)
    const app = Vue.createSSRApp({ setup: () => ({ ...bindings, [columnName]: columns }), render })
    app.component('AppReportTable', SharedDataTable)
    app.component('NTag', NTag)
    app.component('NButton', NButton)
    setupCssr(app)
    const html = await renderToString(app)
    assert.match(html, /n-data-table/)
    for (const value of expected) assert.ok(html.includes(value), `${viewName} shows ${value}`)
    if (viewName === 'AdminAccountsView') assert.match(html, /disabled title="不能撤销当前登录账号自己的权限"/)
    if (columnName === 'diagnosticsTableColumns' || columnName === 'deliveryTableColumns') {
      assert.match(html, /class="n-pagination(?:\s|")/)
      assert.match(html, /第 3 页/)
    } else assert.doesNotMatch(html, /class="n-pagination(?:\s|")/)
    if (['diagnosticsTableColumns', 'breakdownTableColumns'].includes(columnName)) assert.doesNotMatch(html, /<script>unsafe/)
    if (columnName === 'diagnosticsTableColumns') assert.ok(!html.includes('aggregate-host'), 'render the actual Consumer instances')
    const height = { periodTableColumns: 330, scheduleTableColumns: 340, deliveryTableColumns: 440 }[columnName]
    if (height) assert.ok(html.includes(`max-height:${height}px`), 'scroll height is handled by NDataTable')
  }
})

test('automation delivery review preserves a dirty draft when switching is cancelled and rebinds refreshed records', async t => {
  let accepted = false
  const { state } = await mountAutomation(t, { confirm: async () => accepted })
  const original = { delivery_id: 'page-1', subject: '待核对记录' }
  await state.openDelivery(original)
  state.resolutionDirty.value = true
  await state.openDelivery({ delivery_id: 'other' })
  assert.equal(state.selectedDelivery.value.delivery_id, 'page-1')
  assert.equal(state.resolutionDirty.value, true)
  accepted = true
  await state.openDelivery({ delivery_id: 'other' })
  assert.equal(state.selectedDelivery.value.delivery_id, 'other')
  assert.equal(state.resolutionDirty.value, false)
  await state.openDelivery(original)
  await state.deliveryResolved()
  assert.equal(state.selectedDelivery.value, state.deliveries.value[0])
})

test('automation configuration keeps TLS modes exclusive and preserves empty-password save semantics', async () => {
  const saved = [], hasUnsavedChanges = Vue.ref(true)
  let refreshed = 0
  const { useAutomationConfiguration } = automationHooks({
    ...Vue,
    getAutomationConfiguration: async () => ({ smtp_password_configured: true, smtp_password: 'ignored-test-placeholder' }),
    saveAutomationConfiguration: async body => { saved.push(body); return { message: '已保存' } },
  })
  const state = useAutomationConfiguration({ hasUnsavedChanges, refresh: async () => { refreshed++ } })
  await state.loadConfiguration()
  assert.equal(state.configuration.smtp_password, '')
  assert.equal(state.configuration.smtp_password_configured, true)
  assert.equal(hasUnsavedChanges.value, false)
  state.configuration.smtp_use_ssl = true
  state.selectSsl()
  assert.equal(state.configuration.smtp_use_tls, false)
  state.configuration.smtp_use_tls = true
  state.selectTls()
  assert.equal(state.configuration.smtp_use_ssl, false)
  await state.submitConfiguration()
  assert.equal(saved[0].smtp_password, null)
  state.configuration.smtp_password = 'new-test-password'
  await state.submitConfiguration()
  assert.equal(saved[1].smtp_password, 'new-test-password')
  assert.equal(state.configuration.smtp_password, '')
  assert.equal(refreshed, 2)
})

function designerPage(t, overrides = {}) {
  const events = [], apiCalls = [], scope = Vue.effectScope()
  const previousWindow = globalThis.window
  let guard
  globalThis.window = { confirm: () => false }
  t.after(() => { scope.stop(); globalThis.window = previousWindow })
  const bindings = {
    ...Vue, onMounted() {}, defineEmits: () => (...args) => events.push(args),
    useRoute: () => ({ query: {} }), useRouter: () => ({ replace() {} }),
    useUnsavedChanges: value => { guard = value },
    listDesignerResources: async () => ({ items: [] }),
    saveDesignerResource: async (kind, draft, id, token) => { apiCalls.push({ kind, draft, id, token }); return { ...draft, version: 2 } },
    validateDesignerSql: async () => ({ formatted_sql: 'SELECT 1 AS value', tables: ['task'] }),
    previewDesignerQuery: async (id, query, token) => { apiCalls.push({ id, query, token }); return { columns: [], items: [], returned: 0, mode: 'demo' } },
    ...overrides,
  }
  const querySource = readFileSync(new URL('../src/composables/useDesignerQueryPreview.ts', import.meta.url), 'utf8').replace(/^import .*$/gm, '').replace('export function', 'function')
  bindings.useDesignerQueryPreview = scriptFunction(...Object.keys(bindings), `${querySource}; return useDesignerQueryPreview`)(...Object.values(bindings))
  const source = descriptor('DesignerView').scriptSetup.content.replace(/^import .*$/gm, '')
  const state = scope.run(() => scriptFunction(...Object.keys(bindings), `${source}; return { hasUnsavedChanges, adminToken, queryDraft, templateDraft, definitionDraft, queryValidation, queryPreview, loadResources, chooseQuery, saveResource, validateQuery, previewQuery, error }`)(...Object.values(bindings)))
  return { ...state, events, apiCalls, get guard() { return guard } }
}

test('designer keeps one page-owned dirty guard across editor drafts and resets it after saving a definition', async t => {
  const page = designerPage(t)
  await page.loadResources()
  assert.equal(page.guard, page.hasUnsavedChanges)
  assert.equal(page.hasUnsavedChanges.value, false)
  page.queryDraft.value.columns[0].label = '修改字段'
  await Vue.nextTick()
  assert.equal(page.hasUnsavedChanges.value, true)
  const selected = page.queryDraft.value
  page.chooseQuery({ ...selected, query_task_id: 'other' })
  assert.equal(page.queryDraft.value, selected, 'cancelled replacement retains the reviewed draft')
  page.templateDraft.value.config.layout = 'chart_first'
  page.definitionDraft.value.name = '报表甲'
  await Vue.nextTick()
  assert.equal(page.hasUnsavedChanges.value, true)
  await page.saveResource('definitions', page.definitionDraft.value, 'definition_id')
  assert.equal(page.hasUnsavedChanges.value, false)
  assert.deepEqual(page.events, [['definitions-change']])
  assert.equal(page.definitionDraft.value.name, '报表甲')
})

test('designer SQL validation and preview use the current draft, token and unchanged preview parameters', async t => {
  const page = designerPage(t)
  await page.loadResources()
  await page.previewQuery()
  assert.match(page.error.value, /请先保存/)
  assert.equal(page.apiCalls.length, 0)
  page.queryDraft.value.query_task_id = 'query-1'
  page.adminToken.value = 'test-admin-token'
  await page.validateQuery()
  assert.equal(page.queryDraft.value.sql_text, 'SELECT 1 AS value')
  await page.previewQuery()
  assert.equal(page.apiCalls[0].id, 'query-1')
  assert.equal(page.apiCalls[0].token, 'test-admin-token')
  assert.equal(page.apiCalls[0].query.max_rows, 10)
  assert.deepEqual(page.apiCalls[0].query.parameters, {})
  assert.equal(page.queryPreview.value.returned, 0)
})
