import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { taskProductionDate } from '../src/api/reportDates.js'

test('night-shift task actions target the production day rather than its start day', () => {
  const task = { production_date: '2026-09-30', production_time: '2026-09-29T23:30:00' }
  assert.equal(taskProductionDate(task), '2026-09-30')
  const source = readFileSync(new URL('../src/views/WorkshopView.vue', import.meta.url), 'utf8')
  const body = source.match(/function rowDate\(row\) \{([^}]+)\}/)[1]
  const actualRowDate = new Function('taskProductionDate', 'filters', 'row', body)
  assert.equal(actualRowDate(taskProductionDate, { reportDate: '2026-10-01' }, task), '2026-09-30')
})

test('legacy task responses and missing timestamps retain a usable action date', () => {
  assert.equal(taskProductionDate({ production_time: '2026-09-29T23:30:00' }), '2026-09-29')
  assert.equal(taskProductionDate({ production_date: '2026-09-30', production_time: null }), '2026-09-30')
  assert.equal(taskProductionDate(null, '2026-09-30'), '2026-09-30')
})
