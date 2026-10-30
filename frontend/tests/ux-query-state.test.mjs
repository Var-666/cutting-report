import assert from 'node:assert/strict'
import { test } from 'node:test'
import { readFileSync } from 'node:fs'
import { scriptFunction } from './typescript-helpers.mjs'
import * as Vue from 'vue'
import { parse } from '@vue/compiler-sfc'
import { compile } from '@vue/compiler-dom'
import { renderToString } from '@vue/server-renderer'
import { alarmConfidencePercent, latestModelAlarmReport } from '../src/utils/alarmAnalysis.ts'
import { formatChineseDateTime } from '../src/utils/dateDisplay.ts'
import { formatDeviceDisplay } from '../src/utils/deviceDisplay.ts'

function directoryQuery(listFiles) {
  const source = readFileSync(new URL('../src/composables/useCuttingFileBrowser.ts', import.meta.url), 'utf8')
  const code = source.replace(/^import .*$/gm, '').replace('export function', 'function')
  const bindings = { ...Vue, listCuttingFiles: listFiles, fileKey: row => String(row.document_id) }
  const useBrowser = scriptFunction(...Object.keys(bindings), `${code}; return useCuttingFileBrowser`)(...Object.values(bindings))
  const state = useBrowser({ pageActive: Vue.ref(true), systemStatus: Vue.ref(null), isDispatching: () => false, initialProjectId: '', rememberSelectedProject() {}, notice: Vue.ref(''), showNotice() {} })
  state.selectedProject.value = { project_id: 'P1' }
  state.selectedCatalog.value = { catalog_id: 'old' }
  state.rows.value = [{ document_no: 'old.gen' }]
  state.total.value = 40
  return state
}

test('switching directories clears old files immediately and failure cannot restore them', async () => {
  let fail
  const page = directoryQuery(() => new Promise((_resolve, reject) => { fail = reject }))
  const request = page.selectCatalog({ catalog_id: 'new' })
  assert.equal(page.selectedCatalog.value.catalog_id, 'new')
  assert.equal(page.rows.value.length, 0)
  assert.equal(page.total.value, 0)
  assert.equal(page.loading.value, true)
  fail(new Error('PDM unavailable'))
  await request
  assert.equal(page.rows.value.length, 0)
  assert.equal(page.fileError.value, 'PDM unavailable')
  assert.equal(page.loading.value, false)
})

test('file pagination uses applied search until query submission and keeps data on failure', async () => {
  const calls = []
  let rejectQuery = false
  const page = directoryQuery(async query => {
    calls.push(query)
    if (rejectQuery) throw new Error('offline')
    return { items: [{ document_no: `${query.keyword || 'all'}.gen` }], total: 40 }
  })
  page.fileKeyword.value = 'draft'
  await page.changePage(2)
  assert.equal(calls[0].keyword, '')
  assert.equal(page.appliedFileKeyword.value, '')
  assert.equal(page.page.value, 2)
  await page.applyFileSearch()
  assert.equal(calls[1].keyword, 'draft')
  assert.equal(page.appliedFileKeyword.value, 'draft')
  assert.equal(page.page.value, 1)
  page.fileKeyword.value = 'unsubmitted'
  rejectQuery = true
  await page.changePage(2)
  assert.equal(calls[2].keyword, 'draft')
  assert.equal(page.page.value, 1)
  assert.equal(page.rows.value[0].document_no, 'draft.gen')
  assert.equal(page.fileError.value, 'offline')
  rejectQuery = false
  await page.changePage(2)
  assert.equal(page.page.value, 2)
  assert.equal(page.fileError.value, '')
})

const source = readFileSync(new URL('../src/views/AlarmCenterView.vue', import.meta.url), 'utf8')
const script = parse(source).descriptor.scriptSetup.content.replace(/^import .*$/gm, '')
function alarmQuery(overrides = {}) {
  const bindings = {
    ...Vue, onMounted() {}, onBeforeUnmount() {},
    heroImage: '', AppIcon: { render: () => null }, alarmConfidencePercent, latestModelAlarmReport,
    formatChineseDateTime, formatDeviceDisplay, toError: value => value,
    getAlarmStatus: async () => ({ enabled: true, worker_running: true }),
    listAlarmIncidentPage: async ({ page }) => ({ items: [{ incident_id: `i${page}` }], page, total: 30, page_count: 2 }),
    listAlarmReportPage: async ({ page }) => ({ items: [{ report_id: `r${page}` }], page, total: 20, page_count: 2 }),
    listAlarmReports: async () => [], ...overrides,
  }
  const names = [...script.matchAll(/^(?:const|let|(?:async )?function) (\w+)/gm)].map(match => match[1])
  return scriptFunction(...Object.keys(bindings), `${script}; pageActive = true; return { ${names.join(',')}, heroImage, AppIcon, alarmConfidencePercent, formatDeviceDisplay };`)(...Object.values(bindings))
}

test('alarm pagination commits page numbers only after success and preserves data on failure', async () => {
  let fail = false
  const calls = []
  const page = alarmQuery({ listAlarmIncidentPage: async ({ page }) => {
    calls.push(page)
    if (fail) throw new Error('offline')
    return { items: [{ incident_id: `i${page}` }], page, total: 30, page_count: 2 }
  } })
  await page.loadData()
  fail = true
  await page.changeIncidentPage(2)
  assert.equal(page.incidentPage.value, 1)
  assert.equal(page.incidents.value[0].incident_id, 'i1')
  await page.changeReportPage(2)
  assert.equal(page.reportPage.value, 1)
  assert.equal(page.reportItems.value[0].report_id, 'r1')
  assert.equal(page.error.value, 'offline')
  fail = false
  await page.changeIncidentPage(2)
  assert.equal(page.incidentPage.value, 2)
  assert.equal(page.incidents.value[0].incident_id, 'i2')
  await page.changeReportPage(2)
  assert.equal(page.reportPage.value, 2)
  assert.equal(page.reportItems.value[0].report_id, 'r2')
  assert.equal(page.error.value, '')
  assert.deepEqual(calls.slice(0, 3), [1, 2, 1])
})

test('initial alarm failure renders unknown instead of healthy empty states and retry recovers', async () => {
  let fail = true
  const page = alarmQuery({ getAlarmStatus: async () => {
    if (fail) throw new Error('offline')
    return { enabled: true, worker_running: true }
  }, listAlarmIncidentPage: async () => ({ items: [], page: 1 }), listAlarmReportPage: async () => ({ items: [], page: 1 }) })
  const render = scriptFunction('Vue', compile(parse(source).descriptor.template.content, { prefixIdentifiers: true }).code)(Vue)
  async function renderedText() {
    const app = Vue.createSSRApp({ setup: () => page, render })
    for (const name of ['NButton', 'NRadioGroup', 'NRadioButton']) app.component(name, { render() { return this.$slots.default?.() } })
    app.component('AppIcon', { render: () => null })
    return (await renderToString(app)).replace(/<[^>]*>/g, '')
  }
  await page.loadData()
  const failedText = await renderedText()
  assert.match(failedText, /状态未知/)
  assert.match(failedText, /告警事件读取失败/)
  assert.match(failedText, /告警报告读取失败/)
  assert.doesNotMatch(failedText, /状态正常|暂无告警|暂无实体告警报告/)
  fail = false
  await page.loadData()
  const recoveredText = await renderedText()
  assert.match(recoveredText, /状态正常/)
  assert.match(recoveredText, /暂无告警/)
  assert.doesNotMatch(recoveredText, /读取失败|状态未知/)
})
