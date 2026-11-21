// @vitest-environment jsdom

import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { NDialogProvider } from 'naive-ui'
import { afterEach, expect, test, vi } from 'vitest'
import { h } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import AppSelect from '../src/components/AppSelect.vue'
import DatabaseSourcesPanel from '../src/components/DatabaseSourcesPanel.vue'
import { stubTableLayout } from './table-layout-fixture.mjs'

enableAutoUnmount(afterEach)

const root = '/api/v1/database/sources'
const row = { id: 'quality', name: '质量检测', engine: 'sqlserver', enabled: true, host: 'old-host', port: 1433,
  database: 'quality', username: 'reader', password_configured: true, configured: true, references: [] }
const response = data => Response.json({ success: true, data })

async function openPage(overrides = {}) {
  const requests = []
  let current = overrides.row || row
  stubTableLayout()
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const { pathname } = new URL(String(input), 'http://localhost')
    if (pathname === '/api/v1/operations/browser' || pathname.endsWith('/cancel')) return Response.json({})
    const method = init?.method ?? 'GET'
    const body = init?.body && JSON.parse(init.body)
    requests.push({ pathname, method, body })
    if (pathname === root && method === 'GET') return overrides.list ? overrides.list() : response({ items: current ? [current] : [] })
    if (pathname === root + '/test-connection') return response({ connected: true, elapsed_ms: 8, message: '参数未保存' })
    if (pathname === root + '/quality' && method === 'GET') return response({ ...current, host: 'latest-host' })
    if (method === 'PUT') return overrides.save ? overrides.save() : response(row)
    if (method === 'PATCH') { current = { ...current, enabled: body.enabled }; return response(current) }
    if (method === 'DELETE') { current = null; return response({ id: 'quality' }) }
    throw new Error(`Unexpected request ${method} ${pathname}`)
  })
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: DatabaseSourcesPanel }] })
  await router.push('/')
  const wrapper = mount({ render: () => h(NDialogProvider, {}, { default: () => h(RouterView) }) }, {
    attachTo: document.body,
    global: { plugins: [router], components: { AppSelect }, stubs: { teleport: true, transition: false } },
  })
  const input = label => wrapper.get(`input[aria-label="${label}"]`)
  const button = (label, scope = wrapper) => scope.findAll('button').find(candidate => candidate.text() === label)
  const click = async (label, scope) => { await button(label, scope).trigger('click'); await flushPromises() }
  const form = () => wrapper.get('form')
  const chooseEngine = async engine => {
    wrapper.getComponent(AppSelect).vm.$emit('update:modelValue', engine)
    await flushPromises()
  }
  return { wrapper, requests, input, button, click, form, chooseEngine }
}

test('an edited source can be tested without saving and a blank password preserves stored credentials', async () => {
  const page = await openPage()
  await flushPromises()
  expect(page.wrapper.text()).toContain('质量检测')
  await page.click('编辑')
  expect(page.input('密码').element.value).toBe('')
  expect(page.input('密码').attributes('placeholder')).toBe('已配置，留空保留原密码')
  await page.input('服务器地址').setValue('edited-host')
  const editor = page.wrapper.get('[aria-label="编辑数据源"]')
  await page.click('测试连接', editor)
  expect(page.form().get('[role="status"]').text()).toContain('参数未保存')
  expect(page.requests.map(call => call.method)).toEqual(['GET', 'POST'])
  expect(page.requests[1]).toMatchObject({ pathname: root + '/test-connection', body: { host: 'edited-host', password: null } })
  await page.form().trigger('submit')
  await flushPromises()
  expect(page.requests[2]).toMatchObject({ pathname: root + '/quality', method: 'PUT', body: { host: 'edited-host', password: null } })
  expect(page.requests[2].body).not.toHaveProperty('password_configured')
  expect(page.requests[3]).toMatchObject({ method: 'GET', pathname: root })
  await page.click('测试连接', page.wrapper.get('tbody'))
  expect(page.requests.slice(-2)).toMatchObject([
    { pathname: root + '/quality', method: 'GET' },
    { pathname: root + '/test-connection', method: 'POST', body: { host: 'latest-host', password: null } },
  ])
  await page.click('停用')
  expect(page.requests.find(call => call.method === 'PATCH')).toMatchObject({ pathname: root + '/quality/enabled', body: { enabled: false } })
  expect(page.wrapper.get('tbody').text()).toContain('已停用')
  await page.wrapper.get('button[aria-label="删除数据源 质量检测"]').trigger('click')
  await flushPromises()
  expect(page.requests.some(call => call.method === 'DELETE')).toBe(false)
  await page.click('删除数据源')
  expect(page.requests.some(call => call.method === 'DELETE' && call.pathname === root + '/quality')).toBe(true)
  expect(page.wrapper.text()).toContain('暂无其他数据源')
})

