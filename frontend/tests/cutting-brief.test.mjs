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
  assert.match(html, /class="[^"]*\bmodel\b[^"]*"[^>]*>\s*AI 分析/)
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

test('leadership cards show authoritative metrics, actual trends and project evidence', async () => {
  const scope = { ...brief.scope, report_type: 'leadership', comparison_start: '2026-08-15', comparison_end: '2026-08-17' }
  const project = { ...reference, kind: 'project', id: 'P-01', label: 'P-01' }
  const reportBrief = {
    ...brief, scope, key_metric_ids: ['F001', 'F002', 'F003'],
    findings: [{ ...brief.findings[0], references: [project], reference_count: 1 }],
    data_quality: ['当前统计周期尚未结束，仅作进度参考。'],
  }
  const facts = [
    ['F001', '区间任务数', '5 项'], ['F002', '区间切割长度', '12.0 米'],
    ['F003', '区间切割时长', '01:00:00'], ['F007', '区间坡口长度', '2.0 米'],
  ].map(([id, label, value]) => ({ id, label, value }))
  const total = { task_count: 5, cut_length_m: 12, work_time_seconds: 3600, groove_length_m: 2 }
  const leadershipReport = {
    daily_records: [
      { ...total, task_count: 2, period_start: '2026-08-18' },
      { ...total, task_count: 3, period_start: '2026-08-19' },
    ],
    current_month_total: total, comparison_total: { ...total, task_count: 10, cut_length_m: 16 },
    comparison_start: scope.comparison_start, comparison_end: scope.comparison_end,
    project_distribution: [{ project_name: 'P-01', task_count: 3, cut_length_m: 8, work_time_seconds: 1800 }],
  }
  const html = await renderComponent('components/CuttingAnalysisBrief', { brief: reportBrief, facts, leadershipReport })
  for (const text of ['区间坡口长度', '-50.0%', '-25.0%', '基于最近 2 个有效数据点', '周期未结束', '分析依据 / 关联记录', '切割长度 8.0 米 · 切割时长 00:30:00']) {
    assert.ok(html.includes(text), text)
  }
})

test('welding analysis uses only the matching report snapshot for indicators', async () => {
  const weldingReport = {
    data_version: 'current', query: { start: '2026-08-18', end: '2026-08-20' }, daily: [],
    summary: { job_count: 4, weld_length_m: 12.3, involved_part_count: 3, working_hours: 1 },
    meta: { notices: ['按作业结束时间计入生产日。'] },
  }
  const input = { ...analysis, brief: null, facts: [], overview: '焊接生产概况。' }
  for (const matches of [true, false]) {
    const html = await renderComponent('components/ReportAnalysis', {
      analysis: { ...input, data_version: matches ? 'current' : 'old' }, weldingReport, ready: true, loading: false,
    })
    assert.equal(html.includes('焊接作业次数'), matches)
    assert.equal(html.includes('分析依据'), matches)
    assert.ok(html.includes('焊接生产概况'))
  }
})

test('task navigation preserves real ID and original range and rejects another production line', () => {
  assert.deepEqual(analysisTaskLocation(reference, 'Cutter01'), { name: 'workshop-report', query: { start: '2026-08-18', end: '2026-08-20', task: 'real-task-1', task_line: 'Cutter01' } })
  assert.equal(analysisTaskLocation(reference, 'Cutter02'), null)
  assert.equal(analysisTaskLocation({ ...reference, kind: 'project' }, 'Cutter01'), null)
})

test('welding/historical analysis still renders its four original fields', async () => {
  const html = await renderComponent('components/ReportAnalysis', { analysis: { ...analysis, brief: null, overview: '焊接概况', features: '工艺结构', alerts: '数据待核对', suggestions: '核对焊接记录' }, loading: false, ready: true })
  for (const text of ['焊接概况', '工艺结构', '数据待核对', '核对焊接记录']) assert.equal(html.split(text).length - 1, 1)
  assert.ok(html.includes('关键发现') && html.includes('建议跟进'))
})

for (const [mode, status] of [['unavailable', 'unavailable'], ['model', 'error']]) {
  test(`unavailable analysis does not claim model success (${mode}/${status})`, async () => {
    const html = await renderComponent('components/ReportAnalysis', {
      analysis: { ...analysis, brief: null, mode, status, overview: '模型暂不可用。', features: '', alerts: '', suggestions: '', cached: false }, loading: false, ready: true,
    })
    assert.ok(html.includes('智能分析暂不可用'))
    assert.match(html, /class="[^"]*\bunavailable\b[^"]*"/)
    assert.doesNotMatch(html, /class="[^"]*\bmodel\b[^"]*"|AI 生成|生产运行分析已完成|核心指标|分析结果/)
  })
}
