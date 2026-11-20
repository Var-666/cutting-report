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
  const code = readFileSync(new URL('../src/composables/useConfirm.ts', import.meta.url), 'utf8').replace(/^import .*$/gm, '').replace(/^export /gm, '')
  let options, unmount, deactivate, destroyed = 0
  const confirm = scriptFunction('useDialog', 'onBeforeUnmount', 'onDeactivated', `${code}; return useConfirm()`)(
    () => ({ warning: value => { options = value; return { destroy() { destroyed++ } } } }),
    cb => { unmount = cb }, cb => { deactivate = cb },
  )
  const accepted = confirm('执行此操作')
  options.onPositiveClick()
  assert.equal(await confirm('动画退出期间重复打开'), false)
  options.onAfterLeave()
  assert.equal(await accepted, true)
  const dismissed = confirm('放弃修改')
  options.onAfterLeave()
  assert.equal(await dismissed, false)
  const masked = confirm({ title: '删除记录？', content: '记录 A', positiveText: '删除记录', maskClosable: true })
  assert.equal(options.positiveText, '删除记录')
  assert.equal(options.title, '删除记录？')
  assert.equal(await confirm('重复打开'), false)
  assert.equal(options.maskClosable, true)
  options.onAfterLeave()
  assert.equal(await masked, false)
  const pending = confirm('离开页面'); deactivate(); assert.equal(await pending, false)
  assert.equal(destroyed, 1)
  unmount(); assert.equal(destroyed, 1)
})


test('draft protection blocks repeated close, preserves edits on cancel, and covers route and unload', async () => {
  const code = readFileSync(new URL('../src/composables/useUnsavedChanges.ts', import.meta.url), 'utf8')
    .replace(/^import .*$/gm, '').replace('export function', 'function')
  const listeners = new EventTarget()
  let leave, mount, deactivate, resolve, options
  const createGuard = scriptFunction(
    'computed', 'watch', 'onMounted', 'onActivated', 'onDeactivated', 'onBeforeUnmount',
    'onBeforeRouteLeave', 'useConfirm', 'window', `${code}; return useUnsavedChanges`,
  )(Vue.computed, Vue.watch, callback => { mount = callback }, () => {}, callback => { deactivate = callback },
    () => {}, callback => { leave = callback }, () => input => { options = input; return new Promise(done => { resolve = done }) }, listeners)
  const dirty = Vue.ref(false)
  const saving = Vue.ref(false)
  const scope = Vue.effectScope()
  const discard = scope.run(() => createGuard(dirty, '离开将丢弃修改。', saving))
  const unloadAllowed = () => listeners.dispatchEvent(new Event('beforeunload', { cancelable: true }))
  try {
    mount()
    assert.equal(discard(), true)
    assert.equal(unloadAllowed(), true)
    dirty.value = true
    assert.equal(unloadAllowed(), false)
    const first = discard('关闭将丢弃草稿。')
    assert.equal(discard(), false)
    assert.equal(leave(), false)
    assert.equal(options.negativeText, '继续编辑')
    assert.equal(options.positiveText, '放弃修改')
    resolve(false)
    assert.equal(await first, false)
    assert.equal(dirty.value, true)
    const navigation = leave()
    assert.equal(leave(), navigation)
    saving.value = true
    resolve(true)
    assert.equal(await navigation, false)
    assert.equal(discard(), false)
    saving.value = false
    const approved = discard()
    resolve(true)
    assert.equal(await approved, true)
    dirty.value = false
    assert.equal(discard(), true)
    assert.equal(unloadAllowed(), true)
    dirty.value = true
    const stale = discard()
    deactivate()
    resolve(true)
    assert.equal(await stale, false)
    assert.equal(unloadAllowed(), true)
  } finally { scope.stop() }
})