test('a pending save locks the editor and a failed save keeps unsaved fields and the entered password', async () => {
  let finish
  const page = await openPage({ save: () => new Promise(resolve => { finish = resolve }) })
  await flushPromises()
  await page.click('编辑')
  const editor = page.wrapper.get('[aria-label="编辑数据源"]')
  await page.input('服务器地址').setValue('unsaved-host')
  await page.input('密码').setValue('fixture-password')
  await page.form().trigger('submit')
  await page.form().trigger('submit')
  await flushPromises()
  expect(page.requests.filter(call => call.method === 'PUT')).toHaveLength(1)
  expect(page.form().get('fieldset').element.disabled).toBe(true)
  await page.click('测试连接', editor)
  await page.click('取消', editor)
  expect(page.requests.some(call => call.method === 'POST')).toBe(false)
  expect(page.form().exists()).toBe(true)
  finish(Response.json({ detail: '保存失败' }, { status: 409 }))
  await flushPromises()
  expect(page.form().get('[role="alert"]').text()).toBe('保存失败')
  expect(page.input('服务器地址').element.value).toBe('unsaved-host')
  expect(page.input('密码').element.value).toBe('fixture-password')
  await page.click('取消', editor)
  expect(page.wrapper.text()).toContain('放弃未保存的修改')
  await page.click('继续编辑')
  expect(page.input('密码').element.value).toBe('fixture-password')
  await page.form().trigger('submit')
  finish(response(row))
  await flushPromises()
  await page.click('编辑')
  expect(page.input('密码').element.value).toBe('')
})

test('legacy TLS is opt-in, reaches test and save, and is removed when switching to MySQL', async () => {
  const page = await openPage({ row: { ...row, legacy_tls: true } })
  await flushPromises()
  await page.click('编辑')
  const tls = () => page.wrapper.findAll('[role="checkbox"]').find(control => control.text().includes('兼容旧版 SQL Server TLS'))
  expect(tls().attributes('aria-checked')).toBe('true')
  await page.chooseEngine('mysql')
  expect(tls()).toBeUndefined()
  expect(page.input('端口').element.value).toBe('3306')
  expect(page.form().text()).toContain('使用只读事务')
  const editor = page.wrapper.get('[aria-label="编辑数据源"]')
  await page.click('测试连接', editor)
  expect(page.requests.at(-1).body).toMatchObject({ engine: 'mysql', port: 3306, readonly: true, legacy_tls: false })
  await page.chooseEngine('sqlserver')
  expect(page.form().text()).not.toContain('参数未保存')
  expect(tls().attributes('aria-checked')).toBe('false')
  await tls().trigger('click')
  await page.click('测试连接', editor)
  expect(page.requests.at(-1).body).toMatchObject({ engine: 'sqlserver', legacy_tls: true })
  await page.form().trigger('submit')
  await flushPromises()
  expect(page.requests.find(call => call.method === 'PUT').body.legacy_tls).toBe(true)
})

test('a failed source list is announced and can be retried', async () => {
  let finish
  const page = await openPage({ list: () => new Promise(resolve => { finish = resolve }) })
  expect(page.wrapper.get('section[aria-busy]').attributes('aria-busy')).toBe('true')
  finish(Response.json({ detail: '读取失败' }, { status: 502 }))
  await flushPromises()
  expect(page.wrapper.get('[role="alert"]').text()).toContain('读取失败')
  await page.click('重新加载')
  finish(response({ items: [] }))
  await flushPromises()
  expect(page.wrapper.text()).toContain('暂无其他数据源')
  expect(page.wrapper.text()).not.toContain('读取失败')
  await page.click('新增数据源')
  expect(page.input('数据源 ID').element.disabled).toBe(false)
  await page.chooseEngine('mysql')
  expect(page.input('端口').element.value).toBe('3306')
})
