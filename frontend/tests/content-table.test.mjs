import assert from 'node:assert/strict'
import { test } from 'node:test'
import { readFileSync, readdirSync } from 'node:fs'
import { contentFit, contentTable, contentWidthUnits } from '../src/directives/contentTable.js'

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

test('column proportions use every heading and cap long program columns', () => {
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
    ['人', ['待工人确认']],
    ['备注', ['--']],
  ]
  const units = columns.map(([label, values]) => contentWidthUnits(label, values))
  const programIndex = columns.findIndex(([label]) => label === '程序号')
  const programShare = units[programIndex] / units.reduce((sum, value) => sum + value, 0)

  assert.ok(programShare > 0.15)
  assert.ok(programShare < 0.24)
  assert.ok(units[programIndex] > units[columns.findIndex(([label]) => label === '切割长度')])
})

test('tables generate content-weighted percentage columns that follow the window', () => {
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
    ownerDocument: { createElement },
    children,
    insertBefore(node) { children.unshift(node) },
    tHead: { rows: [{ cells: headers }] },
    tBodies: [{ rows: [{ cells: rowCells }] }],
  }

  contentTable.mounted(table)
  assert.ok(headers.every((header) => header.dataset.contentFit === 'compact'))
  assert.ok(rowCells.every((cell) => cell.dataset.contentFit === 'compact'))
  assert.equal(minimumWidth, '560px')
  assert.equal(children[0].tagName, 'COLGROUP')
  assert.equal(children[0].children.length, 3)
  const widths = children[0].children.map((column) => Number.parseFloat(column.style.values.width))
  assert.ok(Math.abs(widths.reduce((sum, width) => sum + width, 0) - 100) < 0.01)
  assert.ok(widths[0] > widths[2])
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
  assert.doesNotMatch(css, /table\.content-table thead th\s*\{[^}]*text-align/)
  assert.doesNotMatch(css, /resize:|grid-template-columns/)
})
