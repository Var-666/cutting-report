import assert from 'node:assert/strict'
import { afterEach, beforeEach, test, vi } from 'vitest'
import { setDeviceCatalog, resetDeviceContext } from '../src/api/deviceContext.ts'
import { downloadReportPdf, waitForReport } from '../src/api/reportExport.ts'
import { downloadWelding } from '../src/api/welding.ts'

beforeEach(() => setDeviceCatalog({ default_device_id: 'Cutter01', items: [{ id: 'Cutter01' }] }))
afterEach(resetDeviceContext)

test('PDF download preserves the Chinese filename and uses an authenticated request', async (t) => {
  let clicked = false
  const link = { click() { clicked = true }, remove() {} }
  const previousDocument = globalThis.document
  globalThis.document = { createElement: () => link, body: { appendChild() {} } }
  t.onTestFinished(() => { globalThis.document = previousDocument })
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (url, options) => {
    assert.equal(url, '/api/v1/reports/rpt_test/download?format=pdf&device_id=Cutter01')
    assert.equal(options.credentials, 'same-origin')
    return new Response(new Blob(['%PDF-1.7']), { headers: {
      'Content-Type': 'application/pdf',
      'Content-Disposition': `attachment; filename*=utf-8''${encodeURIComponent('领导月报_2026-08.pdf')}`,
    } })
  })
  vi.spyOn(globalThis, 'setTimeout').mockImplementation((callback) => { callback(); return 0 })
  await downloadReportPdf('rpt_test')
  assert.equal(link.download, '领导月报_2026-08.pdf')
  assert.equal(clicked, true)
})

test('PDF server failures and wrong content types are reported instead of downloaded', async (t) => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => Response.json({ detail: '浏览器未安装' }, { status: 503 }))
  await assert.rejects(downloadReportPdf('rpt_test'), /浏览器未安装/)
  fetch.mockImplementation(async () => new Response('<html>登录</html>', { headers: { 'Content-Type': 'text/html' } }))
  await assert.rejects(downloadReportPdf('rpt_test'), /未返回 PDF/)
})

test('report polling accepts completion and propagates generation failures', async (t) => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => Response.json({ success: true, data: { status: 'completed' } }))
  assert.equal((await waitForReport('rpt_test')).status, 'completed')
  fetch.mockImplementation(async () => Response.json({ success: true, data: { status: 'failed', error: { message: '缺少车间日报' } } }))
  await assert.rejects(waitForReport('rpt_test'), /缺少车间日报/)
})

test('cancelled or timed-out polling does not keep fetching', async (t) => {
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => { throw new Error('must not fetch') })
  const controller = new AbortController()
  controller.abort()
  await assert.rejects(waitForReport('rpt_test', { signal: controller.signal }), { name: 'AbortError' })
  await assert.rejects(waitForReport('rpt_test', { timeoutMs: 0 }), /生成与历史/)
  assert.equal(fetchMock.mock.calls.length, 0)
})

function legacyController() {
  const controller = new AbortController()
  Object.defineProperty(controller.signal, 'throwIfAborted', { value: undefined })
  return controller
}

function downloadEnvironment(t) {
  const links = [], blobs = [], revoked = [], timers = []
  const previousDocument = globalThis.document
  globalThis.document = { createElement() { const link = { clicked: false, removed: false, click() { this.clicked = true }, remove() { this.removed = true } }; links.push(link); return link }, body: { appendChild() {} } }
  t.onTestFinished(() => { globalThis.document = previousDocument })
  vi.spyOn(URL, 'createObjectURL').mockImplementation(blob => { blobs.push(blob); return `blob:download-${blobs.length}` })
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(url => revoked.push(url))
  vi.spyOn(globalThis, 'setTimeout').mockImplementation((callback, delay) => { timers.push({ callback, delay }); return timers.length })
  return { links, blobs, revoked, timers }
}

