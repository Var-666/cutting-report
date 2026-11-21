import assert from 'node:assert/strict'
import { test } from 'vitest'
import * as Vue from 'vue'
import { renderToString } from '@vue/server-renderer'
import { NConfigProvider, darkTheme, useThemeVars } from 'naive-ui'
import { reportThemeVariables } from '../src/theme/reportVariables.ts'
test('report CSS aliases use actual Naive light/dark surfaces, text, borders and primary contrast', async () => {
  const modes = []
  for (const dark of [false, true]) {
    let vars
    await renderToString(Vue.createSSRApp({ render: () => Vue.h(NConfigProvider, { theme: dark ? darkTheme : null }, { default: () => Vue.h({ setup() { vars = useThemeVars().value; return () => null } }) }) }))
    const aliases = reportThemeVariables(vars)
    assert.equal(aliases['--text-color-1'], vars.textColor1)
    assert.equal(aliases['--theme-table-header-color'], vars.tableHeaderColor)
    assert.equal(aliases['--theme-on-primary-color'], vars.baseColor)
    assert.equal(aliases['--theme-border-radius'], vars.borderRadius)
    assert.ok(Object.values(aliases).every(value => typeof value === 'string' && value.length))
    modes.push(aliases)
  }
  assert.notEqual(modes[0]['--text-color-1'], modes[1]['--text-color-1'])
  assert.notEqual(modes[0]['--theme-input-color'], modes[1]['--theme-input-color'])
  assert.notEqual(modes[0]['--theme-table-header-color'], modes[1]['--theme-table-header-color'])
})

test('energy data module preserves missing-meter values, production-day counts and independent table/chart sorting', async () => {
  const { useEnergyReportData } = await import('../src/composables/useEnergyReportData.ts')
  const records = Array.from({ length: 52 }, (_, index) => ({ task_id: String(index), production_date: index % 2 ? '2026-10-02' : '2026-10-01', production_time: `2026-10-02T${String(23 - index % 24).padStart(2, '0')}:00:00`, electricity_consumption: index % 2 ? null : 0, gas_consumption: 1, work_time: 60 }))
  const taskDay = Vue.ref({ records, energy_connected: false, summary: { task_count: 4, electricity_record_count: 2, gas_record_count: 4 } })
  const monthlyReport = Vue.ref({ rows: [{ report_date: '2026-09' }, { report_date: '2026-10' }], syncing: true, missing_day_count: 2 })
  const data = useEnergyReportData({ taskDay, monthlyReport, summaryPeriodStart: Vue.ref('2025-11-01'), summaryPeriodEnd: Vue.ref('2026-10-30') })
  assert.equal(data.format(null), '暂未接入')
  assert.equal(data.format(0), '0.0')
  assert.equal(data.energyCoverage.value, 75)
  assert.deepEqual(data.deviceMetricSeries.value.tasks, [26, 26])
  assert.equal(data.taskChartRows.value.length, 50)
  assert.equal(data.taskRows.value.length, 52)
  assert.equal(data.taskRows.value[0].task_id, '0', 'chart sorting must not reorder table/source records')
  assert.equal(data.summaryRows.value[0].report_date, '2026-10')
  assert.equal(data.summaryChartRows.value[0].report_date, '2026-09')
  taskDay.value.energy_connected = true
  assert.equal(data.format(null), '--')
  assert.match(data.summarySubtitle.value, /后台补齐 2 天能耗中/)
})
