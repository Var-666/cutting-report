// @vitest-environment jsdom

import assert from 'node:assert/strict'
import { afterEach, beforeEach, test, vi } from 'vitest'
import * as Vue from 'vue'
import { createPinia, disposePinia, setActivePinia } from 'pinia'
import { NConfigProvider, darkTheme, useThemeVars } from 'naive-ui'
import { renderToString } from '@vue/server-renderer'
import { getPaletteColorByNumber } from '@sa/color'
import { resetSetupStore } from '../src/store/plugins/index.ts'
import { themeSettings } from '../src/theme/settings.ts'

const flush = Vue.nextTick
const flushStorage = () => window.dispatchEvent(new Event('beforeunload'))

beforeEach(() => {
  vi.resetModules()
  vi.stubEnv('PROD', true)
  localStorage.clear()
  sessionStorage.clear()
})
afterEach(() => {
  vi.unstubAllEnvs()
  vi.useRealTimers()
  document.documentElement.classList.remove('dark')
  document.documentElement.style.removeProperty('filter')
  document.getElementById('theme-vars')?.remove()
  setActivePinia(undefined)
})

async function mountTheme(saved) {
  if (saved) localStorage.setItem('cutting:themeSettings', JSON.stringify(saved))
  const dark = new EventTarget()
  dark.matches = false
  vi.stubGlobal('matchMedia', query => query.includes('dark') ? dark : Object.assign(new EventTarget(), { matches: false }))
  const { session } = await import('../src/state/session.ts')
  session.user = { display_name: '测试管理员' }
  const { useThemeStore } = await import('../src/store/modules/theme/index.ts')
  const pinia = createPinia().use(resetSetupStore)
  Vue.createApp({}).use(pinia)
  const store = useThemeStore(pinia)
  return {
    store,
    switchOsToDark() {
      dark.matches = true
      dark.dispatchEvent(Object.assign(new Event('change'), { matches: true }))
    },
    color(property, selector = ':root') {
      const rule = [...document.getElementById('theme-vars').sheet.cssRules].find(rule => rule.selectorText === selector)
      return rule.style.getPropertyValue(property).trim()
    },
    close: () => disposePinia(pinia),
  }
}

for (const [color, expected] of [
  ['#18a058', { themeColor: '#18a058', themeScheme: 'dark' }],
  ['url(invalid)', null],
]) test(`legacy preference ${color} is migrated only when its color is valid`, async () => {
  localStorage.setItem('cutting-theme', JSON.stringify({ color, dark: true }))
  const { localStg } = await import('../src/utils/storage.ts')
  assert.deepEqual(localStg.get('themeSettings'), expected)
})

test('official store restores every setting, follows the OS and injects full light/dark palettes', async () => {
  const app = await mountTheme({ themeScheme: 'auto', themeRadius: 12, layout: { mode: 'horizontal' }, otherColor: { success: '#18a058' } })
  try {
    const { store } = app
    assert.equal(store.layout.mode, 'horizontal')
    assert.equal(store.layout.scrollMode, 'content')
    assert.equal(store.themeRadius, 12)
    assert.equal(store.darkMode, false)
    app.switchOsToDark()
    await flush()
    assert.equal(store.darkMode, true)
    assert.ok(document.documentElement.classList.contains('dark'))
    for (const color of ['primary', 'info', 'success', 'warning', 'error']) {
      for (const shade of [50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950]) assert.ok(app.color(`--${color}-${shade}-color`))
    }
    store.setGrayscale(true)
    store.setColourWeakness(true)
    await flush()
    assert.equal(document.documentElement.style.filter, 'grayscale(100%) invert(80%)')
    store.setThemeScheme('light')
    await flush()
    assert.equal(document.documentElement.classList.contains('dark'), false)
    flushStorage()
    assert.equal(JSON.parse(localStorage.getItem('cutting:themeSettings')).themeRadius, 12)
    assert.deepEqual(Object.keys(JSON.parse(store.settingsJson)).sort(), Object.keys(themeSettings).sort())
  } finally { app.close() }
})

