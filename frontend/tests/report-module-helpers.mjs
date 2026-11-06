import { readFileSync } from 'node:fs'
import { scriptFunction } from './typescript-helpers.mjs'

// Run the extracted composables with the same mocked APIs and chart instances as the page fixtures.
export function reportModuleBindings(bindings) {
  const modules = {}
  for (const name of ['useEnergyReportData', 'useEnergyReportCharts', 'useLeadershipReportQuery', 'useLeadershipReportData', 'useLeadershipReportCharts', 'useEnergyReportSummary', 'useEnergyReportQuery']) {
    const source = readFileSync(new URL(`../src/composables/${name}.ts`, import.meta.url), 'utf8')
      .replace(/^import .*$/gm, '').replace('export function', 'function')
    const context = { ...bindings, ...modules }
    modules[name] = scriptFunction(...Object.keys(context), `${source}; return ${name}`)(...Object.values(context))
  }
  return modules
}
