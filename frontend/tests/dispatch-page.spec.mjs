// @vitest-environment jsdom

import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { NAutoComplete, NDialogProvider } from 'naive-ui'
import { afterEach, expect, test, vi } from 'vitest'
import { KeepAlive, h } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { reportCache } from '../src/api/reportCache'
import AppSelect from '../src/components/AppSelect.vue'
import DispatchBatchDialog from '../src/components/dispatch/DispatchBatchDialog.vue'
import { setupSoybeanComponents } from '../src/vendor/soybean-admin/components'
import CuttingDispatchView from '../src/views/CuttingDispatchView.vue'
import { stubTableLayout } from './table-layout-fixture.mjs'

const response = data => Response.json({ success: true, data })
const ready = {
  can_push: true, pdm: { label: 'PDM 正常' }, file_source: { label: '文件服务正常' },
  magic_box: { configured: true, machine_ips: ['10.147.1.40', '10.147.1.38', '10.147.1.41'] },
}
const projects = [
  { project_id: 'P1', project_name: '扬州切割联调项目', ship_no: 'CMYZ0177' },
  { project_id: 'P2', project_name: '文件派发模拟项目', ship_no: 'CMYZ0188' },
  { project_id: 'P3', project_name: '备用生产项目', ship_no: 'CMYZ0199' },
]
const catalog = (id, title = `目录-${id}`) => ({ catalog_id: id, title, children: [] })
const file = (id, status = undefined) => ({
  document_id: id, version_id: 'v1', normalized_path: `files/${id}.gen`, document_no: `${id}.gen`,
  path_available: true, operate_time: '2026-09-30T07:30:00+08:00', dispatch_status: status,
})

enableAutoUnmount(afterEach)
afterEach(() => {
  vi.useRealTimers()
  reportCache.invalidate('cutting:')
  Reflect.deleteProperty(Element.prototype, 'scrollTo')
})

async function open({ handlers = {}, query = {} } = {}) {
  stubTableLayout()
  vi.stubGlobal('matchMedia', () => Object.assign(new EventTarget(), { matches: false }))
  Object.defineProperty(Element.prototype, 'scrollTo', { configurable: true, value: vi.fn() })
  const calls = []
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = new URL(String(input), 'http://localhost')
    if (url.pathname.endsWith('/cancel') || url.pathname === '/api/v1/operations/browser') return Response.json({})
    const path = url.pathname.replace(/^\/api\/v1\/cutting\//, '')
    const call = { path, query: url.searchParams, signal: init?.signal, method: init?.method ?? 'GET', body: init?.body }
    calls.push(call)
    if (handlers[path]) return handlers[path](call, calls.filter(item => item.path === path).length)
    if (path === 'status') return response(ready)
    if (path === 'projects') return response({ items: projects })
    if (path === 'catalogs') {
      const id = call.query.get('project_id')
      const root = { catalog_id: id, title: projects.find(project => project.project_id === id).project_name, children: [catalog(`C-${id}`)] }
      return response({ tree: [root] })
    }
    if (path === 'machines') return response({ items: [
      { machine_ip: '10.147.1.40', online: true }, { machine_ip: '10.147.1.38', online: false },
      { machine_ip: '10.147.1.41', server_name: '其他机床', online: true },
      { machine_ip: '10.147.1.42', online: true },
    ] })
    if (path === 'files' && call.query.get('keyword') === '不存在') return response({ items: [], page: 1, page_size: 10, total: 0 })
    if (path === 'files') return response({ items: [
      file('Z806C080A001', { status: 'sent', status_label: '已派发', stage_label: '推送完成' }), file('待派发文件'),
    ], page: 1, page_size: 10, total: 2 })
    if (path === 'dispatches') return response({ items: [], total: 0, page: 1, page_size: 10 })
    if (path === 'dispatches/trend') return response({ ...Object.fromEntries(call.query), items: [], machine_ips: [], timezone: 'Asia/Shanghai' })
    throw new Error(`Unexpected request ${url.pathname}`)
  })
  const router = createRouter({
    history: createMemoryHistory(), routes: [
      { path: '/dispatch', name: 'cutting-dispatch', component: CuttingDispatchView },
      { path: '/other', component: { render: () => h('h1', '其他页面') } },
    ],
  })
  await router.push({ path: '/dispatch', query })
  const wrapper = mount({
    render: () => h(NDialogProvider, {}, { default: () => h(RouterView, {}, {
      default: ({ Component }) => h(KeepAlive, {}, { default: () => Component }),
    }) }),
  }, { attachTo: document.body, global: {
    plugins: [router, { install: setupSoybeanComponents }], components: { AppSelect },
    stubs: { DispatchHistoryTrend: true, teleport: true },
  } })
  await flushPromises()
  return { wrapper, router, calls, reads: path => calls.filter(call => call.path === path),
    async chooseProject(id) {
      wrapper.getComponent(NAutoComplete).vm.$emit('select', id)
      await flushPromises()
    },
    async click(label) {
      await wrapper.findAll('button').find(button => button.text() === label).trigger('click')
      await flushPromises()
    },
  }
}

