import { scriptFunction } from './typescript-helpers.mjs'
import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync, readdirSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { fileURLToPath } from 'node:url'
import { buildSync, transformSync } from 'esbuild'
import * as Vue from 'vue'
import { createPinia, defineStore, setActivePinia } from 'pinia'
import { NConfigProvider, darkTheme, useThemeVars } from 'naive-ui'
import { renderToString } from '@vue/server-renderer'
import { defu } from 'defu'
import { resetSetupStore } from '../src/store/plugins/index.ts'

const src = path => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8')
function nativeModule(path, bindings, names) {
  const source = src(path).replace(/^import[\s\S]*?from ['"][^'"]+['"];?\s*$/gm, '').replace(/\bexport /g, '')
  const code = transformSync(source, { loader: 'ts', define: { 'import.meta.env.PROD': 'true', BUILD_TIME: '"test-build"' } }).code
  const entries = Object.entries(bindings).filter(([key]) => /^[\w$]+$/.test(key))
  return scriptFunction(...entries.map(([key]) => key), `${code}\nreturn { ${names} }`)(...entries.map(([, value]) => value))
}
const colorsBundle = buildSync({ entryPoints: [fileURLToPath(new URL('../src/vendor/soybean-admin/packages/color/index.ts', import.meta.url))], bundle: true, write: false, format: 'cjs', platform: 'node' }).outputFiles[0].text
const colorModule = { exports: {} }
scriptFunction('module', 'exports', colorsBundle)(colorModule, colorModule.exports)
const colors = colorModule.exports
const { themeSettings } = nativeModule('theme/settings.ts', {}, 'themeSettings')
const { themeVars } = nativeModule('theme/vars.ts', {}, 'themeVars')
const flush = () => Vue.nextTick()

function mountTheme(saved, storageAdapter) {
  const values = new Map(saved ? [['themeSettings', saved]] : [])
  const localStg = storageAdapter || { get: key => values.get(key) ?? null, set: (key, value) => values.set(key, JSON.parse(JSON.stringify(value))) }
  const classes = new Set()
  let style
  const document = { documentElement: { style: {}, classList: { add: name => classes.add(name), remove: name => classes.delete(name) } }, querySelector: () => style, createElement: () => ({}), head: { appendChild: element => { style = element } } }
  const defaults = JSON.parse(JSON.stringify(themeSettings))
  const shared = nativeModule('store/modules/theme/shared.ts', {
    ...colors, defu, DARK_CLASS: 'dark', themeSettings: defaults, overrideThemeSettings: {}, themeVars, localStg, document,
    toggleHtmlClass: name => ({ add: () => classes.add(name), remove: () => classes.delete(name) }),
  }, 'initThemeSettings, createThemeToken, addThemeVarsToGlobal, toggleCssDarkMode, toggleAuxiliaryColorModes, getNaiveTheme')
  const osTheme = Vue.ref('light')
  const events = {}
  let timerRunning = false
  const { useThemeStore } = nativeModule('store/modules/theme/index.ts', {
    ...Vue, ...shared, ...colors, defineStore, localStg, SetupStoreId: { Theme: 'theme-store' }, window: {},
    useAuthStore: () => ({ userInfo: { userName: '测试管理员' } }),
    usePreferredColorScheme: () => osTheme,
    useNow: () => ({ now: Vue.ref(new Date()), pause: () => { timerRunning = false }, resume: () => { timerRunning = true } }),
    useDateFormat: () => Vue.ref('2026-10-23 13:00'),
    useEventListener: (_target, event, callback) => { events[event] = callback },
  }, 'useThemeStore')
  const pinia = createPinia().use(resetSetupStore)
  Vue.createApp({}).use(pinia)
  setActivePinia(pinia)
  const scope = Vue.effectScope()
  const store = scope.run(() => useThemeStore())
  return { store, shared, osTheme, classes, document, values, events, timerRunning: () => timerRunning, style: () => style.textContent, close: () => { pinia._s.clear(); scope.stop() } }
}

