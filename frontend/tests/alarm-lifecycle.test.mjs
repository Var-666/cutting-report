import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import * as Vue from 'vue'
import { parse } from '@vue/compiler-sfc'
import { alarmConfidencePercent, latestModelAlarmReport } from '../src/utils/alarmAnalysis.js'
import { formatChineseDateTime } from '../src/utils/dateDisplay.js'

const source = readFileSync(new URL('../src/views/AlarmCenterView.vue', import.meta.url), 'utf8')
const script = parse(source).descriptor.scriptSetup.content.replace(/^import .*$/gm, '')

test('unmounting during alarm initialization aborts the request and cannot recreate timers', async () => {
  let resolveStatus
  let signal
  const pending = new Promise((resolve) => { resolveStatus = resolve })
  const timers = new Map()
  let nextId = 0
  const originalWindow = globalThis.window
  globalThis.window = {
    setTimeout: (callback) => { timers.set(++nextId, callback); return nextId },
    setInterval: (callback) => { timers.set(++nextId, callback); return nextId },
    clearTimeout: (id) => timers.delete(id),
    clearInterval: (id) => timers.delete(id),
  }
  const bindings = {
    ...Vue,
    getAlarmStatus: ({ signal: value }) => { signal = value; return pending },
    listAlarmIncidentPage: async () => ({ items: [], page: 1, all_total: 0 }),
    listAlarmReportPage: async () => ({ items: [], page: 1, total: 0, completed_count: 0, page_count: 1 }),
    listAlarmReports: async () => [],
    heroImage: '', AppIcon: {}, alarmConfidencePercent, latestModelAlarmReport, formatChineseDateTime,
  }
  // Execute the actual SFC setup using Vue's renderer. Only API and asset
  // imports are supplied by the test; lifecycle and reactivity are real Vue.
  const setup = new Function(...Object.keys(bindings), script)
  const renderer = Vue.createRenderer({
    createComment: () => ({}), createText: () => ({}), createElement: () => ({}),
    insert: () => {}, remove: () => {}, setText: () => {}, setElementText: () => {},
    parentNode: () => null, nextSibling: () => null, patchProp: () => {},
  })
  const app = renderer.createApp({
    setup: () => setup(...Object.values(bindings)),
    render: () => null,
  })
  try {
    app.mount({})
    assert.ok(signal)
    app.unmount()
    assert.equal(signal.aborted, true)
    resolveStatus({ enabled: true })
    // Allow Promise.all, loadData and the asynchronous mounted callback to finish.
    for (let index = 0; index < 5; index++) await Promise.resolve()
    await Vue.nextTick()
    assert.equal(timers.size, 0, 'a detached alarm page must not retain any refresh or clock timer')
  } finally {
    resolveStatus({})
    globalThis.window = originalWindow
  }
})