test('a slow readiness check leaves the project directory and history available and failure does not erase either', async () => {
  let finish
  const page = await open({ handlers: { status: () => new Promise(resolve => { finish = resolve }) } })
  expect(page.wrapper.text()).toContain('3 个支持项目')
  expect(page.wrapper.text()).toContain('暂无派发记录')
  expect(page.reads('dispatches')).toHaveLength(1)
  expect(page.wrapper.get('[aria-label="文件派发服务状态"]').text()).toContain('正在检查')
  finish(Response.json({ detail: 'SYS_CONFIG 不可用' }, { status: 503 }))
  await flushPromises()
  expect(page.wrapper.get('[role="alert"]').text()).toContain('SYS_CONFIG 不可用')
  expect(page.wrapper.text()).toContain('3 个支持项目')
  expect(page.wrapper.text()).toContain('暂无派发记录')
})

test('a timed out readiness check can be retried without reloading the directory or enabling dispatch early', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const page = await open({ handlers: { status: (_call, attempt) => attempt === 1 ? new Promise(() => {}) : response(ready) } })
  expect(page.wrapper.get('button[aria-label="批量设置参数并派发已选文件"]').element.disabled).toBe(true)
  await vi.advanceTimersByTimeAsync(30000)
  expect(page.reads('status')[0].signal.aborted).toBe(true)
  expect(page.wrapper.get('[role="alert"]').text()).toContain('状态检查超时')
  expect(page.wrapper.text()).toContain('3 个支持项目')
  await page.click('重新检查')
  expect(page.wrapper.get('[aria-label="文件派发服务状态"]').text()).toContain('PDM 正常')
  expect(page.reads('status')).toHaveLength(2)
  expect(page.reads('projects')).toHaveLength(1)
})

test('reopening after interruption retries the directory and ignores late answers from the old activation', async () => {
  let finishStatus, finishProjects
  const page = await open({ handlers: {
    status: (_call, attempt) => attempt === 1 ? new Promise(resolve => { finishStatus = resolve }) : response(ready),
    projects: (_call, attempt) => attempt === 1 ? new Promise(resolve => { finishProjects = resolve }) : response({ items: projects }),
  } })
  await page.router.push('/other')
  expect(page.reads('status')[0].signal.aborted).toBe(true)
  expect(page.reads('projects')[0].signal.aborted).toBe(true)
  await page.router.push('/dispatch')
  await flushPromises()
  expect(page.wrapper.text()).toContain('3 个支持项目')
  expect(page.wrapper.text()).toContain('PDM 正常')
  finishStatus(response({ can_push: false, pdm: { label: '旧状态' } }))
  finishProjects(response({ items: [] }))
  await flushPromises()
  expect(page.wrapper.text()).toContain('3 个支持项目')
  expect(page.wrapper.text()).not.toContain('旧状态')
  expect(page.reads('projects')).toHaveLength(2)
})

test('a completed empty catalog stays cached after navigation and cancelled machine status cannot show stale fallback', async () => {
  let rejectMachines
  const page = await open({ handlers: {
    projects: () => response({ items: [] }),
    machines: (_call, attempt) => attempt === 1 ? new Promise((resolve, reject) => { rejectMachines = reject }) : response({ items: [] }),
  } })
  expect(page.wrapper.text()).toContain('没有找到匹配的项目')
  await page.router.push('/other')
  expect(page.reads('machines')[0].signal.aborted).toBe(true)
  rejectMachines(new DOMException('旧机床读取取消', 'AbortError'))
  await page.router.push('/dispatch')
  await flushPromises()
  expect(page.reads('projects')).toHaveLength(1)
  expect(page.wrapper.text()).toContain('没有找到匹配的项目')
  expect(page.wrapper.text()).not.toContain('机床在线状态读取失败')
})

test('a linked project is restored without duplicating its directory root; file status follows the operation time', async () => {
  const page = await open({ query: { project_id: 'P1', source: 'notice' } })
  const tree = page.wrapper.get('[aria-label="PDM项目目录"]')
  expect(tree.text().split('扬州切割联调项目')).toHaveLength(2)
  expect(tree.text()).toContain('目录-C-P1')
  await page.click('目录-C-P1')
  const files = page.wrapper.get('[aria-label="文件查询结果"]')
  const headers = files.findAll('thead th').map(cell => cell.text())
  expect(headers.slice(-3)).toEqual(['操作时间', '派发状态', '操作'])
  expect(files.text()).toContain('已派发')
  expect(files.text()).toContain('推送完成')
  expect(files.text()).toContain('暂无记录')
  await page.wrapper.get('input[name="file_keyword"]').setValue('不存在')
  await page.wrapper.get('form').trigger('submit')
  await flushPromises()
  expect(page.reads('files').at(-1).query.get('keyword')).toBe('不存在')
  expect(files.text()).toContain('当前目录没有匹配的文件')
  expect(page.router.currentRoute.value.query).toEqual({ project_id: 'P1', source: 'notice' })
})

