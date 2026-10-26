import assert from 'node:assert/strict'
import { test } from 'node:test'
import { readFileSync, readdirSync } from 'node:fs'
import { contentFit, contentTable, contentWidthUnits, distributeColumnWidths } from '../src/directives/contentTable.js'

test('short numbers, empty fields and timestamps shrink to their content, not fixed proportions', () => {
  assert.equal(contentFit('序号', ['1', '100']), 'compact')
  assert.equal(contentFit('工程号', ['--', '—']), 'compact')
  assert.equal(contentFit('电耗 kWh', ['31.166', '--']), 'compact')
  assert.equal(contentFit('任务开始', ['2026-09-03 12:45:28']), 'compact')
  assert.equal(contentFit('人', ['张三2026-09-03 12:45']), 'compact')
  assert.equal(contentFit('程序号', ['A_10.0023.slp-PK-406PS23']), 'text')
  assert.equal(contentFit('错误', ['错误原因比较长时应该允许换行而不是裁剪掉']), 'text')
})

test('new rows reclassify a column; summary rows cannot distort sizing', () => {
  const cell = (textContent, colSpan = 1) => ({ textContent, colSpan, dataset: {} })
  const header = cell('工程号')
  const row = { cells: [cell('--')] }
  const summary = { cells: [cell('跨列汇总不应该影响单列'), cell('100', 2)] }
  const classes = new Set()
  const table = { classList: { add: (name) => classes.add(name) }, tHead: { rows: [{ cells: [header] }] }, tBodies: [{ rows: [row, summary] }] }
  contentTable.mounted(table)
  assert.equal(header.dataset.contentFit, 'compact')
  assert.ok(classes.has('content-table'))
  row.cells[0].textContent = 'new-long-project-code-12345'
  contentTable.updated(table)
  assert.equal(header.dataset.contentFit, 'text')
  assert.equal(row.cells[0].dataset.contentFit, 'text')
  assert.deepEqual(summary.cells[0].dataset, {})
})

test('intrinsic widths use every heading and keep program columns naturally wider', () => {
  const columns = [
    ['序号', ['1', '10']],
    ['工程号', ['--']],
    ['船号', ['--']],
    ['分段名', ['--']],
    ['程序号', ['A_10.00004.nrp2-S201C100A004']],
    ['切割长度', ['161.248']],
    ['坡口长度', ['0']],
    ['切割时长', ['00:33:10']],
    ['时间', ['2026-09-08 01:51']],
    ['待确认', ['待确认']],
    ['备注', ['--']],
  ]
  const units = columns.map(([label, values]) => contentWidthUnits(label, values))
  const programIndex = columns.findIndex(([label]) => label === '程序号')
  const programShare = units[programIndex] / units.reduce((sum, value) => sum + value, 0)

  assert.ok(programShare > 0.15)
  assert.ok(programShare < 0.3)
  assert.ok(units[programIndex] > units[columns.findIndex(([label]) => label === '切割长度')])
})

test('tables preserve intrinsic widths and share remaining window width equally', () => {
  const headers = [{ textContent: '日期', dataset: {} }, { textContent: '切割时长', dataset: {} }, { textContent: '电耗 kWh', dataset: {} }]
  const rowCells = [
    { textContent: '2026-09-01', colSpan: 1, dataset: {} },
    { textContent: '00:00:00', colSpan: 1, dataset: {} },
    { textContent: '--', colSpan: 1, dataset: {} },
  ]
  let minimumWidth = ''
  const children = []
  const createElement = (tagName) => ({
    tagName: tagName.toUpperCase(),
    dataset: {},
    children: [],
    style: { values: {}, setProperty(name, value) { this.values[name] = value } },
    appendChild(child) { this.children.push(child); this.lastElementChild = child },
  })
  const table = {
    classList: { add() {} },
    style: { setProperty: (name, value) => { if (name === '--content-table-min-width') minimumWidth = value } },
    parentElement: { clientWidth: 900 },
    ownerDocument: { createElement },
    children,
    insertBefore(node) { children.unshift(node) },
    tHead: { rows: [{ cells: headers }] },
    tBodies: [{ rows: [{ cells: rowCells }] }],
  }

  contentTable.mounted(table)
  assert.ok(headers.every((header) => header.dataset.contentFit === 'compact'))
  assert.ok(rowCells.every((cell) => cell.dataset.contentFit === 'compact'))
  assert.equal(minimumWidth, '900px')
  assert.equal(children[0].tagName, 'COLGROUP')
  assert.equal(children[0].children.length, 3)
  const widths = children[0].children.map((column) => Number.parseFloat(column.style.values.width))
  assert.ok(Math.abs(widths.reduce((sum, width) => sum + width, 0) - 900) < 0.02)
  assert.ok(widths[0] > widths[2])

  const units = [contentWidthUnits('日期', ['2026-09-01']), contentWidthUnits('切割时长', ['00:00:00']), contentWidthUnits('电耗 kWh', ['--'])]
  const tracks = distributeColumnWidths(units, 900)
  const additions = tracks.widths.map((width, index) => width - tracks.intrinsicWidths[index])
  assert.ok(additions.every((addition) => Math.abs(addition - additions[0]) < 0.001))
})

