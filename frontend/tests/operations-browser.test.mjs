import assert from 'node:assert/strict'
import test from 'node:test'
import { observeBrowserErrors, reportApiFailure } from '../src/api/browserObservations.ts'
import { operations } from '../src/api/operations.ts'
import { incidentExplanation, incidentLogQuery, operationStages } from '../src/components/operations/presentation.ts'

test('incident explanations describe impact and action without treating missing energy as zero', () => {
  const energy = incidentExplanation('ENERGY_TASK_DATA_QUALITY', '已结束任务缺少有效能耗读数')
  assert.match(energy.meaning, /电耗或气耗读数/)
  assert.match(energy.impact, /缺失值不按零/)
  assert.match(energy.action, /仪表绑定、采样记录及任务起止时间/)
  assert.equal(operationStages.current_refresh, '生产统计刷新')
  const unknown = incidentExplanation('CUSTOM_FAILURE', '某业务查询失败')
  assert.equal(unknown.meaning, '某业务查询失败')
  assert.match(unknown.impact, /需结合/)
  assert.match(unknown.action, /技术详情/)
})

test('browser observations upload allowlisted summaries once and do not retry', async t => {
  const calls = []
  const events = new EventTarget()
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push({ url, body: JSON.parse(options.body) })
    throw new Error('network failed')
  })
  Object.defineProperty(globalThis, 'window', { value: events, configurable: true })
  const observer = observeBrowserErrors()
  t.after(() => {
    observer.stop()
    delete globalThis.window
  })
  observer.vue()
  observer.vue()
  events.dispatchEvent(new Event('error'))
  reportApiFailure('/api/v1/operations/browser', 503)
  reportApiFailure('/api/v1/reports?token=private-token', 502, 'web_a1')
  reportApiFailure('/api/v1/reports', 502, 'web_second')
  await Promise.resolve()
  assert.deepEqual(calls, [
    { url: '/api/v1/operations/browser', body: { kind: 'vue' } },
    { url: '/api/v1/operations/browser', body: { kind: 'javascript' } },
    { url: '/api/v1/operations/browser', body: { kind: 'api', request_id: 'web_a1' } },
  ])
})

test('incident log links keep the business identity and include the verified recovery', () => {
  const event = {
    first_seen_at: '2026-11-16T10:00:00Z', last_seen_at: '2026-11-16T10:08:00Z',
    recovered_at: '2026-11-16T11:00:00Z', role: 'worker', component: 'database-monitor',
    resource_id: 'fault-a', source_id: 'target-a', error_code: 'DATABASE_TCP_DOWN',
    context: { related_kind: 'database-monitor' },
  }
  assert.deepEqual(incidentLogQuery(event), {
    start: '2026-11-16T09:55:00.000Z', end: '2026-11-16T11:05:00.000Z', resource_id: 'fault-a',
  })
  const mail = incidentLogQuery({ ...event, resource_id: 'mail-a', context: { related_kind: 'email', report_id: 'report-a' } })
  assert.equal(mail.resource_id, 'mail-a')
  assert.equal(mail.report_id, undefined)
  const task = incidentLogQuery({ ...event, context: { related_kind: 'background', task_id: 'task-a' } })
  assert.equal(task.task_id, 'task-a')
  const quality = incidentLogQuery({ ...event, error_code: 'ANALYSIS_DATA_QUALITY', resource_id: 'scope-id',
    context: { analysis_id: 'old-analysis', request_id: 'old-request' } })
  assert.equal(quality.resource_id, 'scope-id')
  assert.equal(quality.error_code, 'ANALYSIS_DATA_QUALITY')
  assert.equal(quality.role, undefined)
  assert.equal(quality.analysis_id, undefined)
  const historical = incidentLogQuery({ ...event, first_seen_at: '2026-01-01T10:00:00Z' })
  assert.equal(Date.parse(historical.end) - Date.parse(historical.start), 90 * 86400_000)
})

test('log export sends the selected filters and preserves the download coverage warning', async t => {
  const content = '{"report_id":"report-a","message":"查询异常"}\n'
  const signal = new AbortController().signal
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    const request = new URL(url, 'https://test.invalid')
    assert.equal(request.pathname, '/api/v1/operations/logs/export')
    assert.deepEqual(Object.fromEntries(request.searchParams), { report_id: 'report-a', level: 'ERROR', keyword: '查询异常' })
    assert.equal(options.credentials, 'same-origin')
    assert.equal(options.signal, signal)
    return new Response(content, { headers: {
      'Content-Type': 'application/x-ndjson', 'X-Log-Coverage-Warnings': 'record_limit',
    } })
  })
  const result = await operations.exportLogs({ report_id: 'report-a', level: 'ERROR', keyword: '查询异常' }, signal)
  assert.equal(await result.blob.text(), content)
  assert.equal(result.headers.get('X-Log-Coverage-Warnings'), 'record_limit')
})
