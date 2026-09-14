import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  formatChineseDate,
  formatChineseDateTime,
  formatChineseMonth,
  formatTableDateValue,
  sortReportPeriods,
} from '../src/utils/dateDisplay.js'

test('table dates share Chinese month, date and date-time formats', () => {
  assert.equal(formatChineseMonth('2026-09'), '2026年9月')
  assert.equal(formatChineseMonth('2026-09-01'), '2026年9月')
  assert.equal(formatChineseDate('2026-09-01'), '2026年9月1日')
  assert.equal(formatChineseDateTime('2026-09-01T08:03:04+08:00'), '2026年9月1日 08:03:04')
  assert.equal(formatTableDateValue('2026-09-01 08:03'), '2026年9月1日 08:03:00')
  assert.equal(formatTableDateValue('普通文本'), '普通文本')
  assert.equal(formatTableDateValue(null), '--')
})

test('report periods can be ordered newest first without mutating API rows', () => {
  const rows = [{ report_date: '2026-07' }, { report_date: '2026-09' }, { report_date: '2026-08' }]
  const sorted = sortReportPeriods(rows, 'desc')
  assert.deepEqual(sorted.map((row) => row.report_date), ['2026-09', '2026-08', '2026-07'])
  assert.deepEqual(rows.map((row) => row.report_date), ['2026-07', '2026-09', '2026-08'])
})
