import assert from 'node:assert/strict'
import { test } from 'node:test'
import { canChooseSaveLocation, saveFileFromUrl } from '../src/api/saveFile.js'

function environment() {
  const calls = []
  const blob = new Blob(['GEN test'])
  const writable = {
    write: async (data) => { assert.equal(data, blob); calls.push('write') },
    close: async () => { calls.push('close') },
    abort: async () => { calls.push('abort') },
  }
  const anchor = { click: () => calls.push('click'), remove: () => calls.push('remove') }
  const env = {
    isSecureContext: true,
    showSaveFilePicker: (options) => {
      calls.push(['picker', options.suggestedName])
      return Promise.resolve({ name: 'chosen.gen', createWritable: async () => { calls.push('open'); return writable } })
    },
    fetch: async () => { calls.push('fetch'); return { ok: true, blob: async () => blob } },
    URL: { createObjectURL: () => 'blob:test', revokeObjectURL: () => calls.push('revoke') },
    document: { createElement: () => anchor, body: { appendChild: () => calls.push('append') } },
    setTimeout: (callback) => callback(),
  }
  return { env, calls, writable, anchor }
}

test('picker opens synchronously before fetching and preserves suggested filename', async () => {
  const { env, calls } = environment()
  const pending = saveFileFromUrl('/file', '原文件.GEN', {}, env)
  assert.deepEqual(calls, [['picker', '原文件.GEN']])
  assert.deepEqual(await pending, { mode: 'picker', filename: 'chosen.gen' })
  assert.deepEqual(calls.slice(1), ['fetch', 'open', 'write', 'close'])
})

test('cancel does not fetch or create a destination; permission failure is not silent fallback', async () => {
  const { env, calls } = environment()
  env.showSaveFilePicker = async () => { throw new DOMException('cancelled', 'AbortError') }
  assert.deepEqual(await saveFileFromUrl('/file', 'a.pdf', {}, env), { mode: 'cancelled' })
  assert.deepEqual(calls, [])
  env.showSaveFilePicker = async () => { throw new DOMException('denied', 'SecurityError') }
  await assert.rejects(saveFileFromUrl('/file', 'a.pdf', {}, env), /无法打开保存窗口/)
  assert.deepEqual(calls, [])
})

test('network or file read failures cannot overwrite a destination', async () => {
  const { env, calls } = environment()
  env.fetch = async () => ({ ok: false, json: async () => ({ detail: '文件已失效' }) })
  await assert.rejects(saveFileFromUrl('/file', 'a.gen', {}, env), /文件已失效/)
  env.fetch = async () => ({ ok: true, blob: async () => { throw new Error('断线') } })
  await assert.rejects(saveFileFromUrl('/file', 'a.gen', {}, env), /断线/)
  assert.ok(!calls.includes('open'))
})

test('a failed write is aborted without committing partial file or starting another download', async () => {
  const { env, calls, writable } = environment()
  writable.write = async () => { throw new Error('disk full') }
  await assert.rejects(saveFileFromUrl('/file', 'a.gen', {}, env), /文件未保存成功/)
  assert.ok(calls.includes('abort'))
  assert.ok(!calls.includes('close'))
  assert.ok(!calls.includes('click'))
})

test('HTTP and unsupported browsers require explicit fallback; browser keeps original name', async () => {
  const { env, calls, anchor } = environment()
  env.isSecureContext = false
  assert.equal(canChooseSaveLocation(env), false)
  await assert.rejects(saveFileFromUrl('/file', 'xxx.pdf', {}, env), /当前浏览器不支持/)
  assert.deepEqual(calls, [])
  assert.deepEqual(await saveFileFromUrl('/file', 'xxx.pdf', { browserDownload: true }, env), { mode: 'browser', filename: 'xxx.pdf' })
  assert.equal(anchor.download, 'xxx.pdf')
  assert.deepEqual(calls, ['fetch', 'append', 'click', 'remove', 'revoke'])
  env.isSecureContext = true
  delete env.showSaveFilePicker
  assert.equal(canChooseSaveLocation(env), false)
})
