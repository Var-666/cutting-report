import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import * as Vue from 'vue'
import * as Naive from 'naive-ui'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'
import { renderToString } from '@vue/server-renderer'
import { setup as setupCssr } from '@css-render/vue3-ssr'
import { scriptFunction } from './typescript-helpers.mjs'

function component(name, extra = {}) {
  const descriptor = parse(readFileSync(new URL(`../src/components/${name}.vue`, import.meta.url), 'utf8')).descriptor
  const bindings = { ...Vue, ...Object.fromEntries(Object.entries(Naive).filter(([name]) => /^N[A-Z]/.test(name))), ...extra }
  const script = compileScript(descriptor, { id: name, inlineTemplate: true }).content
    .replace(/import\s*\{([^}]+)\}\s*from\s*['"]vue['"]/g, (_, names) => {
      for (const name of names.split(',')) {
        const [original, alias = original] = name.trim().split(/\s+as\s+/)
        bindings[alias] = Vue[original]
      }
      return ''
    }).replace(/^import .*$/gm, '').replace('export default', 'return')
  return scriptFunction(...Object.keys(bindings), script)(...Object.values(bindings))
}
async function render(name, props = {}, extra = {}) {
  const app = Vue.createSSRApp(component(name, extra), props)
  for (const [name, comp] of Object.entries(Naive)) if (/^N[A-Z]/.test(name)) app.component(name, comp)
  setupCssr(app)
  return renderToString(app)
}

test('feedback keeps its public actions and accessible states using Naive UI', async () => {
  const html = await render('ReportFeedback', { type: 'error', title: '读取失败', message: '请重新查询', actionLabel: '重试', dismissible: true })
  assert.match(html, /n-alert/)
  assert.match(html, /role="alert"/)
  for (const text of ['读取失败', '请重新查询', '重试', '关闭提示']) assert.ok(html.includes(text))
  assert.deepEqual(component('ReportFeedback').emits.sort(), ['action', 'dismiss'])
  for (const [type, expected] of [['loading', 'n-spin'], ['empty', 'n-empty'], ['error', 'n-alert']]) {
    const html = await render('ReportPanelState', { type, title: '当前状态', message: '状态说明', actionLabel: '重新查询' })
    assert.ok(html.includes(expected))
    for (const text of ['当前状态', '状态说明', '重新查询']) assert.ok(html.includes(text))
  }
})

test('Naive confirmation resolves accept/cancel and cancels pending work when its page leaves', async () => {
  const code = readFileSync(new URL('../src/composables/useConfirm.ts', import.meta.url), 'utf8').replace(/^import .*$/gm, '').replace('export function', 'function')
  let options, unmount, deactivate, destroyed = 0
  const confirm = scriptFunction('useDialog', 'onBeforeUnmount', 'onDeactivated', `${code}; return useConfirm()`)(
    () => ({ warning: value => { options = value; return { destroy() { destroyed++ } } } }),
    cb => { unmount = cb }, cb => { deactivate = cb },
  )
  const accepted = confirm('执行此操作'); options.onPositiveClick(); assert.equal(await accepted, true)
  const rejected = confirm('放弃修改'); options.onNegativeClick(); assert.equal(await rejected, false)
  const closed = confirm('关闭'); options.onClose(); assert.equal(await closed, false)
  const escaped = confirm('Escape'); options.onEsc(); assert.equal(await escaped, false)
  const pending = confirm('离开页面'); deactivate(); assert.equal(await pending, false)
  assert.equal(destroyed, 1)
  unmount(); assert.equal(destroyed, 1)
})


test('migrated controls compile and retain native buttons only in the original directory tree', () => {
  const files = ['views/DatabaseView', 'views/AdminAccountsView', 'views/AutomationView', 'views/WeldingReportView', 'views/WorkshopView', 'views/AlarmCenterView', 'views/CuttingDispatchView', 'views/ReportCenterView', 'components/ReportFeedback', 'components/ReportPanelState', 'components/ReportJobStatus', 'components/DownloadPdfButton', 'components/EmailDeliveryResolution', 'components/ReportPeriodPicker', 'components/CatalogTreeNode', 'components/AnalysisPanel', 'components/AppReportTable', 'components/ReportForm']
  for (const name of files) {
    const source = readFileSync(new URL(`../src/${name}.vue`, import.meta.url), 'utf8')
    const { descriptor, errors } = parse(source)
    assert.deepEqual(errors, [], name)
    const script = compileScript(descriptor, { id: name })
    const template = compileTemplate({ source: descriptor.template.content, filename: name, id: name, compilerOptions: { bindingMetadata: script.bindings, expressionPlugins: ['typescript'] } })
    assert.deepEqual(template.errors, [], name)
    let widgetTemplate = descriptor.template.content
    if (['views/CuttingDispatchView', 'components/CatalogTreeNode'].includes(name)) {
      for (const [tag] of widgetTemplate.matchAll(/<button\b[^>]*>/g)) assert.match(tag, /class="(?:project|catalog)-(?:toggle|label)"/)
      widgetTemplate = widgetTemplate.replace(/<button\b[^>]*>/g, '')
    }
    assert.doesNotMatch(widgetTemplate, /<(?:button|input|textarea|select|details|progress)\b/, name)
    assert.doesNotMatch(descriptor.scriptSetup.content, /window\.(?:confirm|prompt)\(/, name)
  }
})


test('reviewed controls keep filters stationary, the original tree and a full-height file list', () => {
  const source = name => readFileSync(new URL(`../src/${name}.vue`, import.meta.url), 'utf8')
  const alarm = parse(source('views/AlarmCenterView')).descriptor
  const styles = alarm.styles.map(style => style.content).join('\n')
  assert.match(styles, /\.alarm-tabs\{[^}]*height:auto;overflow:visible/)
  assert.doesNotMatch(styles, /\.alarm-tabs\{[^}]*overflow(?:-[xy])?:(?:auto|scroll)/)
  const dispatch = parse(source('views/CuttingDispatchView')).descriptor
  assert.match(dispatch.template.content, /<AppReportTable flex-height :columns="fileTableColumns"/)
  assert.match(dispatch.styles[0].content, /\.download-workspace \.dispatch-table-wrap \{ display:flex; flex:1 1 auto; min-height:260px;/)
  const tree = parse(source('components/CatalogTreeNode')).descriptor
  assert.match(tree.template.content, /class="catalog-folder" aria-hidden="true"/)
  assert.match(tree.template.content, /<CatalogTreeNode[\s\S]*@select="emit\('select', \$event\)"[\s\S]*@toggle="emit\('toggle', \$event\)"/)
})