for (const [format, mime] of [['pdf', 'application/pdf'], ['csv', 'text/csv'], ['html', 'text/html']]) {
  test(`legacy welding ${format} download works and releases its Blob URL`, async t => {
    const env = downloadEnvironment(t)
    const controller = legacyController()
    setDeviceCatalog({ default_device_id: 'welding', items: [{ id: 'welding', type: 'welding' }] })
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, options) => {
      assert.equal(options.signal, controller.signal)
      assert.equal(options.credentials, 'same-origin')
      assert.equal(options.headers['X-Production-Line'], 'welding')
      return new Response('fixture', { headers: { 'Content-Type': mime } })
    })
    await downloadWelding(`/api/v1/welding/export?format=${format}`, `report.${format}`, controller.signal)
    assert.equal(env.links[0].download, `report.${format}`)
    assert.equal(env.links[0].clicked, true)
    assert.equal(env.links[0].removed, true)
    assert.equal(env.blobs[0].type, mime)
    assert.equal(env.timers[0].delay, 60000)
    env.timers[0].callback()
    assert.deepEqual(env.revoked, ['blob:download-1'])
  })
}

test('legacy cutting PDF download and completed polling retain cancellation checks', async t => {
  const env = downloadEnvironment(t)
  const controller = legacyController()
  vi.spyOn(globalThis, 'fetch').mockImplementation(async url => url.includes('/status')
    ? Response.json({ success: true, data: { status: 'completed' } })
    : new Response('%PDF', { headers: { 'Content-Type': 'application/pdf' } }))
  assert.equal((await waitForReport('legacy', { signal: controller.signal })).status, 'completed')
  await downloadReportPdf('legacy', { signal: controller.signal })
  assert.equal(env.links[0].clicked, true)
  env.timers[0].callback()
  assert.deepEqual(env.revoked, ['blob:download-1'])
})

test('already cancelled legacy downloads and polling perform no requests or downloads', async t => {
  const env = downloadEnvironment(t)
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => { throw new Error('must not fetch') })
  const controller = legacyController()
  controller.abort(new Error('custom abort reason'))
  for (const run of [() => downloadReportPdf('cancelled', { signal: controller.signal }), () => downloadWelding('/api/v1/welding/export', 'report.csv', controller.signal), () => waitForReport('cancelled', { signal: controller.signal })]) {
    await assert.rejects(run(), { name: 'AbortError' })
  }
  assert.equal(fetchMock.mock.calls.length, 0)
  assert.equal(env.links.length, 0)
  assert.equal(env.blobs.length, 0)
})

for (const kind of ['cutting', 'welding']) {
  for (const phase of ['response', 'blob']) {
    test(`legacy ${kind} cancellation during ${phase} prevents a late download`, async t => {
      const env = downloadEnvironment(t)
      const controller = legacyController()
      let finish
      const held = new Promise(resolve => { finish = resolve })
      const response = { ok: true, headers: new Headers({ 'Content-Type': 'application/pdf' }), blob: () => phase === 'blob' ? held : Promise.resolve(new Blob(['%PDF'], { type: 'application/pdf' })) }
      vi.spyOn(globalThis, 'fetch').mockImplementation(async url => url.endsWith('/cancel') ? Response.json({ success: true }) : phase === 'response' ? held : response)
      const pending = kind === 'cutting' ? downloadReportPdf('cancelled', { signal: controller.signal }) : downloadWelding('/api/v1/welding/export', 'report.pdf', controller.signal)
      await new Promise(resolve => setImmediate(resolve))
      controller.abort()
      finish(phase === 'response' ? response : new Blob(['%PDF'], { type: 'application/pdf' }))
      await assert.rejects(pending, { name: 'AbortError' })
      assert.equal(env.blobs.length, 0)
      assert.equal(env.links.length, 0)
    })
  }
}

test('legacy welding downloads still reject unexpected file formats', async t => {
  const env = downloadEnvironment(t)
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () => Response.json({ success: true }))
  for (const format of ['csv', 'html', 'pdf']) await assert.rejects(downloadWelding('/api/v1/welding/export', `report.${format}`, legacyController().signal), /文件格式不正确/)
  assert.equal(env.blobs.length, 0)
})
