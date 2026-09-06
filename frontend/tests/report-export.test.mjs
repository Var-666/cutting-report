import assert from 'node:assert/strict'
import { test } from 'node:test'
import { downloadReportPdf, waitForReport } from '../src/api/reportExport.js'

test('PDF download preserves the Chinese filename and uses an authenticated request', async (t) => {
  let clicked = false
  const link = { click() { clicked = true }, remove() {} }
  const previousDocument = globalThis.document
  globalThis.document = { createElement: () => link, body: { appendChild() {} } }
  t.after(() => { globalThis.document = previousDocument })
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(url, '/api/v1/reports/rpt_test/download?format=pdf')
    assert.equal(options.credentials, 'same-origin')
    return new Response(new Blob(['%PDF-1.7']), { headers: {
      'Content-Type': 'application/pdf',
      'Content-Disposition': `attachment; filename*=utf-8''${encodeURIComponent('领导月报_2026-08.pdf')}`,
    } })
  })
  t.mock.method(globalThis, 'setTimeout', (callback) => { callback(); return 0 })
  await downloadReportPdf('rpt_test')
  assert.equal(link.download, '领导月报_2026-08.pdf')
  assert.equal(clicked, true)
})

test('PDF server failures and wrong content types are reported instead of downloaded', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => Response.json({ detail: '浏览器未安装' }, { status: 503 }))
  await assert.rejects(downloadReportPdf('rpt_test'), /浏览器未安装/)
  globalThis.fetch.mock.mockImplementation(async () => new Response('<html>登录</html>', { headers: { 'Content-Type': 'text/html' } }))
  await assert.rejects(downloadReportPdf('rpt_test'), /未返回 PDF/)
})

test('report polling accepts completion with warnings and propagates generation failures', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => Response.json({ success: true, data: { status: 'completed_with_warnings' } }))
  assert.equal((await waitForReport('rpt_test')).status, 'completed_with_warnings')
  globalThis.fetch.mock.mockImplementation(async () => Response.json({ success: true, data: { status: 'failed', error: { message: '缺少车间日报' } } }))
  await assert.rejects(waitForReport('rpt_test'), /缺少车间日报/)
})

test('cancelled or timed-out polling does not keep fetching', async (t) => {
  const fetchMock = t.mock.method(globalThis, 'fetch', async () => { throw new Error('must not fetch') })
  const controller = new AbortController()
  controller.abort()
  await assert.rejects(waitForReport('rpt_test', { signal: controller.signal }), { name: 'AbortError' })
  await assert.rejects(waitForReport('rpt_test', { timeoutMs: 0 }), /生成与历史/)
  assert.equal(fetchMock.mock.callCount(), 0)
})
