import assert from 'node:assert/strict'
import test from 'node:test'
import { renderComponent } from './vue-component-helpers.mjs'
import { listDatabaseMonitors, saveDatabaseMonitor, deleteDatabaseMonitor, listDatabaseMonitorIncidents } from '../src/api/databaseMonitors.ts'

function item(state, changes = {}) {
  return { monitor_id: state, name: `数据库-${state}`, ip: '192.0.2.10', port: 1433, enabled: true, state,
    last_checked_at: 1000, last_success_at: 1000, failure_started_at: null, failures: 0, successes: 0, last_error: null, ...changes }
}
const props = { loading: false, busy: false, now: 1120, timezone: 'Asia/Shanghai', unavailable: false }

test('monitor table renders real states, complete text and management actions', async () => {
  const rows = ['healthy', 'suspect', 'down', 'recovering', 'unknown'].map(state => item(state))
  rows.push(item('paused', { enabled: false }))
  rows[2].name = '生产数据库以及关联业务的完整长名称'.repeat(5)
  rows[2].last_error = '网络或主机不可达，请核对服务端运行状况'.repeat(4)
  rows[2].failure_started_at = 940
  const html = await renderComponent('components/DatabaseMonitorTable', { ...props, items: rows })
  for (const text of ['端口可达', '异常待确认', '连接不可用', '恢复确认中', '状态未知', '已暂停', '编辑', '暂停', '启用', '删除', rows[2].name, rows[2].last_error, '3 分钟']) assert.ok(html.includes(text), text)
  assert.match(html, /1970年1月1日 08:16:40/)
  assert.doesNotMatch(html, /<br\b/)
})

test('stale or unreadable monitor state never reports a previously reachable port as healthy', async () => {
  for (const changes of [{ now: 1181 }, { unavailable: true }]) {
    const html = await renderComponent('components/DatabaseMonitorTable', { ...props, ...changes, items: [item('healthy')] })
    assert.match(html, /状态未知/)
    assert.doesNotMatch(html, /端口可达/)
  }
  const paused = await renderComponent('components/DatabaseMonitorTable', { ...props, unavailable: true, items: [item('paused', { enabled: false })] })
  assert.match(paused, /已暂停/)
})

test('empty monitoring table explains how to add the first target', async () => {
  const html = await renderComponent('components/DatabaseMonitorTable', { ...props, items: [] })
  assert.match(html, /暂无监控目标/)
  assert.match(html, /添加监控/)
})

test('monitor API sends the intended endpoint without credentials or probe requests', async () => {
  const original = globalThis.fetch
  const calls = []
  globalThis.fetch = async (url, options) => {
    calls.push({ url: String(url), ...options })
    return { ok: true, status: 200, headers: { get: () => null }, json: async () => ({ success: true, data: { monitor_id: 'one', items: [] } }) }
  }
  try {
    await listDatabaseMonitors()
    await saveDatabaseMonitor({ name: '生产库', ip: '192.0.2.1', port: 1433 })
    await saveDatabaseMonitor({ name: '生产库', ip: '192.0.2.1', port: 1433, enabled: false }, 'one')
    await deleteDatabaseMonitor('one')
    await listDatabaseMonitorIncidents(2)
    assert.deepEqual(calls.map(call => call.method || 'GET'), ['GET', 'POST', 'PUT', 'DELETE', 'GET'])
    assert.match(calls[4].url, /incidents\/page\?page=2&page_size=15/)
    assert.deepEqual(JSON.parse(calls[1].body), { name: '生产库', ip: '192.0.2.1', port: 1433 })
    assert.equal(JSON.parse(calls[2].body).enabled, false)
  } finally { globalThis.fetch = original }
})
