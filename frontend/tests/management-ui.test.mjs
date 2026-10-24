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
  const router = readFileSync(new URL('../src/router/index.js', import.meta.url), 'utf8')
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

test('database save reloads active settings and status without a restart prompt', async () => {
  const { computed, reactive, ref } = await import('vue')
  const view = descriptor('DatabaseView')
  const calls = []
  const connection = { host: 'new-db', port: 1433, database: 'production', username: 'reader', password_configured: true,
    driver: 'ODBC Driver 18 for SQL Server', encrypt: true, trust_server_certificate: false }
  const current = { data_source_mode: 'sqlserver', worker_database_auth_enabled: true, file_database_enabled: true,
    production: { ...connection }, worker: { ...connection }, file: { ...connection } }
  const bindings = { computed, reactive, ref, onMounted() {}, useUnsavedChanges() {}, formatTableDateValue: String,
    getDatabaseStatus: async () => { calls.push('status'); return { mode: 'sqlserver', driver: connection.driver } },
    getDatabaseConfiguration: async () => { calls.push('configuration'); return structuredClone(current) },
    saveDatabaseConfiguration: async (body) => { calls.push(body); return { saved: true, restart_required: false, message: '配置已保存并热加载' } },
  }
  const script = view.scriptSetup.content.replace(/^import[\s\S]*?from ['"][^'"]+['"]\s*$/gm, '')
  const page = new Function(...Object.keys(bindings), `${script}\nreturn { databaseConfiguration, configurationSaving, configurationResult, configurationError, hasUnsavedChanges, saveConfiguration }`)(...Object.values(bindings))
  page.databaseConfiguration.value = structuredClone(current)
  for (const source of ['production', 'worker', 'file']) page.databaseConfiguration.value[source].password = 'new-private'
  page.hasUnsavedChanges.value = true
  await page.saveConfiguration()
  assert.equal(page.configurationResult.value.restart_required, false)
  assert.equal(page.configurationSaving.value, false)
  assert.equal(page.configurationError.value, '')
  assert.equal(page.hasUnsavedChanges.value, false)
  assert.equal(calls[0].production.password, 'new-private')
  assert.deepEqual(calls.slice(1).sort(), ['configuration', 'status'])
  assert.ok(['production', 'worker', 'file'].every(source => page.databaseConfiguration.value[source].password === ''))
  assert.match(view.template.content, /保存后自动热加载 · 无需重启容器/)
  assert.doesNotMatch(view.template.content, /重启服务后生效/)
})

test('successful database saves clear old report reads and failed saves preserve them', async () => {
  const { reportCache } = await import('../src/api/reportCache.js')
  const { saveDatabaseConfiguration } = await import('../src/api/reports.js')
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
  assert.match(template, /@submit.prevent="saveConfiguration"/)
  assert.match(template, /@submit.prevent="runConnectionTest"/)
  assert.match(template, /aria-label="数据库状态"/)
  assert.doesNotMatch(template, /生产查询映射|固定数据集预览|SQL 工作台|mapping-panel|preview-panel|sql-panel/)
  assert.doesNotMatch(database.scriptSetup.content, /previewDatabaseDataset|executeWorkbenchSql|validateWorkbenchSql|runPreview|setSqlExample|previewResult|sqlResult|query_mappings/)
})
