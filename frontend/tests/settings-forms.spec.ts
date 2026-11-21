// @vitest-environment jsdom

import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { NMessageProvider, NDialogProvider } from 'naive-ui'
import { createPinia } from 'pinia'
import { afterEach, expect, test, vi } from 'vitest'
import { h } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { resetDeviceContext, setDeviceCatalog } from '../src/api/deviceContext'
import AppSelect from '../src/components/AppSelect.vue'
import AutomationView from '../src/views/AutomationView.vue'
import DatabaseView from '../src/views/DatabaseView.vue'
import { setupSoybeanComponents } from '../src/vendor/soybean-admin/components'

import { stubTableLayout } from './table-layout-fixture.mjs'

enableAutoUnmount(afterEach)
afterEach(resetDeviceContext)

async function mountSettings(component: typeof AutomationView | typeof DatabaseView) {
  stubTableLayout()
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component }, { path: '/away', component: { render: () => '已离开设置' } }],
  })
  await router.push('/')
  const wrapper = mount({
    render: () => h(NMessageProvider, {}, { default: () => h(NDialogProvider, {}, { default: () => h(RouterView) }) }),
  }, {
    attachTo: document.body,
    global: {
      plugins: [router, createPinia(), { install: setupSoybeanComponents }],
      components: { AppSelect },
      stubs: { teleport: true, transition: false, DatabaseSourcesPanel: true, ReportCacheSettings: true },
    },
  })
  await flushPromises()
  return { wrapper, router }
}

test('database connection tests submit the named credentials and announce the reported failure', async () => {
  let connection: object | undefined
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input)
    if (url.endsWith('production-lines')) return Response.json({ success: true, data: { items: [], default_device_id: '' } })
    if (url.endsWith('/status')) return Response.json({ success: true, data: { driver: 'ODBC Driver 18 for SQL Server' } })
    if (url.endsWith('/test-connection') && typeof init?.body === 'string') {
      connection = JSON.parse(init.body)
      return Response.json({ detail: '连接凭据无效' }, { status: 502 })
    }
    throw new Error(`Unexpected request ${url}`)
  })
  const { wrapper } = await mountSettings(DatabaseView)
  await wrapper.get('input[name="test_host"]').setValue('sql.example.com')
  await wrapper.get('input[name="test_database"]').setValue('stats')
  await wrapper.get('input[name="test_username"]').setValue('report-reader')
  const password = wrapper.get('input[name="test_password"]')
  expect(password.attributes('autocomplete')).toBe('current-password')
  await password.setValue('example-password')
  await wrapper.get('form:has(input[name="test_host"])').trigger('submit')
  await flushPromises()
  expect(connection).toMatchObject({ host: 'sql.example.com', database: 'stats', username: 'report-reader', password: 'example-password' })
  expect(wrapper.get('[role="alert"]').text()).toContain('连接凭据无效')
})

test('automation protects the sender draft and submits the email contract', async () => {
  let submitted: object | undefined
  const configurationMethods: string[] = []
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input)
    if (url.includes('/cancel')) return Response.json({})
    if (url.endsWith('/configuration')) {
      expect(init?.credentials).toBe('same-origin')
      configurationMethods.push(init?.method ?? 'GET')
    }
    if (url.endsWith('/configuration') && init?.method === 'PUT' && typeof init.body === 'string') {
      submitted = JSON.parse(init.body)
      return Response.json({ success: true, data: { message: '已保存邮件配置' } })
    }
    if (url.endsWith('/configuration')) return Response.json({ success: true, data: { smtp_from_email: 'before@example.com' } })
    if (url.endsWith('/status')) return Response.json({ success: true, data: { automation_enabled: true, smtp: { enabled: false } } })
    if (url.endsWith('/schedules') || url.includes('/deliveries?')) return Response.json({ success: true, data: { items: [], total: 0, page: 1, page_size: 10 } })
    throw new Error(`Unexpected request ${url}`)
  })
  const { wrapper } = await mountSettings(AutomationView)
  const open = wrapper.findAll('button').find(button => button.text() === '运行与邮件参数')
  if (!open) throw new Error('Configuration button is missing')
  await open.trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('保存后即时生效')
  expect(wrapper.get<HTMLInputElement>('input[name="smtp_password"]').element.value).toBe('')
  const sender = wrapper.get('input[name="smtp_from_email"]')
  expect(sender.attributes('type')).toBe('email')
  expect(sender.attributes('inputmode')).toBe('email')
  await sender.setValue('reports@example.com')
  const pendingUnload = new Event('beforeunload', { cancelable: true })
  expect(window.dispatchEvent(pendingUnload)).toBe(false)
  expect((sender.element as HTMLInputElement).value).toBe('reports@example.com')
  await wrapper.get('form:has(input[name="smtp_from_email"])').trigger('submit')
  await flushPromises()
  expect(configurationMethods).toEqual(['GET', 'PUT'])
  expect(submitted).toMatchObject({ smtp_from_email: 'reports@example.com', smtp_password: null })
  expect(wrapper.text()).toContain('已保存邮件配置')
  expect(window.dispatchEvent(new Event('beforeunload', { cancelable: true }))).toBe(true)
})