test('migrated controls compile with native directory buttons and semantic welding disclosures', () => {
  const files = ['views/DatabaseView', 'views/AdminAccountsView', 'views/AutomationView', 'views/WeldingReportView', 'views/WeldingHomeView', 'views/WorkshopView', 'views/AlarmCenterView', 'views/CuttingDispatchView', 'views/ReportCenterView', 'components/ReportFeedback', 'components/ReportPanelState', 'components/ReportJobStatus', 'components/DownloadPdfButton', 'components/EmailDeliveryResolution', 'components/ReportPeriodPicker', 'components/CatalogTreeNode', 'components/AnalysisPanel', 'components/AppReportTable', 'components/ReportForm', 'components/dispatch/DispatchHistoryTable', 'components/automation/AutomationScheduleCards', 'components/automation/AutomationDeliveryTable', 'components/workshop/WorkshopBatchConfirmationDialog', 'components/workshop/WorkshopCorrectionDialog']
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
    if (['views/WeldingReportView', 'views/WeldingHomeView'].includes(name)) {
      const disclosures = [...widgetTemplate.matchAll(/<details\b[\s\S]*?<\/details>/g)]
      const home = name === 'views/WeldingHomeView'
      assert.equal(disclosures.length, home ? 3 : 2)
      for (const [disclosure] of disclosures) {
        const opening = disclosure.slice(0, disclosure.indexOf('<summary>'))
        assert.match(opening, home ? /class="welding-chart-data"/ : /class="panel welding-quality"/)
        assert.match(disclosure, /^<details\b[\s\S]*?><summary>[^<]+<\/summary>/)
        widgetTemplate = widgetTemplate.replace(opening, '')
      }
    }
    assert.doesNotMatch(widgetTemplate, /<(?:button|input|textarea|select|details|progress)\b/, name)
    assert.doesNotMatch(descriptor.scriptSetup.content, /window\.(?:confirm|prompt)\(/, name)
  }
})


