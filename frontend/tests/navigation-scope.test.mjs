import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { computed, defineComponent, effectScope, h, nextTick, reactive, ref, watch } from 'vue'
import { createPinia, defineStore, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, isNavigationFailure, NavigationFailureType } from 'vue-router'
import { adminMenus, navigationSection } from '../src/router/menus.ts'
import { cuttingRoutes } from '../src/router/cuttingRoutes.ts'
import { weldingRoutes } from '../src/router/weldingRoutes.ts'
import { lineHome, productionGroups, productionLineIcon, selectedLineType } from '../src/utils/productionLines.ts'
import { scriptFunction } from './typescript-helpers.mjs'
import { setDeviceCatalog, getDeviceId, resetDeviceContext } from '../src/api/deviceContext.ts'
import { getDatabaseConfiguration, saveDatabaseConfiguration, testWeldingConnection } from '../src/api/configuration.ts'
import { getAutomationStatus, saveAutomationSchedule } from '../src/api/automation.ts'

const read = path => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8')
const selection = () => reactive({ id: 'cut-a', items: [
  { id: 'cut-a', type: 'cutting', name: '切割A', enabled: true },
  { id: 'cut-b', type: 'cutting', name: '切割B', enabled: true },
  { id: 'weld-a', type: 'welding', name: '焊接A', enabled: true },
] })

function setupRouter() {
  const source = read('router/index.ts')
  const expression = source.slice(source.indexOf('const router = createRouter('), source.indexOf('\nrouter.beforeEach'))
  const options = scriptFunction('createRouter', 'createWebHistory', 'cuttingRoutes', 'weldingRoutes', `${expression}; return router`)(value => value, () => null, cuttingRoutes, weldingRoutes)
  const empty = defineComponent({ render: () => null })
  function stubComponents(routes) { return routes.map(route => ({ ...route, ...(route.component ? { component: empty } : {}), children: route.children && stubComponents(route.children) })) }
  return createRouter({ ...options, history: createMemoryHistory(), routes: stubComponents(options.routes), scrollBehavior: undefined })
}

function setupMenus(router, deviceSelection) {
  setActivePinia(createPinia())
  const script = read('store/modules/route/index.ts').replace(/^import .*$/gm, '').replace('export const', 'const')
  const bindings = { computed, h, defineStore, router, adminMenus, navigationSection, lineHome, productionGroups, productionLineIcon, selectedLineType, deviceSelection, AppIcon: 'i' }
  return scriptFunction(...Object.keys(bindings), `${script}; return useRouteStore`)(...Object.values(bindings))()
}

test('public routes have stable primary selection and only settings expands a secondary menu', async () => {
  const router = setupRouter(), selected = selection()
  const menus = setupMenus(router, selected)
  selected.id = 'weld-a'
  for (const [path, key, secondary] of [
    ['/admin/home', 'admin-home', false], ['/admin/pdm-statistics', 'pdm-statistics', false],
    ['/admin/automation', 'automation', false], ['/admin/settings/database', '/admin/settings', true],
  ]) {
    await router.push(path)
    assert.equal(menus.primaryKey, key)
    assert.equal(menus.secondaryMenus.length > 0, secondary)
    assert.equal(selected.id, 'weld-a')
    assert.ok(!menus.secondaryMenus.some(menu => ['admin-home', 'automation', 'pdm-statistics', '/admin/settings'].includes(menu.key)))
  }
  assert.ok(menus.secondaryMenus.some(menu => menu.key === 'database-monitors'))
  assert.deepEqual(menus.primaryMenus.map(menu => menu.key), ['admin-home', 'automation', 'type:cutting', 'type:welding', 'pdm-statistics'])
  assert.deepEqual(menus.navigationMenus.map(menu => menu.key), ['admin-home', 'automation', 'type:cutting', 'type:welding', 'pdm-statistics', '/admin/settings'])
  for (const [key, icon] of [['type:cutting', 'laser-cutter'], ['type:welding', 'welding-torch']]) {
    const group = menus.primaryMenus.find(menu => menu.key === key)
    assert.equal(group.icon().props.name, icon)
    assert.ok(group.children.every(line => line.icon().props.name === icon))
  }
  assert.equal(menus.primaryMenus[1].label, '报表推送')
  await router.push('/admin/settings/automation?page=2')
  assert.equal(router.currentRoute.value.fullPath, '/admin/automation?page=2')
  assert.equal(menus.primaryKey, 'automation')
  assert.equal(menus.secondaryMenus.length, 0)
  const previous = router.currentRoute.value.fullPath
  selected.items = [...selected.items]
  assert.equal(router.currentRoute.value.fullPath, previous)
  router.back(); await new Promise(setImmediate)
  assert.equal(menus.primaryKey, '/admin/settings')
})