const ok = (data: object) => Response.json({ success: true, data })
const catalog = {
  default_device_id: 'Cutter01',
  items: [
    { id: 'Cutter01', name: '切割线', type: 'cutting' as const },
    { id: 'weld-a', name: '焊接线', type: 'welding' as const },
  ],
}
const connection = {
  host: 'saved-host', port: 1433, database: 'production', username: 'reader', password_configured: true,
  driver: 'ODBC Driver 18 for SQL Server', encrypt: true, trust_server_certificate: false,
}
const databaseSettings = {
  data_source_mode: 'demo', worker_database_auth_enabled: false, file_database_enabled: false,
  production: connection, worker: connection, file: connection,
  welding: { enabled: false, host: 'saved-welding', port: 3306, database: 'weld', username: 'reader', password_configured: true, timezone: 'Asia/Shanghai' },
}

test('database edits isolate connection drafts and refresh saved settings without echoing passwords', async () => {
  let settings = structuredClone(databaseSettings)
  let fail = false
  const requests: string[] = []
  const writes = []
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const path = String(input).split('/api/v1/')[1]
    requests.push(path)
    if (path === 'production-lines') return ok(catalog)
    if (path === 'database/status') return ok({ mode: settings.data_source_mode, driver: connection.driver })
    if (init?.method !== 'PUT') return ok(settings)
    const body = JSON.parse(String(init.body))
    writes.push({ path, body })
    if (fail) return Response.json({ detail: '连接参数保存失败' }, { status: 500 })
    if (path === 'database/welding/configuration') {
      settings = { ...settings, welding: { ...settings.welding, timezone: body.timezone } }
    } else {
      settings = { ...settings, worker: { ...settings.worker, host: body.worker.host } }
    }
    return ok({ restart_required: false, message: '配置已保存并热加载' })
  })
  const { wrapper } = await mountSettings(DatabaseView)
  const press = (text: string) => wrapper.findAll('button').find(button => button.text() === text)!.trigger('click')
  const field = (name: string) => wrapper.get<HTMLInputElement>(`input[name="${name}"]`)
  const editor = () => wrapper.get('form:has(input[name="worker_host"], input[name="weld_host"], input[name="production_host"])')
  const row = (label: string) => wrapper.findAll('tbody tr').find(item => item.text().includes(label))!
  const edit = (label: string) => row(label).get('button').trigger('click')
  const save = async () => { await editor().trigger('submit'); await flushPromises() }

  expect(wrapper.get('[aria-label="数据库状态"]').exists()).toBe(true)
  expect(wrapper.text()).not.toMatch(/生产查询映射|固定数据集预览|SQL 工作台/)
  expect(wrapper.text()).toContain('保存后自动热加载')
  await edit('工人 HR 库')
  expect(field('worker_password').element.value).toBe('')
  await field('worker_host').setValue('updated-hr')
  expect(row('工人 HR 库').text()).toContain('saved-host')
  const beforeSave = requests.length
  await save()
  expect(writes[0]).toEqual({ path: 'database/configuration', body: expect.objectContaining({
    worker: expect.objectContaining({ host: 'updated-hr', password: null }),
    production: expect.objectContaining({ host: 'saved-host', password: null }),
  }) })
  expect(requests.slice(beforeSave + 1).sort()).toEqual(['database/configuration', 'database/status', 'production-lines'])
  expect(row('工人 HR 库').text()).toContain('updated-hr')
  expect(wrapper.text()).toContain('配置已保存并热加载')

  const sourceMode = wrapper.findAllComponents(AppSelect).find(select => select.find('select[name="data_source_mode"]').exists())!
  expect(sourceMode.findAll('select option').map(item => item.text())).toEqual(['演示数据', 'SQL Server'])
  sourceMode.vm.$emit('update:modelValue', 'sqlserver')
  sourceMode.vm.$emit('change', { target: { value: 'sqlserver' }, value: 'sqlserver' })
  await edit('小组立焊接线')
  await field('weld_timezone').setValue('UTC')
  await field('weld_password').setValue('fixture-new-password')
  await save()
  expect(writes[1]).toEqual({ path: 'database/welding/configuration', body: expect.objectContaining({
    timezone: 'UTC', password: 'fixture-new-password',
  }) })
  expect(sourceMode.props('modelValue')).toBe('sqlserver')
  await edit('小组立焊接线')
  expect(field('weld_password').element.value).toBe('')
  expect(field('weld_timezone').element.value).toBe('UTC')
  await press('取消')

  fail = true
  await edit('生产报表库')
  await field('production_host').setValue('failed-host')
  await save()
  expect(wrapper.text()).toContain('连接参数保存失败')
  expect(field('production_host').element.value).toBe('failed-host')
  expect(row('生产报表库').text()).toContain('saved-host')
  await press('取消')
  await flushPromises()
  await press('继续编辑')
  expect(field('production_host').element.value).toBe('failed-host')
  expect(writes).toHaveLength(3)
})

