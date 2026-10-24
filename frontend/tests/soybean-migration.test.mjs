import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { computed, h, Fragment, ref, reactive, watch, nextTick } from 'vue'
import { createPinia, setActivePinia, defineStore } from 'pinia'
import { parse } from '@vue/compiler-sfc'
import { cuttingRoutes } from '../src/router/cuttingRoutes.js'
import { adminMenus } from '../src/router/menus.js'
import { selectOptions } from '../src/utils/selectOptions.js'
import { useAppStore } from '../src/state/app.js'
import { getTabIdByRoute } from '../src/utils/themeTabs.js'

function setupControl(name, props, attrs = {}, slots = {}) {
  const emitted = []
  let deactivate
  const source = readFileSync(new URL(`../src/components/${name}.vue`, import.meta.url), 'utf8')
  const script = parse(source).descriptor.scriptSetup.content.replace(/^import .*$/gm, '')
  const bindings = {
    computed, ref, watch, selectOptions, defineOptions: () => {}, defineProps: () => props,
    defineEmits: () => (...event) => emitted.push(event), useAttrs: () => attrs, useSlots: () => slots,
    onDeactivated: (callback) => { deactivate = callback },
  }
  const api = name === 'AppSelect' ? 'options, selectedValue, choose' : 'isOutsideBounds, commit'
  const state = new Function(...Object.keys(bindings), `${script}\nreturn { open, control, invalid, handleInvalid, ${api} }`)(...Object.values(bindings))
  return { state, emitted, deactivate: () => deactivate() }
}

test('sidebar exposes admin routes, excludes redirects and resets the new content scroller', () => {
  const source = readFileSync(new URL('../src/router/index.js', import.meta.url), 'utf8')
  const expression = source.slice(source.indexOf('const router = createRouter('), source.indexOf('\nrouter.beforeEach'))
  const scrolls = []
  const document = { querySelector: () => ({ scrollTo: (value) => scrolls.push(value) }) }
  const router = new Function('createRouter', 'createWebHistory', 'cuttingRoutes', 'document', `${expression}\nreturn router`)((options) => options, () => ({}), cuttingRoutes, document)
  const menus = adminMenus(router.routes.find((route) => route.path === '/admin').children)
  const flatten = (items) => items.flatMap((item) => {
    assert.ok(item.icon, `${item.label} needs a menu icon to keep icon and text columns aligned`)
    return item.children ? [...(item.routeName ? [item.routeName] : []), ...flatten(item.children)] : item.key
  })
  assert.deepEqual(flatten(menus), ['admin-home', 'leadership-report', 'workshop-report', 'equipment-report', 'report-center', 'alarm-center', 'cutting-dispatch', 'settings', 'database', 'automation', 'admin-accounts', 'model-usage'])
  const adminRoutes = router.routes.find(route => route.path === '/admin').children
  assert.equal(adminRoutes.find(route => route.path === 'designer').redirect, '/admin/settings')
  assert.equal(adminRoutes.find(route => route.path === 'settings').children.find(route => route.path === 'report-design').redirect, '/admin/settings')
  assert.doesNotMatch(source, /import\('\.\.\/views\/DesignerView.vue'\)/)
  assert.equal(menus.at(-1).key, '/admin/settings')
  assert.equal(menus.at(-1).routeName, 'settings')
  assert.ok(menus.at(-1).children.every((item) => item.key !== 'settings'))
  assert.deepEqual(menus.filter((menu) => menu.children).map((menu) => menu.key), ['/admin/reports', '/admin/settings'])
  assert.deepEqual(router.scrollBehavior(), { top: 0 })
  assert.deepEqual(scrolls, [{ top: 0 }])
})

test('official menu adapter retains parent navigation, child icons and the actual route hierarchy', () => {
  const source = readFileSync(new URL('../src/store/modules/route/index.js', import.meta.url), 'utf8').replace(/^import .*$/gm, '').replace('export const', 'const')
  const navigations = []
  const router = { options: { routes: [{ path: '/admin', children: [] }] }, resolve: to => ({ path: typeof to === 'string' ? to : '/admin/' + to.name }), push: to => navigations.push(to) }
  const items = [{ key: '/admin/settings', label: '系统设置', icon: 'settings', routeName: 'settings', children: [{ key: 'database', label: '数据库配置', icon: 'database' }] }]
  setActivePinia(createPinia())
  const useStore = new Function('computed', 'h', 'defineStore', 'router', 'adminMenus', 'AppIcon', `${source}\nreturn useRouteStore`)(computed, h, defineStore, router, () => items, 'i')
  const store = useStore()
  store.menus[0].onClick()
  assert.deepEqual(navigations, [{ name: 'settings' }])
  assert.deepEqual(store.getSelectedMenuKeyPath('database'), ['/admin/settings', 'database'])
  assert.equal(store.findMenu('settings').routeKey, '/admin/settings')
  assert.equal(store.menus[0].children[0].icon().props.name, 'database')
  assert.equal(store.searchMenus.length, 2)
})

