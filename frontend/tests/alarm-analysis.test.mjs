import assert from 'node:assert/strict'
import { test } from 'node:test'
import { formatDeviceDisplay } from '../src/utils/deviceDisplay.js'
import {
  alarmConfidencePercent,
  latestModelAlarmReport,
} from '../src/utils/alarmAnalysis.js'

function report(overrides = {}) {
  return {
    report_id: 'alarm-report',
    status: 'completed',
    model_status: 'model',
    completed_at: '2026-09-29T08:00:00+08:00',
    analysis: {
      summary: '模型摘要 [E001]',
      findings: [],
      actions: [],
      facts: [],
      limitations: [],
    },
    ...overrides,
  }
}

test('device aliases improve page readability without changing source identifiers or report links', () => {
  const source = {
    machine_no: 'Cutter01',
    pdf_filename: 'Cutter01_智能告警报告_20261007.pdf',
    download_url: '/api/alarm-reports/Cutter01/download',
  }
  const before = structuredClone(source)
  assert.equal(formatDeviceDisplay(source.machine_no), '20KW切割机')
  assert.equal(formatDeviceDisplay(source.pdf_filename), '20KW切割机_智能告警报告_20261007.pdf')
  assert.equal(formatDeviceDisplay('监控Cutter01运行，cutter01告警 [E001]'), '监控20KW切割机运行，20KW切割机告警 [E001]')
  assert.equal(formatDeviceDisplay('Cutter02、Cutter010、OtherCutter01、Cutter01A'), 'Cutter02、Cutter010、OtherCutter01、Cutter01A')
  assert.equal(formatDeviceDisplay(null), '')
  assert.deepEqual(source, before, 'aliases must never rewrite API data or real filenames and links')
})

test('alarm center selects the newest completed model result instead of generated or failed reports', () => {
  const newest = report({ report_id: 'newest', completed_at: '2026-09-29T10:00:00+08:00' })
  const selected = latestModelAlarmReport([
    report({ report_id: 'older' }),
    report({ report_id: 'generating', status: 'generating', completed_at: '2026-09-29T11:00:00+08:00' }),
    report({ report_id: 'not-model', model_status: 'fallback', completed_at: '2026-09-29T12:00:00+08:00' }),
    newest,
  ])

  assert.equal(selected, newest)
  assert.equal(latestModelAlarmReport([report({ analysis: null })]), null)
})

test('alarm confidence is shown as a bounded percentage without inventing invalid values', () => {
  assert.equal(alarmConfidencePercent(0.6), 60)
  assert.equal(alarmConfidencePercent('0.995'), 100)
  assert.equal(alarmConfidencePercent(-0.1), null)
  assert.equal(alarmConfidencePercent(1.1), null)
  assert.equal(alarmConfidencePercent('unknown'), null)
})