test.each([false, true])('a late database response cannot overwrite the selected line (failure %s)', async fail => {
  let finishOld: (response: Response) => void = () => { throw new Error('Missing initial configuration read') }
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const path = String(input).split('/api/v1/')[1]
    if (path === 'production-lines') return ok(catalog)
    if (path === 'database/status') return ok({ mode: 'demo', driver: connection.driver })
    const line = new Headers(init?.headers).get('X-Production-Line')
    expect(line).toMatch(/^(Cutter01|weld-a)$/)
    if (line === 'Cutter01') return new Promise(resolve => { finishOld = resolve })
    return ok({ ...databaseSettings, welding: { ...databaseSettings.welding, host: 'active-welding' } })
  })
  const { wrapper } = await mountSettings(DatabaseView)
  expect(wrapper.text()).toContain('正在读取数据库配置')
  wrapper.findAllComponents(AppSelect).find(select => select.props('id') === 'configuration-line')!.vm.$emit('update:modelValue', 'weld-a')
  await flushPromises()
  expect(wrapper.text()).toContain('active-welding')
  finishOld(fail ? Response.json({ detail: '过期配置失败' }, { status: 500 }) : ok(databaseSettings))
  await flushPromises()
  expect(wrapper.text()).toContain('active-welding')
  expect(wrapper.text()).not.toMatch(/saved-welding|过期配置失败|工人 HR 库/)
})

