import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import { NDataTable, NPagination } from 'naive-ui'
import { parse, compileScript } from '@vue/compiler-sfc'
import { scriptFunction } from './typescript-helpers.mjs'

const descriptor = parse(readFileSync(new URL('../src/components/AppReportTable.vue', import.meta.url), 'utf8')).descriptor
const bindings = { NDataTable, NPagination }
const script = compileScript(descriptor, { id: 'shared-table-test', inlineTemplate: true }).content
  .replace(/import\s*\{([^}]+)\}\s*from\s*['"]vue['"]/g, (_, names) => {
    for (const name of names.split(',')) {
      const [original, alias = original] = name.trim().split(/\s+as\s+/)
      bindings[alias] = Vue[original]
    }
    return ''
  })
  .replace(/^import .*$/gm, '')
  .replace('export default', 'return')
export function createSharedDataTable(overrides = {}) {
  const values = { ...bindings, ...overrides }
  return scriptFunction(...Object.keys(values), script)(...Object.values(values))
}
export const SharedDataTable = createSharedDataTable()