test('semantic colors, recommendation algorithm, custom tokens and Naive radius update together', async () => {
  const app = await mountTheme()
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
    assert.equal(store.themeColor, getPaletteColorByNumber('#123abc', 500, true))
    store.tokens.light.colors.layout = 'rgb(230, 231, 232)'
    await flush()
    assert.equal(app.color('--layout-bg-color'), '230 231 232')
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
  vi.useFakeTimers({ toFake: ['Date', 'requestAnimationFrame', 'cancelAnimationFrame'] })
  vi.setSystemTime(new Date(2026, 9, 23, 13, 0))
  const app = await mountTheme()
  try {
    const { store } = app
    store.watermark.visible = true
    store.setWatermarkEnableTime(true)
    await flush()
    assert.equal(store.watermarkContent, '2026-10-23 13:00')
    assert.ok(vi.getTimerCount() > 0)
    store.setWatermarkEnableUserName(true)
    await flush()
    assert.equal(store.watermark.enableTime, false)
    assert.equal(store.watermarkContent, '测试管理员')
    assert.equal(vi.getTimerCount(), 0)
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
    const app = await mountTheme({ tokens: { light: { colors: { layout: savedColor } } } })
    try {
      assert.equal(app.color('--layout-bg-color'), expected)
      assert.equal(app.color('--layout-bg-color', 'html.dark'), '18 18 18')
      app.store.setThemeScheme('dark')
      await flush()
      assert.equal(app.store.darkMode, true)
    } finally { app.close() }
  }
})

for (const property of ['localStorage', 'sessionStorage']) {
  test(`${property} acquisition failure allows startup and preserves stored theme when access recovers`, async () => {
    const browser = window[property]
    localStorage.setItem('cutting:themeSettings', JSON.stringify({ themeScheme: 'dark', themeRadius: 17 }))
    const access = vi.spyOn(window, property, 'get').mockImplementation(() => {
      throw new DOMException('Storage blocked', 'SecurityError')
    })
    const { localStg, sessionStg } = await import('../src/utils/storage.ts')
    const storage = property === 'localStorage' ? localStg : sessionStg
    storage.set('themeColor', '#123456')
    assert.equal(storage.get('themeColor'), '#123456')
    storage.remove('themeColor')
    assert.equal(storage.get('themeColor'), null)
    const app = await mountTheme()
    try {
      app.store.setThemeScheme('dark')
      await flush()
      assert.equal(app.store.darkMode, true)
      access.mockReturnValue(browser)
      flushStorage()
      assert.equal(JSON.parse(localStorage.getItem('cutting:themeSettings')).themeRadius, 17)
    } finally { app.close() }
    vi.resetModules()
    const restored = await mountTheme()
    try {
      assert.equal(restored.store.themeRadius, 17)
      assert.equal(restored.store.darkMode, true)
    } finally { restored.close() }
  })

  for (const method of ['getItem', 'setItem', 'removeItem', 'clear']) {
    test(`${property} ${method} failures keep preferences in memory and preserve native data after recovery`, async () => {
      const browser = window[property]
      browser.setItem('cutting:themeColor', JSON.stringify('#18a058'))
      browser.setItem('unrelated', 'preserve')
      const { localStg, sessionStg } = await import('../src/utils/storage.ts')
      const storage = property === 'localStorage' ? localStg : sessionStg
      assert.equal(storage.get('themeColor'), '#18a058')
      const nativeMethod = vi.spyOn(Storage.prototype, method).mockImplementation(() => {
        throw new DOMException('Storage blocked', method === 'setItem' ? 'QuotaExceededError' : 'SecurityError')
      })
      if (method === 'getItem') assert.equal(storage.get('themeColor'), '#18a058')
      if (method === 'setItem') {
        storage.set('themeColor', '#123456')
        assert.equal(storage.get('themeColor'), '#123456')
      }
      if (method === 'removeItem') {
        storage.remove('themeColor')
        assert.equal(storage.get('themeColor'), null)
      }
      if (method === 'clear') {
        storage.clear()
        assert.equal(storage.get('themeColor'), null)
      }
      nativeMethod.mockRestore()
      storage.set('themeColor', '#abcdef')
      assert.equal(storage.get('themeColor'), '#abcdef')
      storage.remove('themeColor')
      assert.equal(storage.get('themeColor'), null)
      assert.equal(JSON.parse(browser.getItem('cutting:themeColor')), '#18a058')
      assert.equal(browser.getItem('unrelated'), 'preserve')
    })
  }
}

test('read access failure during module initialization allows the theme and device selection to start', async () => {
  localStorage.setItem('cutting:themeSettings', JSON.stringify({ themeScheme: 'dark', themeRadius: 19 }))
  const access = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
    throw new DOMException('Storage blocked', 'SecurityError')
  })
  const { setDeviceCatalog, selectDevice, deviceUrl } = await import('../src/api/deviceContext.ts')
  const app = await mountTheme()
  try {
    assert.equal(typeof app.store.darkMode, 'boolean')
    setDeviceCatalog({ default_device_id: 'Cutter01', items: [{ id: 'Cutter01' }, { id: 'Cutter02' }] })
    selectDevice('Cutter02')
    assert.match(deviceUrl('/api/v1/workshop/tasks?report_date=2026-10-27'), /device_id=Cutter02/)
    access.mockRestore()
    flushStorage()
    assert.equal(JSON.parse(localStorage.getItem('cutting:themeSettings')).themeRadius, 19)
  } finally { app.close() }
})