test('the same connection form tests SQL Server and MySQL without persisting credentials', async () => {
  const writes = []
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const path = String(input).split('/api/v1/')[1]
    if (path === 'production-lines') return ok(catalog)
    if (path === 'database/status') return ok({ mode: 'demo', driver: connection.driver })
    if (path === 'database/configuration' && !init?.method) return ok(databaseSettings)
    const body = JSON.parse(String(init?.body))
    writes.push({ path, method: init?.method, body })
    return ok({ database: body.database, elapsed_ms: 2 })
  })
  const { wrapper } = await mountSettings(DatabaseView)
  const fill = (key: string, value: string) => wrapper.get(`input[name="test_${key}"]`).setValue(value)
  const submit = async () => { await wrapper.get('form:has(input[name="test_host"])').trigger('submit'); await flushPromises() }
  await fill('host', 'fixture-sql')
  await fill('database', 'cutting')
  await fill('username', 'reader')
  await fill('password', 'fixture-password')
  await submit()
  wrapper.findAllComponents(AppSelect).find(select => select.find('select[name="test_source"]').exists())!.vm.$emit('update:modelValue', 'welding')
  await flushPromises()
  await fill('host', 'fixture-mysql')
  await fill('database', 'welding')
  expect(wrapper.get('input[name="test_timezone"]').exists()).toBe(true)
  expect(wrapper.find('input[name="test_driver"]').exists()).toBe(false)
  await submit()
  expect(writes).toEqual([
    { path: 'database/test-connection', method: 'POST', body: expect.objectContaining({ host: 'fixture-sql', password: 'fixture-password', port: 1433 }) },
    { path: 'database/welding/test-connection', method: 'POST', body: expect.objectContaining({ host: 'fixture-mysql', password: null, port: 3306 }) },
  ])
  expect(wrapper.text()).toContain('welding · 2 ms · 凭据未保存')
  expect(wrapper.text()).toContain('saved-welding')
  expect(window.dispatchEvent(new Event('beforeunload', { cancelable: true }))).toBe(true)
})

test('automation searches schedules and refreshes the current delivery page after a pause or retry', async () => {
  let enabled = true
  let release: () => void = () => { throw new Error('Missing pause request') }
  const writes = []
  const reads = { status: 0, schedules: 0, deliveries: [] as number[] }
  const schedule = { schedule_id: 'saved', name: '每日车间报表', enabled, device_id: 'Cutter01', report_type: 'workshop',
    frequency: 'daily', run_time: '18:00', recipients: ['one@example.com'] }
  setDeviceCatalog(catalog)
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = new URL(String(input), 'https://fixture.example')
    const path = url.pathname.split('/automation/')[1]
    if (path === 'schedules/saved' && init?.method === 'PATCH') {
      const body = JSON.parse(String(init.body))
      writes.push({ path, body })
      if (body.enabled) return Response.json({ detail: '暂时无法恢复' }, { status: 503 })
      await new Promise<void>(resolve => { release = resolve })
      enabled = false
      return ok({ enabled, next_run_at: null })
    }
    if (path?.endsWith('/retry')) { writes.push({ path }); return ok({ queued: true }) }
    if (path === 'configuration') return ok({})
    if (path === 'status') { reads.status++; return ok({ smtp: { enabled: true, configured: true } }) }
    if (path === 'schedules') { reads.schedules++; return ok({ items: [{ ...schedule, enabled }, { ...schedule, schedule_id: 'monthly', name: '月报 Summary' }] }) }
    if (path === 'deliveries') {
      const page = Number(url.searchParams.get('page'))
      reads.deliveries.push(page)
      return ok({ total: 25, page, page_size: 10, items: [
        { delivery_id: `delivery-${page}`, schedule_name: `测试邮件 ${page}`, recipients: schedule.recipients,
          recipient_results: {}, status: 'failed', last_error: '连接失败' },
        { delivery_id: 'waiting', schedule_name: '待生成分析', recipients: schedule.recipients,
          recipient_results: {}, status: 'analysis_wait', last_error: '旧报告缺少成功分析，等待重新生成' },
        { delivery_id: 'unknown', schedule_name: '邮件待核对', recipients: schedule.recipients,
          recipient_results: { 'one@example.com': { status: 'unknown' } }, status: 'unknown' },
      ] })
    }
    throw new Error(`Unexpected request ${String(input)}`)
  })
  const { wrapper } = await mountSettings(AutomationView)
  const schedules = wrapper.get('[aria-labelledby="automation-schedules-title"]')
  const deliveries = wrapper.get('[aria-label="邮件发送记录"]')
  const waiting = deliveries.findAll('tbody tr').find(row => row.text().includes('待生成分析'))!
  expect(waiting.text()).toContain('等待分析生成')
  expect(waiting.text()).toContain('等待重新生成')
  expect(waiting.findAll('button').map(button => button.text())).toContain('核对记录')
  expect(waiting.text()).not.toContain('重新发送')
  const unknown = deliveries.findAll('tbody tr').find(row => row.text().includes('邮件待核对'))!
  expect(unknown.text()).toContain('结果待核对')
  expect(unknown.text()).toContain('核对结果')
  const search = schedules.get('input[aria-label="任务名称关键词"]')
  await search.setValue(' summary ')
  await schedules.get('form').trigger('submit')
  expect(schedules.text()).toContain('月报 Summary')
  expect(schedules.text()).not.toContain('每日车间报表')
  await search.setValue('')
  await schedules.get('form').trigger('submit')
  await deliveries.get('button[aria-label="第 2 页"]').trigger('click')
  await flushPromises()
  const firstSchedule = () => schedules.findAll('article').find(card => card.text().includes(schedule.name))!
  const pressSchedule = (label: string) => firstSchedule().findAll('button').find(button => button.text() === label)!
  await pressSchedule('暂停').trigger('click')
  await pressSchedule('暂停').trigger('click')
  expect(firstSchedule().text()).toContain(schedule.name)
  expect(writes).toEqual([{ path: 'schedules/saved', body: { enabled: false } }])
  release()
  await flushPromises()
  await pressSchedule('恢复').trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('暂时无法恢复')
  expect(pressSchedule('恢复').exists()).toBe(true)
  await deliveries.findAll('button').find(button => button.text() === '重新发送')!.trigger('click')
  await flushPromises()
  expect(writes).toEqual([
    { path: 'schedules/saved', body: { enabled: false } }, { path: 'schedules/saved', body: { enabled: true } }, { path: 'deliveries/delivery-2/retry' },
  ])
  expect(reads).toEqual({ status: 3, schedules: 3, deliveries: [1, 2, 2, 2] })
  expect(deliveries.get('[aria-current="page"]').text()).toBe('2')
})

