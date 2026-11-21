// @vitest-environment jsdom

import assert from 'node:assert/strict'
import { enableAutoUnmount, mount } from '@vue/test-utils'
import { afterEach, expect, test } from 'vitest'
import ReportAnalysis from '../src/components/ReportAnalysis.vue'
import { compactAnalysisFactLabel, compactAnalysisFactValue } from '../src/utils/analysisFactDisplay.ts'

enableAutoUnmount(afterEach)

test('model text uses readable device names and keeps valid evidence while rounding cutting values', async () => {
  const wrapper = mount(ReportAnalysis, {
    props: {
      ready: true, loading: false,
      analysis: { mode: 'model', status: 'success', overview: 'Cutter01电耗 1,234.567 kWh [F001]。Cutter02电耗 12.345 kWh [F899]',
        facts: [{ id: 'F001', label: '实测电耗', value: '1,234.567', unit: 'kWh', source: '电表' }] },
    },
  })
  expect(wrapper.get('h2').text()).toBe('智能分析')
  expect(wrapper.text()).toContain('AI 生成')
  expect(wrapper.text()).toContain('20KW切割机电耗 1,234.6 kWh [F001]')
  expect(wrapper.text()).toContain('30KW切割机电耗 12.3 kWh')
  expect(wrapper.text()).not.toContain('[F899]')
  expect(wrapper.find('sup').exists()).toBe(false)
  const evidence = wrapper.findAll('span').find(item => item.text().startsWith('· 查看模型输入数据'))
  expect(evidence).toBeDefined()
  await evidence.trigger('click')
  expect(wrapper.text()).toContain('F001 · 实测电耗')
  expect(wrapper.text()).toContain('来源：电表')
})

test('welding model text preserves thousandths and source evidence', () => {
  const wrapper = mount(ReportAnalysis, {
    props: {
      ready: true, loading: false, showMetrics: false,
      weldingReport: { data_version: 'weld-v2' },
      analysis: { mode: 'model', status: 'success', overview: '区间焊接长度 12.345 m [F002]', data_version: 'weld-v1',
        facts: [{ id: 'F002', label: '焊接长度', value: '12.345', unit: 'm' }] },
    },
  })
  expect(wrapper.text()).toContain('区间焊接长度 12.345 m [F002]')
  expect(wrapper.text()).not.toMatch(/报表数据分析|降级分析|分析边界/)
})

test('compound fact values stay compact in metric cards while the detail remains available', () => {
  assert.equal(compactAnalysisFactValue('工程号未填写 984 项，任务较多的日期 1 天'), '工程号未填写 984 项')
  assert.equal(compactAnalysisFactValue('5 项待确认，比上一周期下降 5.6%'), '5 项待确认')
  assert.equal(compactAnalysisFactValue('18,708.5 kWh'), '18,708.5 kWh')
  assert.equal(compactAnalysisFactValue('缺失'), '缺失')
  assert.equal(compactAnalysisFactLabel('工程号与任务较多的日期', '工程号未填写 984 项，任务较多的日期 1 天'), '工程号')
  assert.equal(compactAnalysisFactLabel('待确认任务与数量变化', '5 项待确认，比上一周期下降 5.6%'), '待确认任务')
  assert.equal(compactAnalysisFactLabel('已匹配任务电耗', '18,708.5 kWh'), '已匹配任务电耗')
})
