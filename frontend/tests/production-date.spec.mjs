import assert from 'node:assert/strict'
import { test } from 'vitest'
import { taskProductionDate } from '../src/api/reportDates.ts'

test('night-shift task actions target the production day rather than its start day', () => {
  const task = { production_date: '2026-09-30', production_time: '2026-09-29T23:30:00' }
  assert.equal(taskProductionDate(task), '2026-09-30')
})

test('legacy task responses and missing timestamps retain a usable action date', () => {
  assert.equal(taskProductionDate({ production_time: '2026-09-29T23:30:00' }), '2026-09-29')
  assert.equal(taskProductionDate({ production_date: '2026-09-30', production_time: null }), '2026-09-30')
  assert.equal(taskProductionDate(null, '2026-09-30'), '2026-09-30')
})