test('the schedule drawer keeps inputs on save failure and closes after a successful retry', async () => {
  let fail = true
  const submitted = []
  setDeviceCatalog(catalog)
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input)
    if (url.endsWith('/schedules') && init?.method === 'POST') {
      const body = JSON.parse(String(init.body))
      submitted.push(body)
      return fail ? Response.json({ detail: '任务保存失败' }, { status: 500 }) : ok({ ...body, schedule_id: 'saved' })
    }
    if (url.endsWith('/configuration')) return ok({})
    if (url.endsWith('/status')) return ok({ smtp: { enabled: true, configured: true } })
    return ok({ items: [], total: 0, page: 1, page_size: 10 })
  })
  const { wrapper } = await mountSettings(AutomationView)
  const press = (label: string) => wrapper.findAll('button').find(button => button.text() === label)!.trigger('click')
  await press('新建定时任务')
  const name = wrapper.get<HTMLInputElement>('input[name="schedule_name"]')
  await name.setValue('装配车间日报')
  await press('＋ 添加邮箱')
  await wrapper.get('input[aria-label="收件人邮箱"]').setValue('manager@example.com')
  await press('添加')
  const form = () => wrapper.get('form:has(input[name="schedule_name"])')
  await form().trigger('submit')
  await flushPromises()
  expect(wrapper.get('[role="alert"]').text()).toContain('任务保存失败')
  expect(form().text()).toContain('manager@example.com')
  expect(name.element.value).toBe('装配车间日报')
  fail = false
  await form().trigger('submit')
  await flushPromises()
  expect(wrapper.findAll('input[name="schedule_name"]').some(input => input.isVisible())).toBe(false)
  expect(submitted).toEqual([
    expect.objectContaining({ name: '装配车间日报', recipients: ['manager@example.com'] }),
    expect.objectContaining({ name: '装配车间日报', recipients: ['manager@example.com'] }),
  ])
})
