import assert from 'node:assert/strict'
import test from 'node:test'
import { renderComponent } from './vue-component-helpers.mjs'
import { clonePdmFilters, createPdmFilters, pdmPreferences, validatePdmFilters } from '../src/utils/pdmFilters.ts'
import { pdmQuery } from '../src/api/pdmStatistics.ts'

const totals = { total: 4, completed: 2, on_time: 1, total_rate: 50, on_time_rate: 25 }
function table(grouped = false) {
  return { key: grouped ? 'equipment:all:profession' : 'equipment:summary', kind: 'equipment', title: '设备资料计划完成汇总表', scope_label: grouped ? '所有项目－分专业' : '所有项目', group_label: grouped ? '专业' : null,
    rows: grouped ? [
      { category: '技术协议-按设备计', group_id: 'f1', group_name: '船体', subtotal: false, ...totals },
      { category: '技术协议-按设备计', group_id: '', group_name: '总计', subtotal: true, ...totals },
    ] : [{ category: '技术协议-按设备计', group_id: '', group_name: '', subtotal: false, ...totals }], totals }
}

test('report keeps the document merged title, grouped completion headers and total row', async () => {
  const html = await renderComponent('components/pdm/PdmSummaryTable', { table: table(), startDate: '2026-11-01', endDate: '2026-11-10', queried: true })
  assert.match(html, /colspan="3"[^>]*>设备资料计划完成汇总表/)
  assert.match(html, /colspan="2"[^>]*>完成率/)
  for (const label of ['图纸总数', '已完成数', '按计划完成数', '按时完成率', '总完成率', '2026-11-01 ～ 2026-11-10', '25.00%', '50.00%', '总计']) assert.ok(html.includes(label), label)
  assert.match(html, /<tfoot\b/)
})

test('professional and project reports preserve vertical category spans and per-category totals', async () => {
  for (const group of ['专业', '项目']) {
    const value = table(true); value.group_label = group
    const html = await renderComponent('components/pdm/PdmSummaryTable', { table: value, startDate: '2026-11-01', endDate: '2026-11-10', queried: true })
    assert.match(html, /colspan="4"[^>]*>设备资料计划完成汇总表/)
    assert.match(html, /rowspan="2" scope="rowgroup"[^>]*>技术协议-按设备计/)
    assert.ok(html.includes('船体'))
    assert.doesNotMatch(html, /<tfoot\b/)
  }
})

test('zero denominator stays unavailable and initial empty table explains query state', async () => {
  const value = table(); value.rows[0].on_time_rate = value.rows[0].total_rate = null
  const html = await renderComponent('components/pdm/PdmSummaryTable', { table: value, startDate: '', endDate: '', queried: true })
  assert.ok(html.includes('—'))
  value.rows = []
  const empty = await renderComponent('components/pdm/PdmSummaryTable', { table: value, startDate: '', endDate: '', queried: false })
  assert.ok(empty.includes('选择时间段及筛选条件后查询'))
})

test('filters remember only project/profession per document kind, and owners reset to all', () => {
  const filters = createPdmFilters({ equipment: { projects: ['p1', 'p1', 2], professions: ['f1'], responsiblePeople: ['old'] }, drawing: { projects: ['p2'] } })
  assert.deepEqual(filters.documentKinds, ['equipment', 'drawing'])
  assert.deepEqual(filters.equipment.projects, ['p1'])
  assert.equal(filters.equipment.responsiblePeople, null)
  assert.deepEqual(Object.keys(pdmPreferences(filters).equipment), ['projects', 'professions'])
  const clone = clonePdmFilters(filters)
  clone.equipment.projects.push('new')
  assert.deepEqual(filters.equipment.projects, ['p1'])
})

test('date validation supports long ranges and rejects missing, invalid and reversed dates', () => {
  const filters = createPdmFilters()
  assert.ok(validatePdmFilters(filters))
  Object.assign(filters, { startDate: '2020-01-01', endDate: '2026-11-10' })
  assert.equal(validatePdmFilters(filters), '')
  filters.startDate = '2026-02-30'; assert.ok(validatePdmFilters(filters))
  filters.startDate = '2026-11-11'; assert.ok(validatePdmFilters(filters))
})

test('query contract keeps both kinds, grouping and explicit empty owner selection', () => {
  const filters = createPdmFilters(); filters.equipment.responsiblePeople = []; filters.groupBy = 'profession'
  const payload = pdmQuery(filters)
  assert.deepEqual(payload.document_kinds, ['equipment', 'drawing'])
  assert.deepEqual(payload.equipment.responsible_people, [])
  assert.equal(payload.drawing.responsible_people, null)
  assert.equal(payload.group_by, 'profession')
})

test('filter panels display both sets of required fields and source failures', async () => {
  const html = await renderComponent('components/pdm/PdmFilterBar', { modelValue: createPdmFilters(), status: 'error', error: 'PDM连接失败' })
  for (const label of ['设备资料', '详设图纸', '资料类型', '详设类型', '设备等级', '设备区域', '是否厂商表', '图纸批次', '图纸区域', '图纸类别', '权重', '负责人', '统计所有数据', '按项目统计', '按专业统计', 'PDM连接失败']) assert.ok(html.includes(label), label)
})
