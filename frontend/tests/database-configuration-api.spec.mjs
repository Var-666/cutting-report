import assert from 'node:assert/strict'
import { test } from 'vitest'

for (const method of ['saveDatabaseConfiguration', 'saveWeldingConfiguration', 'saveDatabaseSource', 'setDatabaseSourceEnabled', 'deleteDatabaseSource']) {
  test(`successful ${method} clears report reads and a failed save preserves them`, async () => {
    const { reportCache } = await import('../src/api/reportCache.ts')
    const configuration = await import('../src/api/configuration.ts')
    const mutate = () => method === 'saveDatabaseSource' ? configuration[method]({ id: 'cutting-production' }, false)
      : method === 'setDatabaseSourceEnabled' ? configuration[method]('cutting-production', true)
      : method === 'deleteDatabaseSource' ? configuration[method]('quality') : configuration[method]({})
    const originalFetch = globalThis.fetch
    try {
      await reportCache.read('database-runtime-test', () => 'old')
      globalThis.fetch = async () => Response.json({ detail: '保存失败' }, { status: 500 })
      await assert.rejects(mutate(), /保存失败/)
      assert.equal(await reportCache.read('database-runtime-test', () => 'unexpected'), 'old')
      globalThis.fetch = async () => Response.json({ success: true, data: { restart_required: false } })
      await mutate()
      assert.equal(await reportCache.read('database-runtime-test', () => 'new'), 'new')
    } finally { globalThis.fetch = originalFetch; reportCache.clear() }
  })
}