test('official drawer, application defaults, presets and token helpers match the source manifest', () => {
  assert.equal(themeSettings.layout.mode, 'vertical-mix')
  assert.equal(themeSettings.sider.mixWidth, 64)
  assert.equal(themeSettings.sider.mixCollapsedWidth, 48)
  assert.equal(themeSettings.sider.mixChildMenuWidth, 180)
  const manifest = JSON.parse(src('vendor/soybean-admin/theme-source-manifest.json'))
  assert.equal(Object.keys(manifest.files).filter(path => path.startsWith('src/layouts/modules/theme-drawer/')).length, 20)
  for (const [path, expected] of Object.entries(manifest.files)) {
    assert.equal(createHash('sha256').update(src(path.slice(4))).digest('hex'), expected, path)
  }
})

test('all Naive components required by the native drawer and companion modules are registered', () => {
  const registry = src('vendor/soybean-admin/components.ts')
  function check(folder) {
    for (const item of readdirSync(new URL(`../src/${folder}`, import.meta.url), { withFileTypes: true })) {
      if (item.isDirectory()) check(`${folder}/${item.name}`)
      else if (item.name.endsWith('.vue')) {
        for (const [, tag] of src(`${folder}/${item.name}`).matchAll(/<(N[A-Z]\w+)/g)) assert.ok(registry.includes(tag), `${folder}/${item.name}: ${tag}`)
      }
    }
  }
  for (const folder of ['layouts/modules/theme-drawer', 'layouts/modules/global-menu', 'layouts/modules/global-search', 'components/common', 'components/custom']) check(folder)
})

test('native storage adapter migrates valid existing preferences and ignores malformed legacy colors', () => {
  for (const color of ['#18a058', 'url(invalid)']) {
    const values = new Map([['cutting-theme', JSON.stringify({ color, dark: true })]])
    const storage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) }
    const { localStg } = nativeModule('utils/storage.ts', { window: { localStorage: storage, sessionStorage: storage } }, 'localStg')
    assert.deepEqual(localStg.get('themeSettings'), color.startsWith('#') ? { themeColor: color, themeScheme: 'dark' } : null)
  }
})

test('official store restores every setting, follows the OS and injects full light/dark palettes', async () => {
  const app = mountTheme({ themeScheme: 'auto', themeRadius: 12, layout: { mode: 'horizontal' }, otherColor: { success: '#18a058' } })
  try {
    const { store } = app
    assert.equal(store.layout.mode, 'horizontal')
    assert.equal(store.layout.scrollMode, 'content')
    assert.equal(store.themeRadius, 12)
    assert.equal(store.darkMode, false)
    app.osTheme.value = 'dark'
    await flush()
    assert.equal(store.darkMode, true)
    assert.ok(app.classes.has('dark'))
    assert.match(app.style(), /html.dark/)
    for (const color of ['primary', 'info', 'success', 'warning', 'error']) {
      for (const shade of [50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950]) assert.ok(app.style().includes(`--${color}-${shade}-color:`))
    }
    store.setGrayscale(true)
    store.setColourWeakness(true)
    await flush()
    assert.equal(app.document.documentElement.style.filter, 'grayscale(100%) invert(80%)')
    store.setThemeScheme('light')
    await flush()
    assert.equal(app.classes.has('dark'), false)
    app.events.beforeunload()
    assert.equal(app.values.get('themeSettings').themeRadius, 12)
    assert.deepEqual(Object.keys(JSON.parse(store.settingsJson)).sort(), Object.keys(themeSettings).sort())
  } finally { app.close() }
})

