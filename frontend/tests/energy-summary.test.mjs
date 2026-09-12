import assert from 'node:assert/strict'
import test from 'node:test'
import { aggregateEnergyRowsByMonth } from '../src/utils/energySummary.js'

const channel = (value, valid, expected) => ({
  value,
  valid_count: valid,
  complete_count: valid,
  expected_count: expected,
  estimated_count: 0,
})

test('daily equipment energy rows aggregate into one row per month', () => {
  const result = aggregateEnergyRowsByMonth([
    { report_date: '2026-01-31', task_count: 2, work_time_seconds: 100, electricity: channel(4, 2, 2), gas: channel(null, 0, 2) },
    { report_date: '2026-02-01', task_count: 1, work_time_seconds: 50, electricity: channel(3, 1, 1), gas: channel(2, 1, 1) },
    { report_date: '2026-02-02', task_count: 2, work_time_seconds: 70, electricity: channel(null, 0, 2), gas: channel(3, 2, 2) },
  ])

  assert.equal(result.length, 2)
  assert.deepEqual(result.map((row) => row.report_date), ['2026-01', '2026-02'])
  assert.deepEqual(result.map((row) => row.task_count), [2, 3])
  assert.deepEqual(result.map((row) => row.work_time_seconds), [100, 120])
  assert.equal(result[0].electricity.value, 4)
  assert.equal(result[0].gas.value, null)
  assert.equal(result[1].electricity.value, 3)
  assert.equal(result[1].electricity.status, 'partial')
  assert.equal(result[1].gas.value, 5)
  assert.equal(result[1].gas.status, 'measured')
})
