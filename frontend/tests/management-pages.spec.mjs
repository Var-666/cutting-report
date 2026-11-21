// @vitest-environment jsdom

import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { NDatePicker, NDialogProvider, NMessageProvider, NSelect } from 'naive-ui'
import { createPinia } from 'pinia'
import { afterEach, expect, test, vi } from 'vitest'
import { h } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { resetDeviceContext, setDeviceCatalog } from '../src/api/deviceContext'
import AppSelect from '../src/components/AppSelect.vue'
import DesignerView from '../src/views/DesignerView.vue'
import OperationsOverviewView from '../src/views/OperationsOverviewView.vue'
import OperationsRecordsView from '../src/views/OperationsRecordsView.vue'
import { setupSoybeanComponents } from '../src/vendor/soybean-admin/components'
import { stubTableLayout } from './table-layout-fixture.mjs'

enableAutoUnmount(afterEach)
afterEach(resetDeviceContext)
const ok = data => Response.json({ success: true, data })
const button = (wrapper, label) => wrapper.findAll('button').find(item => item.text() === label)

async function mountPage(component, props = {}, query = {}) {
  stubTableLayout()
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component, props },
      ...['operations-incidents', 'operations-logs', 'database-monitors', 'model-usage', 'configuration-diagnostics', 'automation'].map(name => ({
        name, path: '/' + name, component: { render: () => null },
      })),
    ],
  })
  await router.push({ path: '/', query })
  const page = mount({
    render: () => h(NMessageProvider, {}, { default: () => h(NDialogProvider, {}, { default: () => h(RouterView) }) }),
  }, {
    attachTo: document.body,
    global: {
      plugins: [router, createPinia(), { install: setupSoybeanComponents }],
      components: { AppSelect, NDatePicker },
      stubs: { teleport: true },
    },
  })
  await flushPromises()
  return page
}

test.each([['incidents', undefined], ['incidents', 'WARNING'], ['logs', undefined]])(
  '%s preserves applied severity filters and restores the default on reset (%s)', async (mode, initialLevel) => {
    const requests = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async input => {
      const url = new URL(String(input), 'https://fixture.example')
      expect(url.pathname).toBe('/api/v1/operations/' + mode)
      requests.push(url.searchParams)
      return ok({ items: [], total: 0, page: 1, page_size: 10, coverage_warnings: [], next_cursor: null })
    })
    const page = await mountPage(OperationsRecordsView, { mode }, initialLevel ? { level: initialLevel } : {})
    expect(requests[0].get('level')).toBe(initialLevel ?? null)
    expect(requests[0].get('min_level')).toBe(mode === 'incidents' && !initialLevel ? 'ERROR' : null)

    const showFilters = () => button(page, '筛选').trigger('click')
    const level = () => page.findAllComponents(NSelect).find(select => select.element.closest('label')?.textContent.startsWith('级别'))
    const apply = async (value) => {
      level().vm.$emit('update:value', value)
      await page.get('form').trigger('submit')
      await flushPromises()
    }
    await showFilters()
    await apply(null)
    expect(requests.at(-1).has('min_level')).toBe(false)
    expect(requests.at(-1).has('level')).toBe(false)
    await showFilters()
    expect(level().props('value')).toBeUndefined()
    await button(page, '重置').trigger('click')
    expect(level().props('value')).toBe(mode === 'incidents' ? 'ERROR_PLUS' : undefined)
    if (mode === 'incidents') {
      await apply('ERROR_PLUS')
      expect(requests.at(-1).get('min_level')).toBe('ERROR')
      await showFilters()
      await apply('WARNING')
      expect(requests.at(-1).get('level')).toBe('WARNING')
      expect(requests.at(-1).has('min_level')).toBe(false)
    }
  },
)

test('runtime roles paginate locally and retain or clamp the active page after refresh', async () => {
  let roles = Array.from({ length: 25 }, (_, index) => ({ role: 'worker', instance_id: `consumer-${index}`, state: 'healthy' }))
  const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async input => {
    expect(String(input)).toBe('/api/v1/operations/status')
    return ok({ roles, monitors: [], work: [], postgres: 'healthy', consumers_ready: roles.length, consumers_expected: 25 })
  })
  const page = await mountPage(OperationsOverviewView, { statusOnly: true })
  const instances = () => page.findAll('tbody tr').map(row => row.findAll('td')[1]?.text()).filter(name => name?.startsWith('consumer-'))
  const activePage = () => page.get('[aria-current="page"]').text()
  const refresh = async () => { await button(page, '刷新').trigger('click'); await flushPromises() }
  expect(page.text()).toContain('共 25 条')
  expect(instances()).toEqual(roles.slice(0, 10).map(row => row.instance_id))
  await page.get('button[aria-label="第 3 页"]').trigger('click')
  expect(instances()).toEqual(roles.slice(20).map(row => row.instance_id))
  expect(fetch).toHaveBeenCalledTimes(1)
  await refresh()
  expect(activePage()).toBe('3')
  roles = roles.slice(0, 12)
  await refresh()
  expect(activePage()).toBe('2')
  expect(instances()).toEqual(['consumer-10', 'consumer-11'])
  roles = []
  await refresh()
  expect(activePage()).toBe('1')
  expect(instances()).toEqual([])
  expect(page.text()).toContain('共 0 条')
})

