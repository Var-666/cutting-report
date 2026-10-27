import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { parse } from '@vue/compiler-sfc'
import {
  mergeOwnedQuery,
  queryChoice,
  queryDate,
  queryDateRange,
  queryPositiveInteger,
  sameRouteQuery,
} from '../src/utils/reportRouteState.ts'

function viewSource(name) {
  const source = readFileSync(new URL(`../src/views/${name}`, import.meta.url), 'utf8')
  return parse(source).descriptor.scriptSetup.content
}

test('shared report query parsing rejects links that cannot restore a valid report state', () => {
  assert.equal(queryDate('2024-02-29'), '2024-02-29')
  assert.equal(queryDate('2023-02-29'), '')
  assert.equal(queryDate(['2026-09-01', '2026-09-02']), '2026-09-01')
  assert.deepEqual(queryDateRange({ start: '2026-09-01', end: '2026-09-30' }), {
    start: '2026-09-01',
    end: '2026-09-30',
  })
  assert.equal(queryDateRange({ start: '2026-09-30', end: '2026-09-01' }), null)
  assert.equal(queryDateRange({ start: '2026-01-01', end: '2026-02-01' }, { maxDays: 31 }), null)
  assert.equal(queryChoice('line', ['bar', 'line'], 'bar'), 'line')
  assert.equal(queryChoice('pie', ['bar', 'line'], 'bar'), 'bar')
  assert.equal(queryPositiveInteger('3'), 3)
  assert.equal(queryPositiveInteger('0'), 1)
})

test('report-owned query updates preserve unrelated deep-link state and omit defaults', () => {
  const query = mergeOwnedQuery(
    { job: 'report-7', page: '4', page_size: '20', source: 'notice' },
    ['page', 'page_size'],
    { page: '', page_size: 50 },
  )

  assert.deepEqual(query, { job: 'report-7', source: 'notice', page_size: '50' })
  assert.equal(sameRouteQuery(query, { source: 'notice', page_size: '50', job: 'report-7' }), true)
  assert.equal(sameRouteQuery(query, { job: 'report-7', source: 'notice' }), false)
})

test('report views restore only applied state and keep worker task routing unchanged', () => {
  const leadership = viewSource('LeadershipReportView.vue')
  const equipment = viewSource('EnergyPeriodReportView.vue')
  const workshop = viewSource('WorkshopView.vue')
  const center = viewSource('ReportCenterView.vue')

  assert.match(leadership, /if \(syncRoute\) await syncRouteState\(\)/)
  assert.match(leadership, /watch\(\(\) => route\.query/)
  assert.match(equipment, /queryDateRange\(route\.query, \{ maxDays: 31 \}\)/)
  assert.match(equipment, /if \(syncRoute\) await syncRouteState\(\)/)

  assert.match(workshop, /const appliedRouteState = reactive/)
  assert.match(workshop, /if \(isWorker\.value \|\| route\.name !== 'workshop-report'\) return/)
  assert.match(workshop, /load\(1, \{ force: true, syncRoute: !isWorker\.value \}\)/)
  assert.match(workshop, /page: data\.page/)

  assert.match(center, /const routeQueryKeys = \['page', 'page_size'\]/)
  assert.match(center, /loadHistory\(target, false, \{ syncRoute: true \}\)/)
  assert.match(center, /pageSize: Number\(queryChoice\(route\.query\.page_size/)
})

test('standard reports distinguish edited dates from the last successfully applied range', () => {
  const querySource = readFileSync(new URL('../src/components/StandardReportQuery.vue', import.meta.url), 'utf8')
  const query = parse(querySource).descriptor
  const leadership = viewSource('LeadershipReportView.vue')
  const equipment = viewSource('EnergyPeriodReportView.vue')
  const workshop = viewSource('WorkshopView.vue')

  assert.match(query.scriptSetup.content, /dateDirty: Boolean/)
  assert.match(query.template.content, /v-if="dateDirty && !loading"/)
  assert.match(query.template.content, /role="status"/)
  assert.match(query.template.content, /条件已修改，尚未更新报表/)
  assert.match(leadership, /periodStart\.value !== appliedPeriodStart\.value \|\| periodEnd\.value !== appliedPeriodEnd\.value/)
  assert.match(equipment, /periodStart\.value !== appliedPeriodStart\.value \|\| periodEnd\.value !== appliedPeriodEnd\.value/)
  assert.match(workshop, /filters\.startDate !== appliedRouteState\.startDate \|\| filters\.endDate !== appliedRouteState\.endDate/)
  for (const name of ['LeadershipReportView.vue', 'EnergyPeriodReportView.vue', 'WorkshopView.vue']) {
    const source = readFileSync(new URL(`../src/views/${name}`, import.meta.url), 'utf8')
    assert.match(source, /:date-dirty="dateQueryDirty"/)
  }
})
