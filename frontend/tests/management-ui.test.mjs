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
  assert.match(hero.scriptSetup.content, /home-laser-cutting\.webp/)
  assert.match(hero.template.content, /<AppIcon/)
  assert.match(hero.styles[0].content, /grid-template-columns:minmax\(420px,1fr\) 270px/)
  assert.match(hero.styles[0].content, /@media\(max-width:900px\)/)
})

test('management panels and tables share light headers without zebra striping', () => {
  const css = readFileSync(new URL('../src/assets/style.css', import.meta.url), 'utf8')
  assert.match(css, /\.management-page \.panel-title/)
  assert.match(css, /\.management-page \.history-table th[\s\S]*background:#eef5f9!important/)
  assert.doesNotMatch(css, /\.management-page[^}]*tbody tr:nth-child/)

  const settings = descriptor('SettingsHomeView')
  assert.match(settings.template.content, /<AppIcon/)
  assert.doesNotMatch(settings.template.content, />0[1-4]</)
})

test('settings usage card distinguishes actual token counts from unreported usage', () => {
  const settings = descriptor('SettingsHomeView')
  assert.match(settings.scriptSetup.content, /getLlmUsage\(/)
  assert.match(settings.template.content, /调用次数/)
  assert.match(settings.template.content, /Token 消耗/)
  assert.match(settings.template.content, /未返回 token 用量/)
  assert.match(settings.template.content, /v-else-if="usageError"/)
  assert.match(settings.template.content, /正在读取统计/)
  assert.match(settings.styles[0].content, /flex-wrap:wrap/)
})

test('login keeps authentication behavior inside the production visual system', () => {
  const login = descriptor('LoginView')
  assert.match(login.scriptSetup.content, /home-laser-cutting\.webp/)
  assert.match(login.template.content, /class="login-scene"/)
  assert.match(login.template.content, /<AppIcon name="chart"/)
  assert.match(login.template.content, /@submit\.prevent="submit"/)
})