test('semantic colors, recommendation algorithm, custom tokens and Naive radius update together', async () => {
  const app = mountTheme()
  try {
    const { store } = app
    store.updateThemeColors('primary', '#3b82f6')
    store.isInfoFollowPrimary = false
    store.updateThemeColors('info', '#06b6d4')
    store.updateThemeColors('success', '#18a058')
    store.themeRadius = 11
    await flush()
    assert.equal(store.themeColors.info, '#06b6d4')
    assert.equal(store.naiveTheme.common.successColor, '#18a058')
    assert.equal(store.naiveTheme.common.borderRadius, '11px')
    assert.equal(store.naiveTheme.Tag.borderRadius, '11px')
    store.recommendColor = true
    store.updateThemeColors('primary', '#123abc')
    await flush()
    assert.equal(store.themeColor, colors.getPaletteColorByNumber('#123abc', 500, true))
    store.tokens.light.colors.layout = 'rgb(230, 231, 232)'
    await flush()
    assert.match(app.style(), /--layout-bg-color: 230 231 232/)
    for (const dark of [false, true]) {
      let resolved
      await renderToString(Vue.createSSRApp({ render: () => Vue.h(NConfigProvider, { theme: dark ? darkTheme : null, themeOverrides: store.naiveTheme }, { default: () => Vue.h({ setup() { resolved = useThemeVars().value; return () => null } }) }) }))
      assert.equal(resolved.primaryColor, store.themeColor)
      assert.equal(resolved.borderRadius, '11px')
      assert.ok(resolved.cardColor)
    }
  } finally { app.close() }
})

test('reset and preset overrides preserve native behavior and watermark modes are mutually exclusive', async () => {
  const app = mountTheme()
  try {
    const { store } = app
    store.watermark.visible = true
    store.setWatermarkEnableTime(true)
    await flush()
    assert.equal(store.watermarkContent, '2026-10-23 13:00')
    assert.equal(app.timerRunning(), true)
    store.setWatermarkEnableUserName(true)
    await flush()
    assert.equal(store.watermark.enableTime, false)
    assert.equal(store.watermarkContent, '测试管理员')
    assert.equal(app.timerRunning(), false)
    store.setNaiveThemeOverrides({ common: { borderRadius: '15px' } })
    assert.equal(store.naiveTheme.common.borderRadius, '15px')
    store.setThemeLayout('vertical-mix')
    store.themeRadius = 16
    store.resetStore()
    await flush()
    assert.equal(store.layout.mode, themeSettings.layout.mode)
    assert.equal(store.themeRadius, themeSettings.themeRadius)
    assert.equal(store.naiveTheme.common.borderRadius, '6px')
  } finally { app.close() }
})

test('light canvas separates white cards and migrates only the former default token', async () => {
  assert.equal(themeSettings.tokens.light.colors.layout, 'rgb(241, 245, 249)')
  for (const [savedColor, expected] of [['rgb(247, 250, 252)', '241 245 249'], ['rgb(230, 231, 232)', '230 231 232']]) {
    const app = mountTheme({ tokens: { light: { colors: { layout: savedColor } } } })
    try {
      assert.ok(app.style().includes(`--layout-bg-color: ${expected}`))
      assert.match(app.style(), /--layout-bg-color: 18 18 18/)
      app.store.setThemeScheme('dark')
      await flush()
      assert.equal(app.store.darkMode, true)
    } finally { app.close() }
  }
})

function browserStorage(initial = {}) {
  const values = new Map(Object.entries(initial))
  const fail = { getItem: false, setItem: false, removeItem: false, clear: false }
  const guard = method => { if (fail[method]) throw new DOMException('storage blocked', method === 'setItem' ? 'QuotaExceededError' : 'SecurityError') }
  return { values, fail, storage: {
    getItem(key) { guard('getItem'); return values.get(key) ?? null },
    setItem(key, value) { guard('setItem'); values.set(key, value) },
    removeItem(key) { guard('removeItem'); values.delete(key) },
    clear() { guard('clear'); values.clear() },
  } }
}