test('responsive sidebar closes on entering mobile and reopens when returning to desktop', () => {
  setActivePinia(createPinia())
  const app = useAppStore()
  assert.equal(app.mixSiderFixed, true)
  app.setMobile(true)
  assert.equal(app.siderCollapse, true)
  app.siderCollapse = false
  app.setMobile(true)
  assert.equal(app.siderCollapse, false, 'unchanged viewport must preserve the user selection')
  app.setMobile(false)
  assert.equal(app.siderCollapse, false)
})

test('native PageTab adapter honors cached paths, middle-click setting and active-tab fallback', async () => {
  const source = parse(readFileSync(new URL('../src/layouts/modules/ThemeTabs.vue', import.meta.url), 'utf8')).descriptor.scriptSetup.content.replace(/^import .*$/gm, '')
  const route = reactive({ fullPath: '/admin/reports/leadership', path: '/admin/reports/leadership', meta: {} })
  const theme = reactive({ tab: { cache: true, closeTabByMiddleClick: false } })
  const stored = new Map([['tabs', ['/admin/settings', '/outside', '/admin/not-a-route', '/admin/settings/report-design']]])
  const destinations = []
  const router = { push: path => destinations.push(path), resolve: path => ({ path: path.split('?')[0], query: Object.fromEntries(new URLSearchParams(path.split('?')[1] || '')), matched: path === '/admin/not-a-route' ? [] : [{ meta: { role: 'admin' }, ...(path === '/admin/settings/report-design' ? { redirect: '/admin/settings' } : {}) }], meta: { title: path, icon: 'home' } }) }
  const localStg = { get: key => stored.get(key), set: (key, value) => stored.set(key, [...value]), remove: key => stored.delete(key) }
  const api = new Function('computed', 'nextTick', 'ref', 'watch', 'useRoute', 'useRouter', 'useThemeStore', 'localStg', 'getTabIdByRoute', `${source}\nreturn { tabs, close, middleClick, scrollTabs }`)(computed, nextTick, ref, watch, () => route, () => router, () => theme, localStg, getTabIdByRoute)
  await nextTick()
  assert.deepEqual(api.tabs.value.map(tab => tab.path), ['/admin/home', '/admin/settings', '/admin/reports/leadership'])
  route.fullPath = '/admin/reports/leadership?start_date=2026-10-01'
  await nextTick()
  assert.equal(api.tabs.value.length, 3, 'changing report filters must not open a duplicate tab')
  assert.equal(api.tabs.value.at(-1).path, route.fullPath, 'the existing tab must remember the latest filter URL')
  let prevented = false
  const event = { button: 1, preventDefault: () => { prevented = true } }
  api.middleClick(event, route.path)
  assert.equal(destinations.length, 0)
  theme.tab.closeTabByMiddleClick = true
  api.middleClick(event, route.path)
  assert.equal(prevented, true)
  assert.deepEqual(destinations, ['/admin/settings'])
  api.close('/admin/home')
  assert.ok(api.tabs.value.some(tab => tab.path === '/admin/home'))
  theme.tab.cache = false
  await nextTick()
  assert.equal(stored.has('tabs'), false)
  let preventedWheel = 0
  const scroller = { scrollLeft: 0 }
  const wheel = { currentTarget: scroller, deltaX: 0, deltaY: 80, preventDefault: () => { preventedWheel++ } }
  api.scrollTabs(wheel)
  assert.equal(scroller.scrollLeft, 80)
  api.scrollTabs({ ...wheel, deltaX: -40, deltaY: 2 })
  assert.equal(scroller.scrollLeft, 40)
  assert.equal(preventedWheel, 2)
  api.scrollTabs({ ...wheel, currentTarget: { get scrollLeft() { return 0 }, set scrollLeft(_) {} } })
  assert.equal(preventedWheel, 2, 'a non-scrollable tab bar must not swallow page scrolling')
})