test('cached tables disconnect and restore exactly one ResizeObserver', () => {
  const OriginalResizeObserver = globalThis.ResizeObserver
  const observers = []
  globalThis.ResizeObserver = class {
    constructor(callback) {
      this.callback = callback
      this.connected = false
      observers.push(this)
    }
    observe() { this.connected = true }
    disconnect() { this.connected = false }
  }
  const table = {
    classList: { add() {} },
    style: { setProperty() {} },
    parentElement: { clientWidth: 800 },
    tHead: { rows: [{ cells: [] }] },
    tBodies: [],
    isConnected: true,
  }

  try {
    contentTable.mounted(table, { value: true })
    assert.equal(observers.filter((observer) => observer.connected).length, 1)
    contentTable.updated(table, { value: true, oldValue: true })
    assert.equal(observers.filter((observer) => observer.connected).length, 1)
    contentTable.updated(table, { value: false, oldValue: true })
    assert.equal(observers.filter((observer) => observer.connected).length, 0)
    contentTable.updated(table, { value: true, oldValue: false })
    assert.equal(observers.filter((observer) => observer.connected).length, 1)
    contentTable.beforeUnmount(table)
    assert.equal(observers.filter((observer) => observer.connected).length, 0)
  } finally {
    globalThis.ResizeObserver = OriginalResizeObserver
  }
})

test('every Vue page table opts into shared content sizing', () => {
  let count = 0
  for (const folder of ['views', 'components']) {
    const dir = new URL(`../src/${folder}/`, import.meta.url)
    for (const name of readdirSync(dir).filter((name) => name.endsWith('.vue'))) {
      const source = readFileSync(new URL(name, dir), 'utf8')
      for (const [tag] of source.matchAll(/<table\b[^>]*>/g)) {
        assert.match(tag, /v-content-table/, name)
        count++
      }
    }
  }
  assert.ok(count >= 14)
  const css = readFileSync(new URL('../src/assets/content-tables.css', import.meta.url), 'utf8')
  assert.match(css, /table\.content-table\s*\{[\s\S]*--content-table-min-width[\s\S]*table-layout:fixed !important/)
  assert.match(css, /table\.content-table th,\s*table\.content-table td\s*\{[^}]*text-align:left !important/)
  assert.match(css, /white-space:nowrap !important/)
  assert.doesNotMatch(css, /resize:/)
})

test('header and summary cells paint a solid theme surface below their tint while rows scroll underneath', () => {
  const css = readFileSync(new URL('../src/assets/content-tables.css', import.meta.url), 'utf8')
  const header = css.match(/table\.content-table thead th\s*\{([^}]+)\}/)[1]
  assert.match(header, /background:linear-gradient\(var\(--report-table-head-bg\),var\(--report-table-head-bg\)\),rgb\(var\(--container-bg-color\)\) !important/)
  const footer = css.match(/table\.content-table tfoot th,\s*table\.content-table tfoot td\s*\{([^}]+)\}/)[1]
  assert.match(footer, /color:var\(--text-color-1\) !important/)
  assert.match(footer, /background:linear-gradient\([^;]+\),rgb\(var\(--container-bg-color\)\) !important/)
})

test('table details and action groups stay on the same line', () => {
  const css = readFileSync(new URL('../src/assets/content-tables.css', import.meta.url), 'utf8')
  assert.match(css, /td :is\(strong, small, code, \.confirmed-mark\)\s*\{[^}]*display:inline-block !important;[^}]*margin-block:0 !important;/)
  assert.match(css, /td > :is\(div,p,span\)\s*\{[^}]*display:inline-flex !important;[^}]*flex-flow:row nowrap !important;/)
  assert.match(css, /:is\(\.push-batch-file > span,\.error-cell > span\)\s*\{[^}]*display:inline-block !important;[^}]*white-space:nowrap !important;/)
  assert.match(css, /td :is\(button,a\) \{ white-space:nowrap !important;/)
  const automation = readFileSync(new URL('../src/views/AutomationView.vue', import.meta.url), 'utf8')
  assert.match(automation, /td\.error-cell \{ white-space: nowrap !important; \}/)
})