test('line navigation excludes public items and the single-drawer tree exposes the current business routes', async () => {
  const router = setupRouter(), selected = selection()
  const menus = setupMenus(router, selected)
  await router.push('/admin/cutting/home')
  assert.equal(menus.primaryKey, 'line:cut-a')
  assert.ok(menus.secondaryMenus.some(menu => menu.key === 'cutting-home'))
  assert.ok(menus.secondaryMenus.some(menu => menu.key === 'cutting-dispatch'))
  assert.ok(!menus.secondaryMenus.some(menu => ['admin-home', 'automation', 'pdm-statistics', '/admin/settings'].includes(menu.key)))
  assert.deepEqual(menus.getSelectedMenuKeyPath('workshop-report', menus.navigationMenus), ['type:cutting', 'line:cut-a', '/admin/reports', 'workshop-report'])
  selected.id = 'weld-a'
  await router.push('/admin/welding/reports/daily')
  assert.equal(menus.primaryKey, 'line:weld-a')
  assert.ok(!menus.secondaryMenus.some(menu => ['automation', 'cutting-home', 'cutting-dispatch', 'alarm-center'].includes(menu.key)))
  assert.ok(menus.secondaryMenus.some(menu => menu.key === 'welding-home'))
  assert.deepEqual(menus.getSelectedMenuKeyPath('welding-daily', menus.navigationMenus), ['type:welding', 'line:weld-a', '/admin/welding/reports', 'welding-daily'])
})

test('entering a line from a shared module navigates even for the remembered line and cancellation restores selection', async () => {
  const router = setupRouter(), selected = selection()
  await router.push('/admin/home')
  let queryDate = '2026-11-01'
  const script = read('composables/useDeviceSelection.ts').replace(/^import .*$/gm, '').replace('export function', 'function')
  const bindings = { lineHome, selectedLineType, navigationSection, isNavigationFailure, NavigationFailureType, onMounted() {}, ref, useRoute: () => new Proxy({}, { get: (_, key) => router.currentRoute.value[key] }), useRouter: () => router,
    deviceSelection: selected, changeDevice: id => { selected.id = id }, loadDevices() {}, allowDeviceSwitch: async () => true, deviceSwitchQuery: () => ({ start_date: queryDate }) }
  const api = scriptFunction(...Object.keys(bindings), `${script}; return useDeviceSelection()`)(...Object.values(bindings))
  await api.enterLine('cut-a')
  assert.equal(router.currentRoute.value.path, '/admin/cutting/home')
  await api.enterLine('cut-b')
  assert.equal(selected.id, 'cut-b', 'same-type homes share a URL but must still switch line')
  await api.enterLine('cut-a')
  await router.push('/admin/reports/workshop')
  await api.switchDevice('cut-b')
  assert.equal(selected.id, 'cut-b')
  assert.equal(router.currentRoute.value.query.start_date, '2026-11-01')
  await api.switchDevice('cut-a')
  assert.equal(selected.id, 'cut-a', 'an unchanged filter URL must not prevent switching')
  await api.switchDevice('cut-b')
  const removeGuard = router.beforeEach(() => false)
  await api.enterLine('weld-a')
  assert.equal(selected.id, 'cut-b')
  assert.equal(router.currentRoute.value.path, '/admin/reports/workshop')
  queryDate = '2026-10-01'
  await api.switchDevice('cut-a')
  assert.equal(selected.id, 'cut-b')
  removeGuard()
})

test('reopening the mobile menu expands the current line and follows same-type switches', async () => {
  const router = setupRouter(), selected = selection()
  const menus = setupMenus(router, selected)
  await router.push('/admin/cutting/home')
  const app = reactive({ siderCollapse: true })
  const route = new Proxy({}, { get: (_, key) => router.currentRoute.value[key] })
  const script = read('layouts/modules/global-menu/modules/vertical-menu.vue').split('<script setup lang="ts">')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  const bindings = { computed, ref, watch, defineOptions() {}, useRoute: () => route, useAppStore: () => app,
    useThemeStore: () => ({ darkMode: false, sider: {} }), useRouteStore: () => menus,
    useRouterPush: () => ({}), useMenu: () => ({ selectedKey: computed(() => router.currentRoute.value.name) }) }
  const scope = effectScope()
  try {
    const expanded = scope.run(() => scriptFunction(...Object.keys(bindings), `${script}; return expandedKeys`)(...Object.values(bindings)))
    assert.deepEqual(expanded.value, [])
    app.siderCollapse = false; await nextTick()
    assert.deepEqual(expanded.value, ['type:cutting', 'line:cut-a', 'cutting-home'])
    selected.id = 'cut-b'; await nextTick()
    assert.deepEqual(expanded.value, ['type:cutting', 'line:cut-b', 'cutting-home'])
  } finally { scope.stop() }
})

test('configuration requests target their page selection and automation stays shared in welding context', async t => {
  const selected = selection()
  setDeviceCatalog({ default_device_id: 'weld-a', items: selected.items })
  t.after(resetDeviceContext)
  const calls = []
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push({ url, ...options })
    return new Response(JSON.stringify({ success: true, data: {} }), { headers: { 'Content-Type': 'application/json' } })
  })
  await getDatabaseConfiguration('cut-b')
  await saveDatabaseConfiguration({}, 'cut-b')
  await testWeldingConnection({}, 'weld-a')
  await getAutomationStatus()
  await saveAutomationSchedule({ device_id: 'cut-a' })
  assert.deepEqual(calls.map(call => call.headers['X-Production-Line']), ['cut-b', 'cut-b', 'weld-a', '', ''])
  assert.equal(JSON.parse(calls.at(-1).body).device_id, 'cut-a')
  assert.equal(getDeviceId(), 'weld-a')
})
