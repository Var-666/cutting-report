import assert from 'node:assert/strict'
import test from 'node:test'
import { renderComponent } from './vue-component-helpers.mjs'
import { analysisTaskLocation } from '../src/utils/analysisReferences.ts'

const reference = { kind: 'task', id: 'real-task-1', label: 'real-task-1', device_id: 'Cutter01', period_start: '2026-08-18', period_end: '2026-08-20', production_date: '2026-08-19' }
const brief = {
  headline: '已有切割记录，确认归属待补齐', summary: '生产记录和人员确认是两个不同的统计口径。', key_metric_ids: [],
  scope: { report_type: 'workshop', device_id: 'Cutter01', line_name: '切割一线', period_start: '2026-08-18', period_end: '2026-08-20', period_mode: 'range' },
  findings: [{ candidate_id: 'C_confirmation', title: '待工人确认', detail: '核对实际完成人后补齐确认，未确认不代表切割未完成。', evidence_ids: ['F004'], references: [reference, ...Array.from({ length: 4 }, (_, i) => ({ ...reference, id: `real-task-${i + 2}`, label: `real-task-${i + 2}` }))], reference_count: 5 }],
  actions: [], data_quality: ['工人工时仅包含已确认记录。'],
}
const analysis = { status: 'success', mode: 'model', overview: '旧适配文本', features: '', alerts: '', suggestions: '', facts: [{ id: 'F004', label: '工人确认率', value: '50.0%', source: '当前报表' }], cached: true, generated_at: '2026-08-20 12:00', data_version: 'v', brief }

test('cutting brief renders dynamic prose, actual period and preview count without fixed KPI cards', async () => {
  const html = await renderComponent('components/ReportAnalysis', { analysis, ready: true, loading: false })
  assert.ok(html.includes(brief.headline) && html.includes(brief.summary))
  assert.ok(html.includes('2026-08-18 至 2026-08-20'))
  assert.ok(html.includes('查看全部关联任务（5 项）'))
  assert.ok(!html.includes('real-task-5'))
  assert.ok(!html.includes('核心指标') && !html.includes('建议跟进') && !html.includes('旧适配文本'))
  assert.ok(!html.includes('C_confirmation') && !html.includes('F004'))
})

test('empty findings and actions leave no placeholder headings; selected metrics bind backend facts', async () => {
  const html = await renderComponent('components/CuttingAnalysisBrief', { brief: { ...brief, findings: [], key_metric_ids: ['F004'] }, facts: analysis.facts })
  assert.ok(!html.includes('关键发现') && !html.includes('建议跟进'))
  assert.ok(html.includes('工人确认率') && html.includes('50.0%'))
})

test('task navigation preserves real ID and original range and rejects another production line', () => {
  assert.deepEqual(analysisTaskLocation(reference, 'Cutter01'), { name: 'workshop-report', query: { start: '2026-08-18', end: '2026-08-20', task: 'real-task-1', task_line: 'Cutter01' } })
  assert.equal(analysisTaskLocation(reference, 'Cutter02'), null)
  assert.equal(analysisTaskLocation({ ...reference, kind: 'project' }, 'Cutter01'), null)
})

test('welding/historical analysis still renders its four original fields', async () => {
  const html = await renderComponent('components/ReportAnalysis', { analysis: { ...analysis, brief: null, overview: '焊接概况', features: '工艺结构', alerts: '数据待核对', suggestions: '核对焊接记录' }, loading: false, ready: true })
  for (const text of ['焊接概况', '工艺结构', '数据待核对', '核对焊接记录']) assert.ok(html.includes(text))
})
