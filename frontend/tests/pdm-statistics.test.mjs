import assert from 'node:assert/strict'
import test from 'node:test'
import { createPinia, defineStore, setActivePinia } from 'pinia'
import { effectScope, nextTick, ref } from 'vue'
import { init, use } from 'echarts/core'
import { BarChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { SVGRenderer } from 'echarts/renderers'
import { renderComponent } from './vue-component-helpers.mjs'
import { clonePdmFilters, createPdmFilters, pdmCurrentMonth, pdmPreferences, validatePdmFilters } from '../src/utils/pdmFilters.ts'
import { getPdmOverview, pdmQuery } from '../src/api/pdmStatistics.ts'
import { pdmCompletionChartOption, pdmDetailCharts, pdmReportRegion, pdmReportSections, unassignedPdmGroups } from '../src/utils/pdmReport.ts'
import { usePdmFilterOptions } from '../src/composables/usePdmFilterOptions.ts'

const totals = { total: 4, completed: 2, on_time: 1, total_rate: 50, on_time_rate: 25 }
function table(grouped = false) {
  return { key: grouped ? 'equipment:all:profession' : 'equipment:summary', kind: 'equipment', title: '设备资料计划完成汇总表', scope_label: grouped ? '所有项目－分专业' : '所有项目', group_label: grouped ? '专业' : null,
    rows: grouped ? [
      { category: '技术协议-按设备计', group_id: 'f1', group_name: '船体', subtotal: false, ...totals },
      { category: '技术协议-按设备计', group_id: '', group_name: '总计', subtotal: true, ...totals },
    ] : [{ category: '技术协议-按设备计', group_id: '', group_name: '', subtotal: false, ...totals }], totals }
}

test('both global cards display on-time, total and unfinished rates from document counts', async () => {
  setActivePinia(createPinia())
  const html = await renderComponent('components/pdm/PdmCompletionCards', { loading: false, overview: {
    equipment: { total: 8, completed: 5, on_time: 3, total_rate: 62.5, on_time_rate: 37.5 },
    drawing: { total: 4, completed: 3, on_time: 2, total_rate: 75, on_time_rate: 50 },
  } })
  for (const label of ['按时完成率', '总完成率', '未完成率']) assert.equal(html.split(`>${label}</dt>`).length - 1, 2)
  const values = [...html.matchAll(/<dd\b[^>]*>([\s\S]*?)<\/dd>/g)].map(match => match[1].replace(/<[^>]*>/g, '').trim())
  assert.deepEqual(values, ['37.50%', '62.50%', '37.50%', '50.00%', '75.00%', '25.00%'])
  assert.ok(html.includes('设备资料完成率'))
  assert.ok(html.includes('详设图纸完成率'))
  assert.ok(html.includes('全部数据 · 总数 8 · 已完成 5 · 未完成 3'))
})

test('empty and unavailable global cards keep all three rates unknown instead of zero', async () => {
  setActivePinia(createPinia())
  const empty = { total: 0, completed: 0, on_time: 0, total_rate: null, on_time_rate: null }
  for (const overview of [null, { equipment: empty, drawing: empty }]) {
    const html = await renderComponent('components/pdm/PdmCompletionCards', { loading: false, overview })
    assert.equal((html.match(/—/g) || []).length, 6)
    assert.doesNotMatch(html, /NaN|Infinity/)
  }
})

test('loading global cards show a loading state for each of the three rates', async () => {
  setActivePinia(createPinia())
  const html = await renderComponent('components/pdm/PdmCompletionCards', { loading: true, overview: null })
  for (const kind of ['设备资料', '详设图纸']) {
    for (const label of ['按时完成率', '总完成率', '未完成率']) assert.ok(html.includes(`${kind}${label}正在加载`))
  }
  assert.doesNotMatch(html, /NaN|Infinity|0\.00/)
})

test('global category cards expose keyboard-operable selection while retaining both sets of metrics', async () => {
  setActivePinia(createPinia())
  for (const activeKind of ['equipment', 'drawing']) {
    const html = await renderComponent('components/pdm/PdmCompletionCards', { loading: false, overview: { equipment: totals, drawing: totals }, activeKind })
    assert.equal((html.match(/role="button"/g) || []).length, 2)
    assert.equal((html.match(/tabindex="0"/g) || []).length, 2)
    assert.equal((html.match(/aria-pressed="true"/g) || []).length, 1)
    assert.equal((html.match(/aria-pressed="false"/g) || []).length, 1)
    assert.ok(html.includes('设备资料完成率')); assert.ok(html.includes('详设图纸完成率'))
  }
})

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

test('all equipment and drawing templates retain every category and the document header and total structure', async () => {
  const categories = {
    equipment: ['技术协议-按设备计', '技术协议-按协议计', '设备认可图', '设备工作资料', '设备完工资料'],
    drawing: ['预审图', '送审图', '船东退审图', '船检退审图', 'O版图', '详设下发图', '完工图'],
  }
  for (const [kind, labels] of Object.entries(categories)) {
    for (const group of [null, '专业', '项目']) {
      const names = group === '专业' ? ['总体', '外舾', '内装', '船体', '轮机', '电气', '船管', '工法'] : ['项目一', '项目二']
      const zero = { total: 0, completed: 0, on_time: 0, total_rate: null, on_time_rate: null }
      const value = { ...table(), kind, title: `${kind === 'equipment' ? '设备资料' : '详设图纸'}计划完成汇总表`, group_label: group, totals: zero,
        rows: labels.flatMap(category => group
          ? [...names.map(name => ({ category, group_id: name, group_name: name, subtotal: false, ...zero })), { category, group_id: '', group_name: '总计', subtotal: true, ...zero }]
          : [{ category, group_id: '', group_name: '', subtotal: false, ...zero }]),
      }
      const html = await renderComponent('components/pdm/PdmSummaryTable', { table: value, startDate: '2026-11-01', endDate: '2026-11-30', queried: true })
      assert.equal([...html.matchAll(/scope="rowgroup"/g)].length, labels.length)
      assert.equal([...html.matchAll(new RegExp(`rowspan="${group ? names.length + 1 : 1}" scope="rowgroup"`, 'g'))].length, labels.length)
      assert.equal(/<tfoot\b/.test(html), !group)
      assert.equal([...html.matchAll(/>总计</g)].length, group ? labels.length : 1)
      assert.match(html, /colspan="2" scope="colgroup"[^>]*>完成率/)
      let previous = -1
      for (const category of labels) { const next = html.indexOf(`>${category}</th>`); assert.ok(next > previous); previous = next }
    }
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

test('documents outside the fixed profession rows remain explained outside the template', async () => {
  const value = table(true)
  value.totals = { ...totals, total: 7 }
  const html = await renderComponent('components/pdm/PdmSummaryTable', { table: value, startDate: '2026-11-01', endDate: '2026-11-30', queried: true })
  assert.match(html, /<\/table>\s*<p[^>]*role="note"[^>]*>有 3 份文档/)
  assert.ok(html.includes('已计入总计，未分配到专业行'))
  value.totals = totals
  const assigned = await renderComponent('components/pdm/PdmSummaryTable', { table: value, startDate: '2026-11-01', endDate: '2026-11-30', queried: true })
  assert.doesNotMatch(assigned, /role="note"/)
})

test('missing project records are explained without adding rows to the project template', async () => {
  const value = table(true)
  value.group_label = '项目'; value.rows[0].group_name = '项目一'
  value.totals = { ...totals, total: 6 }
  const html = await renderComponent('components/pdm/PdmSummaryTable', { table: value, startDate: '2026-11-01', endDate: '2026-11-30', queried: true })
  assert.match(html, /<\/table>\s*<p[^>]*role="note"[^>]*>有 2 份文档的项目信息缺失/)
  assert.ok(html.includes('未分配到项目行'))
  assert.doesNotMatch(html, /未填写/)
})

test('omitted group tables stay accounted for without double counting summary expansions', () => {
  const value = table(true)
  const report = { group_by: 'summary', summaries: { equipment: { ...totals, total: 9 } }, tables: [value, value, value] }
  assert.deepEqual(unassignedPdmGroups(report), [])
  for (const mode of ['project', 'profession']) {
    report.group_by = mode; report.tables = [value]
    assert.deepEqual(unassignedPdmGroups(report), [{ value: 'equipment', label: '设备资料', total: 5 }])
    report.tables = []
    assert.equal(unassignedPdmGroups(report)[0].total, 9)
  }
  report.tables = [{ ...value, totals: { ...totals, total: 9 } }]
  assert.deepEqual(unassignedPdmGroups(report), [])
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
  assert.equal(validatePdmFilters(filters), '')
  filters.startDate = ''; filters.endDate = ''
  assert.ok(validatePdmFilters(filters))
  Object.assign(filters, { startDate: '2020-01-01', endDate: '2026-11-10' })
  assert.equal(validatePdmFilters(filters), '')
  filters.startDate = '2026-02-30'; assert.ok(validatePdmFilters(filters))
  filters.startDate = '2026-11-11'; assert.ok(validatePdmFilters(filters))
})

test('table filters default to the complete current month in Shanghai, including leap years and year changes', () => {
  for (const [now, startDate, endDate] of [
    ['2026-11-16T00:00:00Z', '2026-11-01', '2026-11-30'],
    ['2024-02-15T00:00:00Z', '2024-02-01', '2024-02-29'],
    ['2026-12-31T16:05:00Z', '2027-01-01', '2027-01-31'],
  ]) assert.deepEqual(pdmCurrentMonth(new Date(now)), { startDate, endDate })
})

test('each table has an independent filter button and chart without an inline common filter form', async () => {
  setActivePinia(createPinia())
  defineStore('theme-store', { state: () => ({ darkMode: false }) })()
  const filters = createPdmFilters(); filters.documentKinds = ['equipment']
  const report = { group_by: 'summary', summaries: { equipment: totals }, tables: [table()] }
  const region = pdmReportRegion(pdmReportSections[0], filters, report)
  const html = await renderComponent('components/pdm/PdmReportPanel', { region })
  assert.ok(html.includes('筛选设备资料 · 所有项目'))
  assert.ok(html.includes('按时完成率与总完成率柱状图'))
  assert.ok(html.includes('<table'))
  assert.doesNotMatch(html, /<form\b/)
})

test('both partition templates have their own visible query entry before any results exist', async () => {
  for (const section of pdmReportSections.filter(section => section.groupBy !== 'summary')) {
    const region = pdmReportRegion(section, createPdmFilters())
    const html = await renderComponent('components/pdm/PdmReportPanel', { region })
    assert.ok(html.includes(section.title))
    assert.ok(html.includes(`筛选${section.title}`))
    assert.ok(html.includes('请选择条件后查询本表'))
    assert.doesNotMatch(html, /<form\b|<table\b/)
    region.queried = true
    const empty = await renderComponent('components/pdm/PdmReportPanel', { region })
    assert.ok(empty.includes('当前筛选范围没有可展示的分表'))
    assert.ok(empty.includes(`筛选${section.title}`))
  }
})

test('popup option loading stays independent, cancels closed requests and ignores late responses', async () => {
  const original = globalThis.fetch
  const requests = []
  globalThis.fetch = (url, options) => {
    if (String(url).includes('/cancel')) return Promise.resolve(new Response('{}'))
    return new Promise(resolve => requests.push({ options, resolve }))
  }
  const scope = effectScope()
  const a = ref(createPdmFilters()), b = ref(createPdmFilters())
  const showA = ref(false), showB = ref(false)
  let first, second
  scope.run(() => { first = usePdmFilterOptions(a, showA, 'equipment'); second = usePdmFilterOptions(b, showB, 'drawing') })
  const flush = async () => { await nextTick(); await new Promise(resolve => setImmediate(resolve)) }
  const complete = (request, people) => request.resolve(new Response(JSON.stringify({ success: true, data: {
    projects: [], professions: [], responsible_people: people.map(value => ({ value, label: value })),
  } }), { headers: { 'Content-Type': 'application/json' } }))
  try {
    assert.equal(requests.length, 0)
    showA.value = true; await flush()
    assert.equal(requests.length, 1)
    complete(requests[0], ['甲', '乙']); await flush()
    assert.deepEqual(a.value.equipment.responsiblePeople, ['甲', '乙'])
    assert.equal(first.status.value, 'ready')
    assert.equal(b.value.drawing.responsiblePeople, null)
    showB.value = true; await flush()
    const pending = requests[1]
    showB.value = false; await flush()
    assert.equal(pending.options.signal.aborted, true)
    complete(pending, ['过期人员']); await flush()
    assert.equal(b.value.drawing.responsiblePeople, null)
    showB.value = true; await flush()
    complete(requests[2], ['丙']); await flush()
    assert.deepEqual(b.value.drawing.responsiblePeople, ['丙'])
    assert.deepEqual(a.value.equipment.responsiblePeople, ['甲', '乙'])
    assert.equal(second.status.value, 'ready')
    showB.value = false; await flush()
    showB.value = true; await flush()
    requests[3].resolve(new Response(JSON.stringify({ detail: '模拟选项查询失败' }), { status: 502, headers: { 'Content-Type': 'application/json' } }))
    await flush()
    assert.equal(second.status.value, 'error')
    assert.match(second.error.value, /模拟选项查询失败/)
    assert.deepEqual(b.value.drawing.responsiblePeople, ['丙'])
    second.retry(); await flush()
    complete(requests[4], ['丙', '丁']); await flush()
    assert.equal(second.status.value, 'ready')
    assert.deepEqual(b.value.drawing.responsiblePeople, ['丙', '丁'])
  } finally { scope.stop(); globalThis.fetch = original }
})

test('popup retains valid selected professions and clears only unavailable ones after project changes', async () => {
  const original = globalThis.fetch
  globalThis.fetch = async (url, options) => {
    if (String(url).includes('/cancel')) return new Response('{}')
    const filters = JSON.parse(options.body).filters
    return new Response(JSON.stringify({ success: true, data: {
      projects: ['p1', 'p2', 'p3'].map(value => ({ value, label: value })),
      professions: (filters.projects.includes('p3') ? [] : ['f1', 'f2']).map(value => ({ value, label: value })),
      responsible_people: [],
    } }), { headers: { 'Content-Type': 'application/json' } })
  }
  const draft = ref(createPdmFilters()), visible = ref(true), scope = effectScope()
  draft.value.equipment.professions = ['f2']
  const flush = async () => { await nextTick(); await new Promise(resolve => setImmediate(resolve)) }
  scope.run(() => usePdmFilterOptions(draft, visible, 'equipment'))
  try {
    await flush()
    draft.value.equipment.projects = ['p1']; await flush()
    assert.deepEqual(draft.value.equipment.professions, ['f2'])
    draft.value.equipment.projects = ['p3']; await flush()
    assert.deepEqual(draft.value.equipment.projects, ['p3'])
    assert.deepEqual(draft.value.equipment.professions, [])
  } finally { scope.stop(); globalThis.fetch = original }
})

test('per-table popup keeps its document kind fixed and retains grouping and all relevant filters', async () => {
  const html = await renderComponent('components/pdm/PdmFilterBar', { kind: 'equipment', modelValue: createPdmFilters(), status: 'ready' })
  assert.ok(html.includes('统计方式'))
  assert.ok(html.includes('时间段'))
  assert.ok(html.includes('设备等级'))
  assert.doesNotMatch(html, /文档类型|详设类型|图纸批次/)
})

test('grouping mode explains each selector role, puts the partition first and preserves selections', async () => {
  for (const kind of ['equipment', 'drawing']) {
    const filters = createPdmFilters()
    filters[kind].projects = ['p1']; filters[kind].professions = ['f1']
    const options = { [kind]: { projects: [{ value: 'p1', label: '项目一' }], professions: [{ value: 'f1', label: '船体' }] } }
    for (const mode of ['summary', 'project', 'profession', 'summary']) {
      filters.groupBy = mode
      const html = await renderComponent('components/pdm/PdmFilterBar', { kind, modelValue: filters, options, status: 'ready' })
      if (mode === 'summary') assert.doesNotMatch(html, /每项一张表|表内明细|点击查询后更新/)
      else {
        const outer = mode === 'project' ? '项目' : '专业', inner = mode === 'project' ? '专业' : '项目'
        assert.ok(html.includes(`${outer}（每项一张表）`))
        assert.ok(html.includes(`${inner}（表内明细）`))
        assert.ok(html.includes(`每个所选${outer}生成一张表，表内按${inner}展开`))
        const kindLabel = kind === 'equipment' ? '设备资料' : '详设图纸'
        assert.ok(html.indexOf(`aria-label="${kindLabel}${outer}"`) < html.indexOf(`aria-label="${kindLabel}${inner}"`))
      }
      assert.ok(html.includes('项目一')); assert.ok(html.includes('船体'))
      assert.deepEqual(filters[kind].projects, ['p1'])
      assert.deepEqual(filters[kind].professions, ['f1'])
    }
  }
})

test('each fixed template keeps its fields and hides the control that switches table types', async () => {
  for (const section of pdmReportSections) {
    const filters = createPdmFilters(); filters.groupBy = section.groupBy
    const html = await renderComponent('components/pdm/PdmFilterBar', { kind: section.kind, fixedGroupBy: section.groupBy, modelValue: filters, status: 'ready' })
    assert.doesNotMatch(html, /统计方式|按项目统计|按专业统计|文档类型/)
    for (const field of ['时间段', '项目', '专业', '负责人', section.kind === 'equipment' ? '设备等级' : '图纸批次']) assert.ok(html.includes(field))
  }
})

test('every popup opening selects all owners while cancelling leaves applied conditions unchanged', async () => {
  const original = globalThis.fetch
  globalThis.fetch = async () => new Response(JSON.stringify({ success: true, data: {
    projects: [], professions: [], responsible_people: ['甲', '乙'].map(value => ({ value, label: value })),
  } }), { headers: { 'Content-Type': 'application/json' } })
  const applied = createPdmFilters(); applied.equipment.responsiblePeople = ['甲']
  const draft = ref(clonePdmFilters(applied)), visible = ref(true), scope = effectScope()
  const flush = async () => { await nextTick(); await new Promise(resolve => setImmediate(resolve)) }
  scope.run(() => usePdmFilterOptions(draft, visible, 'equipment'))
  try {
    await flush()
    assert.deepEqual(draft.value.equipment.responsiblePeople, ['甲', '乙'])
    draft.value.equipment.responsiblePeople = ['甲']
    draft.value.equipment.types = ['设备技术协议-按设备统计']; await flush()
    assert.deepEqual(draft.value.equipment.responsiblePeople, ['甲', '乙'])
    visible.value = false; await flush()
    draft.value = clonePdmFilters(applied)
    visible.value = true; await flush()
    assert.deepEqual(draft.value.equipment.responsiblePeople, ['甲', '乙'])
    assert.deepEqual(applied.equipment.types, [])
    assert.deepEqual(applied.equipment.responsiblePeople, ['甲'])
  } finally { scope.stop(); globalThis.fetch = original }
})

test('all six fixed regions retain independent filters and accept only their own template results', () => {
  const simple = table(), professional = table(true)
  const project = { ...professional, key: 'equipment:all:project', group_label: '项目' }
  const drawing = { ...simple, key: 'drawing:summary', kind: 'drawing' }
  const report = { group_by: 'summary', summaries: { equipment: totals, drawing: totals }, tables: [simple, drawing, professional, project] }
  const filters = createPdmFilters()
  const regions = pdmReportSections.map(section => pdmReportRegion(section, filters, report))
  assert.equal(regions.filter(region => region.section.kind === 'equipment').length, 5)
  assert.equal(regions.filter(region => region.section.kind === 'drawing').length, 1)
  assert.deepEqual(regions.map(region => region.panels.map(panel => panel.table.key)), [
    ['equipment:summary'], ['equipment:all:profession'], ['equipment:all:project'], [], [], ['drawing:summary'],
  ])
  regions[0].filters.equipment.projects.push('edited')
  assert.deepEqual(regions[1].filters.equipment.projects, [])
  assert.deepEqual(filters.equipment.projects, [])
  filters.equipment.projects = ['p2']; filters.startDate = '2026-10-01'; filters.endDate = '2026-10-31'; filters.dateBasis = 'actual'
  const changed = pdmReportRegion(regions[1].section, filters, report)
  assert.equal(changed.panels.length, 1)
  assert.equal(changed.panels[0].table.key, professional.key)
  assert.deepEqual(changed.filters.documentKinds, ['equipment'])
  assert.deepEqual(changed.filters.equipment.projects, ['p2'])
  assert.equal(changed.filters.dateBasis, 'actual')
  assert.equal(regions[2].filters.dateBasis, 'plan')
  assert.equal(regions[2].filters.startDate, createPdmFilters().startDate)
  assert.equal(regions[5].filters.startDate, createPdmFilters().startDate)
})

test('project and profession tables receive their own narrowed scopes after splitting', () => {
  const filters = createPdmFilters(); filters.documentKinds = ['equipment']
  const first = { ...table(true), key: 'equipment:project:p1', scope_label: '项目一' }
  const second = { ...first, key: 'equipment:project:p2', scope_label: '项目二' }
  filters.groupBy = 'project'
  const report = { group_by: 'project', summaries: { equipment: { ...totals, total: 8 } }, tables: [first, second] }
  const projectSection = pdmReportSections.find(section => section.key === 'equipment:project')
  const professionSection = pdmReportSections.find(section => section.key === 'equipment:profession')
  const projectRegion = pdmReportRegion(projectSection, filters, report)
  const panels = projectRegion.panels
  assert.deepEqual(projectRegion.filters.equipment.projects, [])
  assert.deepEqual(panels.map(panel => panel.filters.equipment.projects), [['p1'], ['p2']])
  filters.groupBy = 'profession'; report.group_by = 'profession'
  report.tables = [{ ...first, key: 'equipment:profession:船体' }]
  const options = { equipment: { professions: [{ value: 'f1', label: '船体' }, { value: 'f2', label: '船体' }, { value: 'f3', label: '轮机' }] } }
  assert.deepEqual(pdmReportRegion(professionSection, filters, report, options).panels[0].filters.equipment.professions, ['f1', 'f2'])
  filters.equipment.professions = ['f1']
  assert.deepEqual(pdmReportRegion(professionSection, filters, report, options).panels[0].filters.equipment.professions, ['f1'])
  report.tables = []
  const empty = pdmReportRegion(professionSection, filters, report)
  assert.deepEqual(empty.panels, [])
  assert.equal(empty.queried, true)
  assert.equal(empty.omittedGroups, 8)
})

test('type charts use category subtotal rates, omit the grand total and keep missing rates distinct from zero', () => {
  const value = table(true)
  value.rows = [
    { ...value.rows[0], total: 1, completed: 1, on_time: 1, total_rate: 100, on_time_rate: 100 },
    { ...value.rows[0], group_id: 'f2', total: 9, completed: 0, on_time: 0, total_rate: 0, on_time_rate: 0 },
    { ...value.rows[1], total: 10, completed: 1, on_time: 1, total_rate: 10, on_time_rate: 10 },
    { ...value.rows[1], category: '设备完工资料', total: 0, completed: 0, on_time: 0, total_rate: null, on_time_rate: null },
  ]
  const option = pdmCompletionChartOption(value, { onTime: '#008800', total: '#0066cc', text: '#222', border: '#ddd', divider: '#eee' })
  assert.deepEqual(option.xAxis.data, ['技术协议-按设备计', '设备完工资料'])
  assert.equal(option.yAxis.min, 0); assert.equal(option.yAxis.max, 100)
  assert.deepEqual(option.series.map(series => series.name), ['按时完成率', '总完成率'])
  assert.deepEqual(option.series.map(series => series.data), [[10, null], [10, null]])
  assert.equal(option.xAxis.axisLabel.formatter('技术协议-按设备计'), '技术协议\n按设备计')
  assert.equal(option.xAxis.axisLabel.formatter('船东退审图'), '船东退\n审图')
  assert.deepEqual(pdmCompletionChartOption(table(), { onTime: '', total: '', text: '', border: '', divider: '' }).xAxis.data, ['技术协议-按设备计'])
})

test('detail charts use each category profession or project rates without subtotal bars', async () => {
  setActivePinia(createPinia())
  defineStore('theme-store', { state: () => ({ darkMode: false }) })()
  for (const group of ['专业', '项目']) {
    const value = table(true); value.group_label = group
    value.rows = [
      { ...value.rows[0], group_name: group === '专业' ? '总体' : '项目一', on_time_rate: 0, total_rate: 25 },
      { ...value.rows[0], group_id: 'f2', group_name: group === '专业' ? '船体' : '项目二', on_time_rate: null, total_rate: null },
      value.rows[1],
      { ...value.rows[0], category: '设备工作资料', group_name: '空数据', total: 0, on_time_rate: null, total_rate: null },
      { ...value.rows[1], category: '设备工作资料', total: 0, on_time_rate: null, total_rate: null },
    ]
    const charts = pdmDetailCharts(value)
    assert.deepEqual(charts.map(chart => chart.title), ['技术协议-按设备计', '设备工作资料'])
    const option = pdmCompletionChartOption(charts[0], { onTime: '', total: '', text: '', border: '', divider: '' }, true)
    assert.equal(option.xAxis.name, group)
    assert.deepEqual(option.xAxis.data, value.rows.slice(0, 2).map(row => row.group_name))
    assert.deepEqual(option.series.map(series => series.data), [[0, null], [25, null]])
    assert.equal(option.xAxis.axisLabel.formatter('很长的模拟项目名称'), '很长的模拟项…')
    const html = await renderComponent('components/pdm/PdmChartTable', { panel: { table: value, filters: createPdmFilters() }, loading: false })
    assert.equal((html.match(/class="pdm-detail-chart"/g) || []).length, 2)
    assert.equal((html.match(/<table\b/g) || []).length, 1)
    assert.ok(html.includes('当前条件下暂无可统计文档'))
    assert.match(html, /rowspan="3" scope="rowgroup"/)
  }
})

test('type and detail chart axis titles and labels remain inside the SVG viewport', () => {
  use([BarChart, GridComponent, LegendComponent, TooltipComponent, SVGRenderer])
  const categories = [
    ['技术协议-按设备计', '技术协议-按协议计', '设备认可图', '设备工作资料', '设备完工资料'],
    ['预审图', '送审图', '船东退审图', '船检退审图', 'O版图', '详设下发图', '完工图'],
  ]
  const cases = categories.map(labels => ({ labels, detail: false, group: null, height: 300 }))
  cases.push({ labels: ['总体', '外舾', '内装', '船体', '轮机', '电气', '船管', '工法'], detail: true, group: '专业', height: 280 },
    { labels: ['一个很长的模拟项目一', '另一个很长的模拟项目二'], detail: true, group: '项目', height: 280 })
  for (const { labels, detail, group, height } of cases) for (const width of [360, 580]) {
    const value = table()
    value.group_label = group
    value.rows = labels.map(category => ({ ...value.rows[0], category, group_name: category }))
    const chart = init(null, undefined, { renderer: 'svg', ssr: true, width, height })
    try {
      chart.setOption({ ...pdmCompletionChartOption(value, { onTime: '#008800', total: '#0066cc', text: '#222', border: '#ddd', divider: '#eee' }, detail), animation: false })
      const textNodes = chart.getZr().storage.getDisplayList(true).filter(node => node.type === 'tspan')
      assert.ok(textNodes.some(node => node.style.text === (group || '类型')))
      for (const node of textNodes) {
        const bounds = node.getBoundingRect().clone()
        if (node.transform) bounds.applyTransform(node.transform)
        assert.ok(bounds.x >= -1 && bounds.y >= -1 && bounds.x + bounds.width <= width + 1 && bounds.y + bounds.height <= height + 1,
          `${node.style.text} is clipped at ${width}px: ${JSON.stringify(bounds)}`)
      }
    } finally { chart.dispose() }
  }
})

test('query contract keeps both kinds, grouping and explicit empty owner selection', () => {
  const filters = createPdmFilters(); filters.equipment.responsiblePeople = []; filters.groupBy = 'profession'
  const payload = pdmQuery(filters)
  assert.deepEqual(payload.document_kinds, ['equipment', 'drawing'])
  assert.deepEqual(payload.equipment.responsible_people, [])
  assert.equal(payload.drawing.responsible_people, null)
  assert.equal(payload.group_by, 'profession')
  assert.equal(payload.date_basis, 'plan')
  filters.dateBasis = 'actual'
  assert.equal(pdmQuery(filters).date_basis, 'actual')
})

test('global cards load both kinds through a separate request without report filters or dates', async () => {
  const original = globalThis.fetch
  const calls = []
  const overview = { equipment: totals, drawing: { ...totals, total_rate: 75 } }
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options })
    return new Response(JSON.stringify({ success: true, data: overview }), { headers: { 'Content-Type': 'application/json' } })
  }
  try {
    assert.deepEqual(await getPdmOverview(new AbortController().signal), overview)
    assert.equal(calls.length, 1)
    assert.equal(calls[0].url, '/api/v1/pdm-statistics/overview')
    assert.equal(calls[0].options.body, undefined)
  } finally { globalThis.fetch = original }
})

test('filter panels display both sets of required fields and source failures', async () => {
  const html = await renderComponent('components/pdm/PdmFilterBar', { modelValue: createPdmFilters(), status: 'error', error: 'PDM连接失败' })
  for (const label of ['设备资料', '详设图纸', '资料类型', '详设类型', '设备等级', '设备区域', '是否厂商表', '图纸批次', '图纸区域', '图纸类别', '权重', '负责人', '统计所有数据', '按项目统计', '按专业统计', '计划日期', '实际日期', 'PDM连接失败']) assert.ok(html.includes(label), label)
})