test('upstream tab identity includes sorted queries only for explicit multiTab routes', () => {
  assert.equal(getTabIdByRoute({ path: '/admin/home', query: { b: '2', a: '1' }, meta: {} }), '/admin/home')
  assert.equal(getTabIdByRoute({ path: '/admin/home', query: { b: '2', a: '1' }, meta: { multiTab: true } }), '/admin/home?a=1&b=2')
})

test('select option slots retain fragments, groups, disabled options and original value types', () => {
  assert.deepEqual(selectOptions([
    h('option', { value: '', disabled: '' }, '请选择'),
    h(Fragment, null, [h('option', { value: 2 }, ['设备', '二'])]),
    h('optgroup', { label: '其他' }, [h('option', { value: false }, '否')]),
  ]), [
    { value: 0, sourceValue: '', label: '请选择', disabled: true },
    { value: 1, sourceValue: 2, label: '设备二', disabled: false },
    { value: 2, sourceValue: false, label: '其他 · 否', disabled: false },
  ])
})

test('select retains v-model, value-only actions and the existing change event contract', () => {
  const select = parse(readFileSync(new URL('../src/components/AppSelect.vue', import.meta.url), 'utf8')).descriptor
  assert.match(select.template.content, /<div class="app-select"[^>]*@click\.prevent>/, 'wrapping labels must not focus the hidden native select and close the menu')
  assert.match(select.scriptSetup.content, /filterable: Boolean/)
  assert.match(select.template.content, /:filterable="filterable"/)
  assert.match(select.template.content, /:placeholder="\$attrs.placeholder"/)
  const props = reactive({ modelValue: 2 })
  const { state, emitted, deactivate } = setupControl('AppSelect', props, {}, { default: () => [h('option', { value: '' }, '导出'), h('option', { value: 2 }, '设备二')] })
  assert.equal(state.selectedValue.value, 1)
  state.choose(1)
  assert.deepEqual(emitted, [['update:modelValue', 2], ['change', { target: { value: 2 }, value: 2 }]])
  props.modelValue = undefined
  assert.equal(state.selectedValue.value, 0)
  state.open.value = true
  deactivate()
  assert.equal(state.open.value, false)
  const action = setupControl('AppSelect', {}, { value: 'pdf' }, { default: () => [h('option', { value: '' }, '导出'), h('option', { value: 'pdf' }, 'PDF')] })
  assert.equal(action.state.selectedValue.value, 1)
})

test('date picker keeps ISO date and month boundaries, clearing and change events', () => {
  const props = reactive({ mode: 'date', min: '2026-10-01', max: '2026-10-23' })
  const { state, emitted, deactivate } = setupControl('AppDatePicker', props)
  assert.equal(state.isOutsideBounds(new Date(2026, 8, 30).getTime()), true)
  assert.equal(state.isOutsideBounds(new Date(2026, 9, 23).getTime()), false)
  assert.equal(state.isOutsideBounds(new Date(2026, 9, 24).getTime()), true)
  props.mode = 'month'; props.min = '2026-09'; props.max = '2026-10'
  assert.equal(state.isOutsideBounds(new Date(2026, 9, 31).getTime()), false)
  state.commit('2026-10')
  state.commit(null)
  assert.deepEqual(emitted, [
    ['update:modelValue', '2026-10'], ['change', { target: { value: '2026-10' }, value: '2026-10' }],
    ['update:modelValue', ''], ['change', { target: { value: '' }, value: '' }],
  ])
  state.open.value = true
  deactivate()
  assert.equal(state.open.value, false)
})

for (const name of ['AppSelect', 'AppDatePicker']) {
  test(`${name} exposes failed native form validation, focuses the control and clears errors on updates`, async () => {
    let focused = false
    const props = reactive({ modelValue: '' })
    const { state } = setupControl(name, props, {}, { default: () => [] })
    state.control.value = { focus: () => { focused = true } }
    state.handleInvalid()
    assert.equal(state.invalid.value, true)
    assert.equal(focused, true)
    props.modelValue = '2026-10-23'
    await nextTick()
    assert.equal(state.invalid.value, false)
    state.open.value = true
    props.disabled = true
    await nextTick()
    assert.equal(state.open.value, false)
  })
}
