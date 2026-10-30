import { readFileSync } from 'node:fs'
import { scriptFunction } from './typescript-helpers.mjs'

// Run the extracted composables with the same mocked APIs and chart instances as the page fixtures.
export function reportModuleBindings(bindings) {
  return Object.fromEntries(['useEnergyReportData', 'useEnergyReportCharts', 'useLeadershipReportQuery', 'useLeadershipReportData', 'useLeadershipReportCharts'].map(name => {
    const source = readFileSync(new URL(`../src/composables/${name}.ts`, import.meta.url), 'utf8')
      .replace(/^import .*$/gm, '').replace('export function', 'function')
    return [name, scriptFunction(...Object.keys(bindings), `${source}; return ${name}`)(...Object.values(bindings))]
  }))
}
