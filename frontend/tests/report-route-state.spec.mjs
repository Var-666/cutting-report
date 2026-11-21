// @vitest-environment jsdom

import assert from 'node:assert/strict'
import { mount } from '@vue/test-utils'
import { expect, test } from 'vitest'
import AppDatePicker from '../src/components/AppDatePicker.vue'
import AppSelect from '../src/components/AppSelect.vue'
import StandardReportQuery from '../src/components/StandardReportQuery.vue'
import {
  mergeOwnedQuery,
  queryChoice,
  queryDate,
  queryDateRange,
  queryPositiveInteger,
  sameRouteQuery,
} from '../src/utils/reportRouteState.ts'

test('shared report query parsing rejects links that cannot restore a valid report state', () => {
  assert.equal(queryDate('2024-02-29'), '2024-02-29')
  assert.equal(queryDate('2023-02-29'), '')
  assert.equal(queryDate(['2026-09-01', '2026-09-02']), '2026-09-01')
  assert.deepEqual(queryDateRange({ start: '2026-09-01', end: '2026-09-30' }), {
    start: '2026-09-01',
    end: '2026-09-30',
  })
  assert.equal(queryDateRange({ start: '2026-09-30', end: '2026-09-01' }), null)
  assert.equal(queryDateRange({ start: '2026-01-01', end: '2026-02-01' }, { maxDays: 31 }), null)
  assert.equal(queryChoice('line', ['bar', 'line'], 'bar'), 'line')
  assert.equal(queryChoice('pie', ['bar', 'line'], 'bar'), 'bar')
  assert.equal(queryPositiveInteger('3'), 3)
  assert.equal(queryPositiveInteger('0'), 1)
})

test('report-owned query updates preserve unrelated deep-link state and omit defaults', () => {
  const query = mergeOwnedQuery(
    { job: 'report-7', page: '4', page_size: '20', source: 'notice' },
    ['page', 'page_size'],
    { page: '', page_size: 50 },
  )

  assert.deepEqual(query, { job: 'report-7', source: 'notice', page_size: '50' })
  assert.equal(sameRouteQuery(query, { source: 'notice', page_size: '50', job: 'report-7' }), true)
  assert.equal(sameRouteQuery(query, { job: 'report-7', source: 'notice' }), false)
})


const range = { startDate: '2026-11-05', endDate: '2026-11-05', chartType: 'bar_line' }

test('report controls announce the applied date while a draft is pending', async t => {
  const wrapper = mount(StandardReportQuery, { props: { ...range, dateDirty: true, hasData: true, appliedRange: '2026年11月4日' },
    global: { components: { AppSelect, AppDatePicker } } })
  t.onTestFinished(() => wrapper.unmount())
  expect(wrapper.get('[role="status"]').text()).toContain('仍显示 2026年11月4日')
  await wrapper.setProps({ hasData: false })
  expect(wrapper.get('[role="status"]').text()).toBe('所选区间尚未成功查询')
})

test('warm report controls remain locked while an initial read shows its loading label', async t => {
  const wrapper = mount(StandardReportQuery, { props: { ...range, loading: true, hasData: true, showLoading: false },
    global: { components: { AppSelect, AppDatePicker } } })
  t.onTestFinished(() => wrapper.unmount())
  expect(wrapper.get('form').attributes('aria-busy')).toBe('true')
  const submit = wrapper.get('button[type="submit"]')
  expect(submit.element.disabled).toBe(true)
  expect(submit.text()).toBe('更新报表')
  await wrapper.setProps({ hasData: false, showLoading: true })
  expect(submit.element.disabled).toBe(true)
  expect(submit.text()).toBe('查询中')
})