test('project search preserves typed terms and late catalogs cannot replace the current project or file selection', async () => {
  let finish
  const handlers = { catalogs: call => {
    const id = call.query.get('project_id')
    return id === 'P2' ? new Promise(resolve => { finish = resolve }) : response({ tree: [catalog(`C-${id}`)] })
  } }
  const page = await open({ handlers })
  const search = page.wrapper.get('input[name="cutting_project"]')
  await search.setValue('  cmyz0177  ')
  const suggestions = page.wrapper.getComponent(NAutoComplete)
  expect(suggestions.props('options')).toEqual([{ value: 'P1', displayLabel: '扬州切割联调项目 · CMYZ0177' }])
  await page.chooseProject('P1')
  expect(search.element.value).toBe('  cmyz0177  ')
  await page.click('目录-C-P1')
  await page.wrapper.get('[role="checkbox"][aria-label="选择文件 Z806C080A001.gen"]').trigger('click')
  expect(page.wrapper.text()).toMatch(/已选择\s*1/)
  await page.chooseProject('P1')
  expect(page.wrapper.text()).toMatch(/已选择\s*1/)
  expect(page.reads('catalogs')).toHaveLength(1)
  await page.chooseProject('P2')
  expect(page.wrapper.text()).toMatch(/已选择\s*0/)
  expect(page.wrapper.text()).not.toContain('Z806C080A001.gen')
  await search.setValue('备用')
  await page.chooseProject('P3')
  expect(page.reads('catalogs')[1].signal.aborted).toBe(true)
  finish(response({ tree: [catalog('stale', '迟到目录')] }))
  await flushPromises()
  expect(page.wrapper.text()).not.toContain('迟到目录')
  expect(page.wrapper.get('[aria-label="PDM项目目录"]').text()).toContain('目录-C-P3')
  expect(search.element.value).toBe('备用')
  expect(page.router.currentRoute.value.query.project_id).toBe('P3')
})

test('machine options show names and addresses while keeping the raw IP as the dispatch value', async () => {
  const page = await open({ query: { project_id: 'P1' } })
  await page.click('目录-C-P1')
  await page.wrapper.get('button[aria-label="派发 Z806C080A001.gen"]').trigger('click')
  await flushPromises()
  const batch = page.wrapper.getComponent(DispatchBatchDialog)
  const machine = batch.findAllComponents(AppSelect).find(control => control.findAll('option').some(option => option.text().includes('20kW')))
  expect(machine).toBeDefined()
  const options = machine.findAll('option').map(option => [option.attributes('value'), option.text()])
  expect(options).toEqual(expect.arrayContaining([
    ['10.147.1.40', expect.stringContaining('20kW 切割机')],
    ['10.147.1.38', expect.stringContaining('30kW 切割机')],
    ['10.147.1.41', expect.stringContaining('其他机床')],
    ['10.147.1.42', expect.stringContaining('未命名机床')],
  ]))
  machine.vm.$emit('update:modelValue', '10.147.1.41')
  await flushPromises()
  expect(batch.props('machineIp')).toBe('10.147.1.41')
})

test('directory-wide selection shows progress and selects available files across pages', async () => {
  let finish
  const page = await open({ query: { project_id: 'P1' }, handlers: {
    files(call) {
      if (call.query.get('page_size') === '100') return new Promise(resolve => { finish = resolve })
      return response({ items: [file('first')], page: 1, page_size: 10, total: 11 })
    },
  } })
  await page.click('目录-C-P1')
  await page.wrapper.get('[role="checkbox"][aria-label="全选当前目录所有分页的可派发文件"]').trigger('click')
  await flushPromises()
  expect(page.wrapper.text()).toContain('正在选择所有分页的文件')
  expect(page.reads('files').at(-1).query.get('page_size')).toBe('100')
  finish(response({ items: [file('first'), file('off-page'), { ...file('unavailable'), path_available: false }], total: 3 }))
  await flushPromises()
  expect(page.wrapper.text()).toMatch(/已选择\s*2/)
  expect(page.wrapper.text()).not.toContain('正在选择所有分页的文件')
  expect(page.wrapper.get('[role="checkbox"][aria-label="全选当前目录所有分页的可派发文件"]').attributes('aria-checked')).toBe('true')
})