const queryTask = {
  query_task_id: 'query-1', name: '生产汇总', code: 'production', version: 1, enabled: true,
  sql_text: 'SELECT 1', columns: [{ field: 'value', label: '数值', type: 'number', unit: '' }], parameters: {},
}

test('designer validates the edited SQL and previews the stored query with its management token', async () => {
  setDeviceCatalog({ default_device_id: 'Cutter01', items: [{ id: 'Cutter01', type: 'cutting' }] })
  const writes = []
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const path = new URL(String(input), 'https://fixture.example').pathname.split('/designer/')[1]
    if (init?.method !== 'POST') return ok({ items: path === 'query-tasks' ? [queryTask] : [] })
    writes.push({ path, token: new Headers(init.headers).get('X-SQL-Workbench-Token'), body: JSON.parse(init.body) })
    if (path === 'query-tasks/validate') return ok({ formatted_sql: 'SELECT 1 AS value', tables: [] })
    return ok({
      returned: 1, mode: 'demo', columns: [...queryTask.columns, { field: 'name', label: '名称', type: 'text' },
        { field: 'date', label: '日期', type: 'date' }, { field: 'time', label: '时间', type: 'datetime' },
        { field: 'duration', label: '时长', type: 'duration' }],
      items: [{ value: 0, name: '<b>工件</b>', date: '2026-10-28', time: '2026-10-28T08:00:00', duration: 12.5 }],
    })
  })
  const page = await mountPage(DesignerView)
  expect(button(page, '预览数据').attributes('disabled')).toBeDefined()
  await button(page, '预览数据').trigger('click')
  expect(writes).toEqual([])
  await page.get('input[name="designer_admin_token"]').setValue('fixture-admin-token')
  await flushPromises()
  await page.findAll('aside button').find(item => item.text().includes(queryTask.name)).trigger('click')
  const sql = page.get('textarea[name="query_sql"]')
  await sql.setValue('select  1')
  await button(page, '校验 SQL').trigger('click')
  await flushPromises()
  expect(sql.element.value).toBe('SELECT 1 AS value')
  await button(page, '预览数据').trigger('click')
  await flushPromises()
  expect(writes).toEqual([
    { path: 'query-tasks/validate', token: 'fixture-admin-token', body: { sql_text: 'select  1' } },
    { path: 'query-tasks/query-1/preview', token: 'fixture-admin-token',
      body: { report_date: expect.any(String), project_code: null, ship_no: null, parameters: {}, max_rows: 10 } },
  ])
  const cells = page.findAll('tbody td').map(cell => cell.text())
  expect(cells).toEqual(['0', '<b>工件</b>', '2026年10月28日', expect.stringContaining('08:00:00'), expect.stringContaining('12.5')])
  expect(page.find('tbody b').exists()).toBe(false)
})

test('saving a designer definition retains independent query and template drafts', async () => {
  const writes = []
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const path = new URL(String(input), 'https://fixture.example').pathname.split('/designer/')[1]
    if (init?.method !== 'POST') return ok({ items: [] })
    const body = JSON.parse(init.body)
    writes.push({ path, body })
    return ok({ ...body, definition_id: 'definition-1', version: 1 })
  })
  const page = await mountPage(DesignerView)
  const input = name => page.get(`input[name="${name}"]`)
  const tab = label => page.findAll('[role="tab"]').find(item => item.text() === label).trigger('click')
  const blockUnload = () => !window.dispatchEvent(new Event('beforeunload', { cancelable: true }))
  expect(blockUnload()).toBe(false)
  await input('query_column_label_0').setValue('修改字段')
  await button(page.get('[role="tabpanel"]'), '新建').trigger('click')
  await flushPromises()
  await button(page, '继续编辑').trigger('click')
  expect(input('query_column_label_0').element.value).toBe('修改字段')

  await tab('模板设计器')
  await input('template_name').setValue('暂存模板')
  await tab('报表定义')
  await input('definition_name').setValue('报表甲')
  await button(page, '保存定义').trigger('click')
  await flushPromises()
  expect(writes).toEqual([{ path: 'definitions', body: expect.objectContaining({ name: '报表甲' }) }])
  expect(page.getComponent(DesignerView).emitted('definitions-change')).toEqual([[]])
  expect(input('definition_name').element.value).toBe('报表甲')
  expect(blockUnload()).toBe(true)

  await tab('模板设计器')
  expect(input('template_name').element.value).toBe('暂存模板')
  await input('template_name').setValue('')
  await tab('查询任务')
  expect(input('query_column_label_0').element.value).toBe('修改字段')
  await input('query_column_label_0').setValue('日期')
  expect(blockUnload()).toBe(false)
})
