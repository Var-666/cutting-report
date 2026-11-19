import assert from 'node:assert/strict'
import test from 'node:test'
import { renderComponent } from './vue-component-helpers.mjs'

test('statistics freshness renders the original Beijing timestamp and distinct refresh states', async () => {
  const report = { line_id: 'line', report_type: 'report', data_version: 'version',
    data_as_of: '2026-11-19T02:21:57Z', refreshed_at: '2026-11-19T02:22:01Z',
    expires_at: '2026-11-19T02:32:01Z', source: 'snapshot', stale: false, refreshing: false, refresh_failed: false }
  for (const [changes, label] of [[{}, '更新于'], [{ stale: true }, '数据待更新'], [{ refreshing: true }, '后台更新中'],
    [{ refresh_failed: true, stale: true, refreshing: true }, '刷新失败，保留上次结果']]) {
    const html = await renderComponent('components/ReportDataStatus', { meta: { report_data: { ...report, ...changes } }, busy: true })
    assert.ok(html.includes('2026年11月19日 10:21:57'))
    assert.ok(html.includes('datetime="2026-11-19T02:21:57Z"'))
    assert.ok(html.includes(label))
    assert.match(html, /<button[^>]*disabled/)
    if (changes.refresh_failed) assert.ok(!html.includes('后台更新中'))
  }
  assert.ok(!(await renderComponent('components/ReportDataStatus')).includes('更新于'))
})