test('explicit field alignment reaches headings, placeholders and totals with safe hover text', () => {
  const cell = (textContent, align) => ({ textContent, colSpan: 1, dataset: align ? { contentAlign: align } : {} })
  const headers = [cell('船号'), cell('切割时长', 'right'), cell('状态', 'center'), cell('操作', 'center')]
  headers[3].dataset.contentAction = 'true'
  const cells = [cell('00123456'), cell('--'), cell('已完成'), cell('编辑')]
  cells[0].title = '现有的完整船号说明'
  const totals = [cell('汇总'), cell('12345:59:59'), cell('--'), cell('--')]
  const empty = cell('暂无数据'); empty.colSpan = 4
  const table = { dataset: { contentColumns: 'left right center actions' }, classList: { add() {} }, tHead: { rows: [{ cells: headers }] }, tBodies: [{ rows: [{ cells }, { cells: [empty] }] }], tFoot: { rows: [{ cells: totals }] } }
  contentTable.mounted(table)
  for (const row of [headers, cells, totals]) assert.deepEqual(row.map(c => c.dataset.contentAlign), ['left', 'right', 'center', 'center'])
  assert.equal(cells[3].dataset.contentAction, 'true')
  assert.equal(cells[0].title, '现有的完整船号说明')
  assert.equal(cells[1].title, '--')
  cells[1].textContent = '99999:59:59'
  contentTable.updated(table)
  assert.equal(cells[1].title, '99999:59:59')
  assert.equal(totals[1].title, '12345:59:59')
  assert.deepEqual(empty.dataset, {})
})

test('alignment and frozen actions use explicit column definitions', () => {
  const css = readFileSync(new URL('../src/assets/content-tables.css', import.meta.url), 'utf8')
  assert.match(css, /\[data-content-align="right"\]\s*\{[^}]*text-align:right !important;[^}]*font-variant-numeric:tabular-nums/)
  assert.match(css, /\[data-content-action="true"\]\s*\{[^}]*position:sticky;[^}]*right:0;/)
  assert.match(css, /:is\(\.table-empty,\.energy-empty,[^}]*text-align:center !important/)
  for (const name of ['LeadershipReportView', 'WorkshopView', 'EnergyPeriodReportView', 'AlarmCenterView', 'CuttingDispatchView', 'AutomationView', 'ModelUsageView', 'AdminAccountsView', 'ConfigurationDiagnosticsView', 'DesignerView']) {
    const source = readFileSync(new URL(`../src/views/${name}.vue`, import.meta.url), 'utf8')
    for (const [table] of source.matchAll(/<table\b[\s\S]*?<thead>/g)) assert.match(table, /data-content-columns=/, name)
  }
})

test('action tracks reserve the complete button group including padding and margins', () => {
  const cell = textContent => ({ textContent, colSpan: 1, dataset: {}, children: [] })
  const headers = [cell('文件名'), cell('程序号'), cell('操作')]
  const cells = [cell('A'.repeat(40)), cell('B'.repeat(40)), cell('立即执行 编辑 删除')]
  cells[2].children = [{ getBoundingClientRect: () => ({ width: 176 }) }]
  const createElement = tag => ({ tagName: tag.toUpperCase(), dataset: {}, children: [], style: { setProperty() {} }, appendChild(child) { this.children.push(child) } })
  let minimumWidth = 0
  const table = {
    dataset: { contentColumns: 'left left actions' }, classList: { add() {} },
    style: { setProperty: (name, value) => { if (name === '--content-table-min-width') minimumWidth = parseFloat(value) } },
    parentElement: { clientWidth: 600 }, children: [], insertBefore(node) { this.children.push(node) },
    ownerDocument: { createElement, defaultView: { getComputedStyle: () => ({ paddingLeft: '12px', paddingRight: '12px', marginLeft: '4px', marginRight: '4px' }) } },
    tHead: { rows: [{ cells: headers }] }, tBodies: [{ rows: [{ cells }] }],
  }
  contentTable.mounted(table)
  assert.ok(minimumWidth >= 864, 'two long identifiers plus the complete action group must fit without clipping')
})
