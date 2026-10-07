import assert from 'node:assert/strict'
import { test } from 'node:test'
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
