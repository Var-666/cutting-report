// @vitest-environment jsdom

import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { NDatePicker, NSelect } from 'naive-ui'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { Fragment, defineComponent, h, reactive, ref, watch } from 'vue'
import AppDatePicker from '../src/components/AppDatePicker.vue'
import AppSelect from '../src/components/AppSelect.vue'
import StandardReportQuery from '../src/components/StandardReportQuery.vue'
import { selectOptions } from '../src/utils/selectOptions'
import { getTabIdByRoute } from '../src/utils/themeTabs'

enableAutoUnmount(afterEach)
beforeEach(() => Object.defineProperty(Element.prototype, 'scrollTo', { configurable: true, value: vi.fn() }))
afterEach(() => { Reflect.deleteProperty(Element.prototype, 'scrollTo') })

test('select options preserve fragments, groups, disabled options and original value types', () => {
  expect(selectOptions([
    h('option', { value: '', disabled: '' }, '请选择'),
    h(Fragment, null, [h('option', { value: 2 }, ['设备', '二'])]),
    h('optgroup', { label: '其他' }, [h('option', { value: false }, '否')]),
  ])).toEqual([
    { value: 0, sourceValue: '', label: '请选择', disabled: true },
    { value: 1, sourceValue: 2, label: '设备二', disabled: false },
    { value: 2, sourceValue: false, label: '其他 · 否', disabled: false },
  ])
})

test('select exposes a named combobox and announces validation, choices and selection', async () => {
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
  const wrapper = mount(AppSelect, {
    attachTo: document.body,
    props: { modelValue: '', required: true },
    attrs: { 'aria-label': '设备', name: 'device' },
    slots: { default: () => [
      h('option', { value: '', disabled: true }, '请选择'),
      h('option', { value: 2 }, '设备二'),
    ] },
  })
  const native = wrapper.get<HTMLSelectElement>('select[name="device"]')
  const combo = wrapper.get<HTMLElement>('[role="combobox"]')
  expect(native.element.checkValidity()).toBe(false)
  await flushPromises()
  expect(combo.attributes('aria-invalid')).toBe('true')
  expect(combo.attributes('aria-label')).toBe('设备')
  expect(document.activeElement).toBe(combo.element)
  await combo.trigger('click')
  await flushPromises()
  expect(combo.attributes('aria-expanded')).toBe('true')
  const list = document.getElementById(combo.attributes('aria-controls') ?? '')
  expect(list?.getAttribute('role')).toBe('listbox')
  const select = wrapper.getComponent(NSelect)
  expect(select.props('options')).toMatchObject([{ label: '请选择', disabled: true }, { label: '设备二', disabled: false }])
  select.vm.$emit('update:value', 1)
  await flushPromises()
  expect(wrapper.emitted('update:modelValue')).toEqual([[2]])
  expect(wrapper.emitted('change')).toEqual([[{ target: { value: 2 }, value: 2 }]])
  await wrapper.setProps({ modelValue: 2 })
  expect(combo.attributes('aria-invalid')).toBe('false')
  await wrapper.setProps({ disabled: true })
  expect(combo.attributes('aria-expanded')).toBe('false')
  expect(combo.attributes('aria-disabled')).toBe('true')
})

test('date field uses ISO values, month boundaries and clear events', async () => {
  const wrapper = mount(AppDatePicker, {
    props: { required: true, modelValue: '', min: '2026-10-01', max: '2026-10-23' },
    attrs: { name: 'period' },
  })
  const native = wrapper.get<HTMLInputElement>('input[name="period"]')
  expect(native.element.checkValidity()).toBe(false)
  await flushPromises()
  const picker = wrapper.getComponent(NDatePicker)
  expect(picker.props('status')).toBe('error')
  expect(picker.props('isDateDisabled')?.(new Date(2026, 8, 30).getTime(), null, null)).toBe(true)
  expect(picker.props('isDateDisabled')?.(new Date(2026, 9, 23).getTime(), null, null)).toBe(false)
  expect(picker.props('isDateDisabled')?.(new Date(2026, 9, 24).getTime(), null, null)).toBe(true)
  await wrapper.setProps({ mode: 'month', min: '2026-09', max: '2026-10', modelValue: '2026-10', required: false })
  expect(native.attributes('type')).toBe('month')
  expect(picker.props('valueFormat')).toBe('yyyy-MM')
  expect(picker.props('status')).toBeUndefined()
  expect(picker.props('isDateDisabled')?.(new Date(2026, 9, 31).getTime(), null, null)).toBe(false)
  picker.vm.$emit('update:formatted-value', null)
  expect(wrapper.emitted('update:modelValue')).toEqual([['']])
  expect(wrapper.emitted('change')).toEqual([[{ target: { value: '' }, value: '' }]])
})

test('only explicit multi-tab routes use query values as part of tab identity', () => {
  const route = { path: '/admin/home', query: { b: '2', a: '1' }, meta: {} }
  expect(getTabIdByRoute(route)).toBe('/admin/home')
  expect(getTabIdByRoute({ ...route, meta: { multiTab: true } })).toBe('/admin/home?a=1&b=2')
})

test('the report date picker submits one validated range and returns focus on Escape', async () => {
  const dates = reactive({ start: '2026-10-29', end: '2026-10-29' })
  const submissions: Array<typeof dates & { project: string }> = []
  const project = ref('previous-project')
  const Form = defineComponent({
    setup() {
      watch(() => [dates.start, dates.end], () => { project.value = '' })
      return () => h(StandardReportQuery, {
        startDate: dates.start, endDate: dates.end, anchorDate: '2026-10-29', chartType: 'bar_line',
        'onUpdate:startDate': value => { dates.start = value },
        'onUpdate:endDate': value => { dates.end = value },
        onSubmit: () => submissions.push({ ...dates, project: project.value }),
      })
    },
  })
  const wrapper = mount(Form, {
    attachTo: document.body,
    global: { components: { AppDatePicker, AppSelect }, stubs: { teleport: true } },
  })
  const opener = wrapper.get<HTMLButtonElement>('button[aria-haspopup="dialog"]')
  await opener.trigger('click')
  const dialog = wrapper.get('[role="dialog"][aria-label="选择报表统计区间"]')
  expect(opener.attributes('aria-controls')).toBe(dialog.attributes('id'))
  const dateControls = wrapper.findAllComponents(NDatePicker)
  expect(dateControls.map(date => date.props('to'))).toEqual(['body', 'body'])
  dateControls[0].vm.$emit('update:formatted-value', '2026-10-30')
  const apply = dialog.findAll('button').find(button => button.text() === '应用')
  if (!apply) throw new Error('Apply button is missing')
  await apply.trigger('click')
  expect(dialog.text()).toContain('结束日期不能早于开始日期')
  expect(submissions).toEqual([])
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
  await flushPromises()
  expect(opener.attributes('aria-expanded')).toBe('false')
  expect(document.activeElement).toBe(opener.element)
  expect(dates).toEqual({ start: '2026-10-29', end: '2026-10-29' })

  await opener.trigger('click')
  const reopened = wrapper.get('[role="dialog"][aria-label="选择报表统计区间"]')
  const buttons = reopened.findAll('button')
  const lastWeek = buttons.find(button => button.text() === '最近 7 天')
  const submit = buttons.find(button => button.text() === '应用')
  if (!lastWeek || !submit) throw new Error('Date selection controls are missing')
  await lastWeek.trigger('click')
  await submit.trigger('click')
  await flushPromises()
  expect(submissions).toEqual([{ start: '2026-10-23', end: '2026-10-29', project: '' }])
})