test('reviewed controls keep filters stationary, the original tree and ten-row file pages without internal vertical scrolling', () => {
  const source = name => readFileSync(new URL(`../src/${name}.vue`, import.meta.url), 'utf8')
  const alarm = parse(source('views/AlarmCenterView')).descriptor
  const styles = alarm.styles.map(style => style.content).join('\n')
  assert.match(styles, /\.alarm-tabs\{[^}]*height:auto;overflow:visible/)
  assert.doesNotMatch(styles, /\.alarm-tabs\{[^}]*overflow(?:-[xy])?:(?:auto|scroll)/)
  const dispatch = parse(source('views/CuttingDispatchView')).descriptor
  assert.match(dispatch.template.content, /<AppReportTable :flex-height="rows.length === 0" :max-height="null" :columns="fileTableColumns"/)
  assert.match(readFileSync(new URL('../src/composables/useCuttingFileBrowser.ts', import.meta.url), 'utf8'), /const pageSize = 10/)
  assert.match(dispatch.styles[0].content, /\.download-workspace \.dispatch-table-wrap \{ display:flex; flex:1 1 auto; min-height:260px;/)
  const tree = parse(source('components/CatalogTreeNode')).descriptor
  assert.match(tree.template.content, /class="catalog-folder" aria-hidden="true"/)
  assert.match(tree.template.content, /<CatalogTreeNode[\s\S]*@select="emit\('select', \$event\)"[\s\S]*@toggle="emit\('toggle', \$event\)"/)
})

test('shared report table removes the height cap only when requested and keeps empty flex layouts', async () => {
  const states = []
  const items = Array.from({ length: 20 }, (_, index) => ({ id: index + 1 }))
  const Table = Vue.defineComponent({ props: ['data', 'maxHeight', 'flexHeight'],
    setup(props) { states.push({ rows: props.data.length, maxHeight: props.maxHeight, flexHeight: props.flexHeight }); return () => null },
  })
  const props = { columns: [{ key: 'id', title: '文件', width: 160 }], items, rowKey: row => row.id, paginate: false }
  for (const overrides of [{}, { maxHeight: null }, { maxHeight: 330 }, { items: [], maxHeight: null, flexHeight: true }]) {
    await render('AppReportTable', { ...props, ...overrides }, { NDataTable: Table })
  }
  assert.deepEqual(states, [
    { rows: 20, maxHeight: 540, flexHeight: false },
    { rows: 20, maxHeight: undefined, flexHeight: false },
    { rows: 20, maxHeight: 330, flexHeight: false },
    { rows: 0, maxHeight: undefined, flexHeight: true },
  ])
})

test('period calendar clicks keep both draft dates open until Apply or an outside click', () => {
  const source = readFileSync(new URL('../src/components/ReportPeriodPicker.vue', import.meta.url), 'utf8')
  const script = parse(source).descriptor.scriptSetup.content.replace(/^import .*$/gm, '')
  const events = []
  const bindings = {
    ...Vue, defineProps: () => ({ startDate: '2026-10-29', endDate: '2026-10-29', anchorDate: '2026-10-29', maxDays: 366 }),
    defineEmits: () => (...event) => events.push(event), useId: () => 'period-test',
    createActivationBinding: () => ({ activate() {}, deactivate() {} }),
    onMounted() {}, onActivated() {}, onDeactivated() {}, onBeforeUnmount() {},
  }
  const api = scriptFunction(...Object.keys(bindings), `${script}\nreturn { open, draftStart, draftEnd, showPanel, handleClickoutside, applyPeriod }`)(...Object.values(bindings))
  const calendarClick = { target: { closest: selector => selector === '.n-date-panel' ? {} : null } }
  api.showPanel()
  api.draftStart.value = '2026-10-28'
  api.handleClickoutside(calendarClick)
  assert.equal(api.open.value, true)
  api.draftEnd.value = '2026-10-28'
  api.handleClickoutside(calendarClick)
  assert.equal(api.open.value, true)
  assert.deepEqual(events, [], 'choosing dates must not apply or discard the draft')
  api.applyPeriod()
  assert.equal(api.open.value, false)
  assert.deepEqual(events.find(([event]) => event === 'apply'), ['apply', { startDate: '2026-10-28', endDate: '2026-10-28', preset: 'custom' }])
  api.showPanel()
  api.draftStart.value = '2026-10-30'
  api.draftEnd.value = '2026-10-28'
  api.applyPeriod()
  assert.equal(api.open.value, true)
  assert.equal(events.filter(([event]) => event === 'apply').length, 1, 'an invalid range must not query')
  api.handleClickoutside({ target: { closest: () => null } })
  assert.equal(api.open.value, false)
})

test('period calendars escape the scrollable popover while other date pickers retain their default container', async () => {
  const targets = []
  const DatePicker = component('AppDatePicker', {
    NDatePicker: Vue.defineComponent({ props: ['to'], setup(props) { targets.push(props.to); return () => null } }),
  })
  const Period = component('ReportPeriodPicker', {
    AppDatePicker: DatePicker,
    AppIcon: () => null,
    createActivationBinding: () => ({ activate() {}, deactivate() {} }),
    NPopover: Vue.defineComponent({ setup(_, { slots }) { return () => slots.default?.() } }),
  })
  const app = Vue.createSSRApp({ render: () => Vue.h(Vue.Fragment, [
    Vue.h(Period, { startDate: '2026-10-29', endDate: '2026-10-29' }), Vue.h(DatePicker),
  ]) })
  app.component('AppDatePicker', DatePicker)
  setupCssr(app)
  await renderToString(app)
  assert.deepEqual(targets, ['body', 'body', undefined])
})

test('applying a report period submits once after date watchers settle and cancel does not query', async () => {
  for (const apply of [true, false]) {
    const dates = Vue.reactive({ start: '2026-10-29', end: '2026-10-29' })
    const submissions = []
    let filter = 'previous-project'
    const stop = Vue.watch(() => [dates.start, dates.end], () => { filter = '' })
    const Query = component('StandardReportQuery', {
      AppIcon: () => null, ReportExportMenu: () => null,
      ReportPeriodPicker: Vue.defineComponent({
        emits: ['update:startDate', 'update:endDate', 'apply'],
        setup(_, { emit }) {
          if (apply) {
            emit('update:startDate', '2026-10-27')
            emit('update:endDate', '2026-10-28')
            emit('apply', { startDate: '2026-10-27', endDate: '2026-10-28', preset: 'custom' })
          }
          return () => null
        },
      }),
    })
    const app = Vue.createSSRApp({ render: () => Vue.h(Query, {
      startDate: dates.start, endDate: dates.end, chartType: 'bar_line',
      'onUpdate:startDate': date => { dates.start = date }, 'onUpdate:endDate': date => { dates.end = date },
      onSubmit: () => submissions.push({ ...dates, filter }),
    }) })
    app.component('AppSelect', () => null)
    setupCssr(app)
    await renderToString(app)
    await Vue.nextTick()
    stop()
    assert.deepEqual(submissions, apply ? [{ start: '2026-10-27', end: '2026-10-28', filter: '' }] : [])
  }
})
