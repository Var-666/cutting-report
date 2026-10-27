import { scriptFunction } from './typescript-helpers.mjs'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import * as Vue from 'vue'
import { parse } from '@vue/compiler-sfc'
import { compile } from '@vue/compiler-dom'
import { renderToString } from '@vue/server-renderer'
import { alarmConfidencePercent, latestModelAlarmReport } from '../src/utils/alarmAnalysis.ts'
import { formatChineseDateTime } from '../src/utils/dateDisplay.ts'
import { formatDeviceDisplay } from '../src/utils/deviceDisplay.ts'

const source = readFileSync(new URL('../src/views/AlarmCenterView.vue', import.meta.url), 'utf8')
const script = parse(source).descriptor.scriptSetup.content.replace(/^import .*$/gm, '')

test('alarm page uses the device alias in every displayed section while preserving PDF destinations', async () => {
  const incident = { incident_id: 'incident', machine_no: 'Cutter01', severity: 'warning', status: 'open', alarm_desc: '水冷机报警', pdf_report_id: 'report', report_status: 'completed', report_view_url: '/api/reports/Cutter01/view' }
  const report = {
    report_id: 'report', machine_no: 'Cutter01', incident_ids: ['incident'], status: 'completed', model_status: 'model',
    pdf_filename: 'Cutter01_智能告警报告_20261007.pdf',
    download_url: '/api/reports/Cutter01/download', view_url: '/api/reports/Cutter01/view',
    analysis: {
      overall_risk: 'warning', summary: 'Cutter01出现水冷机报警 [E001]',
      findings: [{ title: 'Cutter01水冷机报警', analysis: '检查Cutter01冷却系统', possible_causes: ['Cutter01冷却异常'], confidence: 0.6, evidence_ids: ['E001'] }],
      actions: [{ action: '检查Cutter01冷却水', evidence_ids: ['E001'] }],
      facts: [{ id: 'E001', machine_no: 'Cutter01', alarm_desc: '水冷机报警' }], limitations: ['Cutter01缺少维修记录'],
    },
  }
  const originalReport = structuredClone(report)
  const bindings = { ...Vue, heroImage: '', AppIcon: { render: () => null }, alarmConfidencePercent, latestModelAlarmReport, formatChineseDateTime, formatDeviceDisplay, incident, report }
  const names = [...script.matchAll(/^(?:const|let|(?:async )?function) (\w+)/gm)].map((match) => match[1])
  const setup = scriptFunction(...Object.keys(bindings), `${script}\nstatus.value = { machine_nos: ['Cutter01'] }; incidents.value = [incident]; reports.value = [report]; reportItems.value = [report]; return { ${names.join(',')}, heroImage, AppIcon, alarmConfidencePercent, formatDeviceDisplay };`)
  const render = scriptFunction('Vue', compile(parse(source).descriptor.template.content, { prefixIdentifiers: true }).code)(Vue)
  const app = Vue.createSSRApp({ setup: () => setup(...Object.values(bindings)), render })
  app.component('AppIcon', bindings.AppIcon)
  app.directive('content-table', {})
  const html = await renderToString(app)
  const text = html.replace(/<[^>]*>/g, '')
  assert.doesNotMatch(text, /Cutter01/, 'monitor, table, report names and model evidence must all use the same readable name')
  assert.match(text, /20KW切割机_智能告警报告_20261007.pdf/)
  assert.match(text, /检查20KW切割机冷却系统/)
  assert.match(text, /\[E001\]/)
  assert.match(text, /置信度 60%/)
  assert.match(html, /href="\/api\/reports\/Cutter01\/download"/)
  assert.match(html, /href="\/api\/reports\/Cutter01\/view"/)
  assert.deepEqual(report, originalReport)
})

test('incident report status is independent of alarm resolution and the recent report list', async () => {
  const cases = [
    { incident_id: 'old-completed', pdf_report_id: 'old-report', report_status: 'completed', report_view_url: '/api/v1/alarms/reports/old-report/view', label: '查看' },
    { incident_id: 'generating', pdf_report_id: 'generating-report', report_status: 'generating', report_view_url: '/not-ready', label: '生成中' },
    { incident_id: 'failed', pdf_report_id: 'failed-report', report_status: 'failed', report_error_message: '模型服务不可用', label: '生成失败' },
    { incident_id: 'pending', pdf_report_id: null, label: '待生成' },
    { incident_id: 'missing', pdf_report_id: 'missing-report', report_status: null, label: '报告不存在' },
    { incident_id: 'no-file', pdf_report_id: 'no-file-report', report_status: 'completed', report_view_url: null, label: '报告不可用' },
  ]
  const items = cases.map((item) => ({ ...item, machine_no: 'Cutter01', severity: 'warning', status: 'open' }))
  const bindings = { ...Vue, heroImage: '', AppIcon: { render: () => null }, alarmConfidencePercent, latestModelAlarmReport, formatChineseDateTime, formatDeviceDisplay, items }
  const names = [...script.matchAll(/^(?:const|let|(?:async )?function) (\w+)/gm)].map((match) => match[1])
  const setup = scriptFunction(...Object.keys(bindings), `${script}; incidents.value = items; return { ${names.join(',')}, heroImage, alarmConfidencePercent, formatDeviceDisplay };`)
  const render = scriptFunction('Vue', compile(parse(source).descriptor.template.content, { prefixIdentifiers: true }).code)(Vue)
  const app = Vue.createSSRApp({ setup: () => setup(...Object.values(bindings)), render })
  app.component('AppIcon', bindings.AppIcon)
  app.directive('content-table', {})
  const html = await renderToString(app)
  const rows = [...html.matchAll(/<tr>([\s\S]*?)<\/tr>/g)].map((match) => match[1]).filter((row) => row.includes('<td'))
  assert.equal(rows.length, cases.length)
  for (const [index, item] of cases.entries()) {
    assert.match(rows[index], new RegExp(item.label))
    assert.match(rows[index], /异常/)
    assert.match(rows[index], /待处理/, 'a generated report must never imply the alarm has been handled')
    if (item.incident_id !== 'old-completed') assert.doesNotMatch(rows[index], /href=/, 'only completed reports with a view file are actionable')
  }
  assert.match(rows[0], /href="\/api\/v1\/alarms\/reports\/old-report\/view"/, 'an older completed report remains viewable without loading it into the newest 50 reports')
  assert.match(rows[2], /title="模型服务不可用"/)
  assert.doesNotMatch(html, /已归档/)
})

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
    heroImage: '', AppIcon: {}, alarmConfidencePercent, latestModelAlarmReport, formatChineseDateTime, formatDeviceDisplay,
  }
  // Execute the actual SFC setup using Vue's renderer. Only API and asset
  // imports are supplied by the test; lifecycle and reactivity are real Vue.
  const setup = scriptFunction(...Object.keys(bindings), script)
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
