import assert from 'node:assert/strict'
import test from 'node:test'
import { renderComponent } from './vue-component-helpers.mjs'

test('report refresh states show only the relevant action', async () => {
  const report = { line_id: 'line', report_type: 'report', data_version: 'version',
    data_as_of: '2026-11-19T02:21:57Z', refreshed_at: '2026-11-19T02:22:01Z',
    expires_at: '2026-11-19T02:32:01Z', source: 'snapshot', stale: false, refreshing: false, refresh_failed: false }
  for (const meta of [undefined, { report_data: report }]) {
    const html = await renderComponent('components/ReportDataStatus', { meta })
    assert.doesNotMatch(html, /role="status"|<time\b|<button\b/)
  }
  for (const [changes, label] of [[{ stale: true }, '刷新旧数据'], [{ refreshing: true }, '刷新'],
    [{ stale: true, refreshing: true }, '刷新旧数据'],
    [{ refresh_failed: true, stale: true, refreshing: true }, '刷新旧数据'],
    [{ refresh_failed: true, stale: true }, '重试刷新']]) {
    const html = await renderComponent('components/ReportDataStatus', { meta: { report_data: { ...report, ...changes } }, busy: true })
    assert.doesNotMatch(html, /<time\b|更新于|统计截止|正在重试|保留上次结果|后台更新中|数据待更新/)
    assert.equal(html.replace(/<[^>]+>/g, '').trim(), label)
    assert.match(html, /<button[^>]*\sdisabled(?:=""|(?=\s|>))/)
  }
  const failed = await renderComponent('components/ReportDataStatus', { meta: { report_data: { ...report, refresh_failed: true } } })
  assert.match(failed, /<button[^>]*>[\s\S]*重试刷新/)
  assert.doesNotMatch(failed, /<button[^>]*\sdisabled(?:=""|(?=\s|>))/)
})
