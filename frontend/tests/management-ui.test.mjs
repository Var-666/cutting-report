import { scriptFunction } from './typescript-helpers.mjs'
import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { parse } from '@vue/compiler-sfc'

function descriptor(name, folder = 'views') {
  const source = readFileSync(new URL(`../src/${folder}/${name}.vue`, import.meta.url), 'utf8')
  return parse(source).descriptor
}

test('remaining management pages use one production hero and density system', () => {
  for (const name of ['ReportCenterView', 'DatabaseView', 'DesignerView', 'AutomationView', 'AdminAccountsView']) {
    const view = descriptor(name)
    assert.match(view.scriptSetup.content, /import ManagementPageHero from/)
    assert.match(view.template.content, /<ManagementPageHero/)
    assert.match(view.template.content, /class="[^"]*management-page[^"]*"/)
  }

  const hero = descriptor('ManagementPageHero', 'components')
  assert.match(hero.scriptSetup.content, /NCard, NTag/)
  assert.match(hero.template.content, /<AppIcon/)
  assert.match(hero.template.content, /<NCard/)
  assert.match(hero.styles[0].content, /@media\(max-width:900px\)/)
})

test('management panels and tables share themed headers without zebra striping', () => {
  const css = readFileSync(new URL('../src/assets/style.css', import.meta.url), 'utf8')
  assert.match(css, /\.management-page \.panel-title/)
  assert.match(css, /\.management-page \.history-table th[\s\S]*background:var\(--theme-table-header-color\)!important/)
  assert.doesNotMatch(css, /\.management-page[^}]*tbody tr:nth-child/)

})

test('settings entry redirects to database and omits the repeated overview page', () => {
  const router = readFileSync(new URL('../src/router/index.ts', import.meta.url), 'utf8')
  assert.match(router, /path: '',\s*name: 'settings',\s*redirect: '\/admin\/settings\/database'/)
  assert.doesNotMatch(router, /SettingsHomeView/)
})

test('management hero status styles do not override Naive tag internals', () => {
  const hero = descriptor('ManagementPageHero', 'components')
  assert.match(hero.template.content, /<p v-if="description">/)
  assert.match(hero.styles[0].content, /\.management-hero__status > div\{/)
  assert.doesNotMatch(hero.styles[0].content, /\.management-hero__status div\{/)
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

test('welding connection uses the same source card grid and full-width field layout as SQL Server', () => {
  const database = descriptor('DatabaseView')
  const grid = database.template.content.slice(database.template.content.indexOf('class="saved-source-grid"'), database.template.content.indexOf('<div class="configuration-actions">'))
  assert.match(grid, /fieldset v-for="source in configurationSources"/)
  assert.match(grid, /class="welding-connection"/)
  const welding = grid.slice(grid.indexOf('<fieldset v-if="databaseConfiguration.welding"'))
  assert.doesNotMatch(welding, /class="connection-form"/)
  assert.doesNotMatch(welding, /<button|测试焊接只读连接|保存焊接连接/)
  assert.match(database.template.content, /class="configuration-switches"[\s\S]*name="weld_enabled"/)
  assert.match(welding, /name="weld_host"[^>]*:required="databaseConfiguration.welding.enabled"[^>]*placeholder="请输入地址，例如 10.147.1.21"/)
  assert.match(welding, /name="weld_password"[^>]*:required="databaseConfiguration.welding.enabled && !databaseConfiguration.welding.password_configured"/)
  assert.match(database.template.content, /v-else-if="source.configured">已使用独立连接配置/)
  assert.match(database.template.content, /连接参数尚未保存完整/)
  assert.match(database.styles[0].content, /\.saved-source-grid fieldset \{ min-width:0; display:grid; grid-template-columns:minmax\(0,1fr\) 92px;/)
  assert.match(database.styles[0].content, /\.saved-source-grid--welding \{ grid-template-columns:1fr;/)
  assert.match(database.styles[0].content, /\.saved-source-grid \{ display:grid; grid-template-columns:repeat\(2,minmax\(0,1fr\)\);/)
  assert.match(database.styles[0].content, /@media \(max-width:1100px\) \{ \.saved-source-grid \{ grid-template-columns:1fr;/)
})

test('database save reloads active settings and status without a restart prompt', async () => {
  const { computed, reactive, ref } = await import('vue')
  const view = descriptor('DatabaseView')
  const calls = []
  const connection = { host: 'new-db', port: 1433, database: 'production', username: 'reader', password_configured: true,
    driver: 'ODBC Driver 18 for SQL Server', encrypt: true, trust_server_certificate: false }
  const current = { data_source_mode: 'sqlserver', worker_database_auth_enabled: true, file_database_enabled: true,
    production: { ...connection }, worker: { ...connection }, file: { ...connection } }
  const bindings = { computed, reactive, ref, watch() {}, onMounted() {}, useUnsavedChanges() {}, formatTableDateValue: String,
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

test('successful database saves clear old report reads and failed saves preserve them', async () => {
  const { reportCache } = await import('../src/api/reportCache.ts')
  const { saveDatabaseConfiguration } = await import('../src/api/reports.ts')
  const originalFetch = globalThis.fetch
  try {
    await reportCache.read('database-runtime-test', () => 'old')
    globalThis.fetch = async () => ({ ok: false, json: async () => ({ detail: '保存失败' }) })
    await assert.rejects(saveDatabaseConfiguration({}), /保存失败/)
    assert.equal(await reportCache.read('database-runtime-test', () => 'unexpected'), 'old')
    globalThis.fetch = async () => ({ ok: true, json: async () => ({ success: true, data: { restart_required: false } }) })
    await saveDatabaseConfiguration({})
    assert.equal(await reportCache.read('database-runtime-test', () => 'new'), 'new')
  } finally { globalThis.fetch = originalFetch; reportCache.clear() }
})

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
  const selection = reactive({ id: 'Cutter01' })
  const bindings = { computed, reactive, ref, watch() {}, onMounted() {}, useUnsavedChanges() {}, formatTableDateValue: String,
    deviceSelection: selection,
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
  assert.match(testSection, /v-model="activeTestConnection.host"/)
  assert.match(testSection, /v-if="testIsWelding"[\s\S]*name="test_timezone"/)
})

for (const [reportType, period] of [['leadership', 'current_month'], ['leadership', 'previous_month'], ['workshop', 'current_day'], ['equipment', 'previous_day']]) {
  test(`editing ${reportType}/${period} preserves the stored period through a name-only save`, async () => {
    const Vue = await import('vue')
    const saved = []
    const scope = Vue.effectScope()
    const script = descriptor('AutomationView').scriptSetup.content.replace(/^import .*$/gm, '')
    const bindings = {
      ...Vue, onMounted() {}, useUnsavedChanges() {}, deviceSelection: Vue.reactive({ id: 'Cutter02' }),
      saveAutomationSchedule: async (body, id) => saved.push({ body, id }),
      getAutomationStatus: async () => ({}), listAutomationSchedules: async () => ({ items: [] }),
      listEmailDeliveries: async () => ({ items: [], total: 0 }),
    }
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
