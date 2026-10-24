import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { parse } from '@vue/compiler-sfc'

function descriptor(name, folder = 'views') {
  const source = readFileSync(new URL(`../src/${folder}/${name}.vue`, import.meta.url), 'utf8')
  return parse(source).descriptor
}

test('remaining management pages use one production hero and density system', () => {
  for (const name of ['ReportCenterView', 'SettingsHomeView', 'DatabaseView', 'DesignerView', 'AutomationView', 'AdminAccountsView']) {
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

  const settings = descriptor('SettingsHomeView')
  assert.match(settings.template.content, /<AppIcon/)
  assert.doesNotMatch(settings.template.content, />0[1-4]</)
})

test('settings overview omits usage summary requests and explanatory text', () => {
  const settings = descriptor('SettingsHomeView')
  assert.doesNotMatch(settings.scriptSetup.content, /getLlmUsage|loadUsage|onMounted|description:/)
  assert.doesNotMatch(settings.template.content, /llm-usage-card|<p|description=|status-note=/)
  assert.match(settings.template.content, /status-value="5 个管理模块"/)
})

test('management hero status styles do not override Naive tag internals', () => {
  const hero = descriptor('ManagementPageHero', 'components')
  assert.match(hero.template.content, /<p v-if="description">/)
  assert.match(hero.styles[0].content, /\.management-hero__status > div\{/)
  assert.doesNotMatch(hero.styles[0].content, /\.management-hero__status div\{/)
})

test('login keeps authentication behavior inside the production visual system', () => {
  const login = descriptor('LoginView')
  assert.match(login.scriptSetup.content, /home-laser-cutting\.webp/)
  assert.match(login.template.content, /class="login-scene"/)
  assert.match(login.template.content, /<AppIcon name="chart"/)
  assert.match(login.template.content, /@submit\.prevent="submit"/)
})

test('database source selector offers SQL Server and demo without the removed Excel source', () => {
  const database = descriptor('DatabaseView')
  const selector = database.template.content.match(/<AppSelect[^>]*name="data_source_mode"[^>]*>(.*?)<\/AppSelect>/s)[1]
  assert.deepEqual([...selector.matchAll(/<option value="([^"]+)"/g)].map((match) => match[1]), ['demo', 'sqlserver'])
})
