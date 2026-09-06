import assert from 'node:assert/strict'
import { test } from 'node:test'
import { readFileSync, readdirSync } from 'node:fs'
import { contentFit, contentTable } from '../src/directives/contentTable.js'

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

test('every Vue page table opts into native content sizing', () => {
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
  assert.match(css, /table-layout:auto !important/)
  assert.doesNotMatch(css, /table-layout:fixed|resize:|grid-template-columns/)
})