for (const type of ['local', 'session']) {
  test(`${type} storage acquisition failures allow module initialization and memory-only theme startup`, async () => {
    const browser = browserStorage({ 'cutting:themeSettings': JSON.stringify({ themeScheme: 'dark', themeRadius: 17 }) })
    let blocked = true
    const window = {}
    for (const property of ['localStorage', 'sessionStorage']) Object.defineProperty(window, property, { get() { if (blocked) throw new DOMException('blocked', 'SecurityError'); return browser.storage } })
    const adapters = nativeModule('utils/storage.ts', { window }, 'localStg, sessionStg')
    const storage = type === 'local' ? adapters.localStg : adapters.sessionStg
    storage.set('themeColor', '#123456')
    assert.equal(storage.get('themeColor'), '#123456')
    storage.remove('themeColor')
    assert.equal(storage.get('themeColor'), null)
    const app = mountTheme(undefined, adapters.localStg)
    try {
      app.store.setThemeScheme('dark')
      await Vue.nextTick()
      assert.equal(app.store.darkMode, true)
      blocked = false
      app.events.beforeunload()
      assert.equal(JSON.parse(browser.values.get('cutting:themeSettings')).themeRadius, 17, 'recovering storage must not persist fallback defaults over a saved theme')
      assert.equal(browser.values.size, 1)
      const reloaded = nativeModule('utils/storage.ts', { window }, 'localStg')
      assert.equal(reloaded.localStg.get('themeSettings').themeRadius, 17)
    } finally { app.close() }
  })

  for (const method of ['getItem', 'setItem', 'removeItem', 'clear']) {
    test(`${type} ${method} failures keep cached values in memory and preserve native data after recovery`, () => {
      const browser = browserStorage({ 'cutting:themeColor': JSON.stringify('#18a058'), unrelated: 'preserve' })
      const window = { localStorage: browser.storage, sessionStorage: browser.storage }
      const adapters = nativeModule('utils/storage.ts', { window }, 'localStg, sessionStg')
      const storage = type === 'local' ? adapters.localStg : adapters.sessionStg
      assert.equal(storage.get('themeColor'), '#18a058')
      browser.fail[method] = true
      if (method === 'getItem') assert.equal(storage.get('themeColor'), '#18a058')
      if (method === 'setItem') { storage.set('themeColor', '#123456'); assert.equal(storage.get('themeColor'), '#123456') }
      if (method === 'removeItem') { storage.remove('themeColor'); assert.equal(storage.get('themeColor'), null) }
      if (method === 'clear') { storage.clear(); assert.equal(storage.get('themeColor'), null) }
      browser.fail[method] = false
      storage.set('themeColor', '#abcdef')
      assert.equal(storage.get('themeColor'), '#abcdef')
      storage.remove('themeColor')
      assert.equal(storage.get('themeColor'), null)
      assert.equal(JSON.parse(browser.values.get('cutting:themeColor')), '#18a058')
      assert.equal(browser.values.get('unrelated'), 'preserve')
    })
  }
}

test('a read SecurityError during module initialization cannot break theme or device context startup', async () => {
  const browser = browserStorage({ 'cutting:themeSettings': JSON.stringify({ themeScheme: 'dark', themeRadius: 19 }) })
  browser.fail.getItem = true
  const window = { localStorage: browser.storage, sessionStorage: browser.storage }
  const adapters = nativeModule('utils/storage.ts', { window }, 'localStg, sessionStg')
  const app = mountTheme(undefined, adapters.localStg)
  try {
    await Vue.nextTick()
    assert.equal(typeof app.store.darkMode, 'boolean')
    const code = src('api/deviceContext.ts').replace(/^import .*$/gm, '').replace(/\bexport /g, '')
    const context = scriptFunction('globalThis', `${code}\nreturn { setDeviceCatalog, selectDevice, deviceUrl }`)({ sessionStorage: browser.storage })
    context.setDeviceCatalog({ default_device_id: 'Cutter01', items: [{ id: 'Cutter01' }, { id: 'Cutter02' }] })
    context.selectDevice('Cutter02')
    assert.match(context.deviceUrl('/api/v1/workshop/tasks?report_date=2026-10-27'), /device_id=Cutter02/)
    browser.fail.getItem = false
    app.events.beforeunload()
    assert.equal(JSON.parse(browser.values.get('cutting:themeSettings')).themeRadius, 19)
    const fresh = nativeModule('utils/storage.ts', { window }, 'localStg')
    const restored = mountTheme(undefined, fresh.localStg)
    try { assert.equal(restored.store.themeRadius, 19); assert.equal(restored.store.darkMode, true) }
    finally { restored.close() }
  } finally { app.close() }
})
