import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'
import { searchCuttingFiles, clearCuttingFilePages } from '../src/api/reports.js'
import { stopReportPreload } from '../src/state/reportPreload.js'

const originalFetch = globalThis.fetch
const filters = { logic: 'and', rules: [{ field: 'document_no', operator: 'contains', value: '.GEN' }] }
function mockApi() {
  const calls = []
  globalThis.fetch = async (_, options) => {
    const body = JSON.parse(options.body)
    calls.push(body)
    return { ok: true, json: async () => ({ success: true, data: { items: [body.page], total: 50 } }) }
  }
  return calls
}
afterEach(() => { clearCuttingFilePages(); globalThis.fetch = originalFetch })

test('repeated and prefetched pages share requests; page and sort are cache keys', async () => {
  const calls = mockApi()
  await searchCuttingFiles(filters, { page: 1, force: true })
  await Promise.all([searchCuttingFiles(filters, { page: 2 }), searchCuttingFiles(filters, { page: 2 })])
  await searchCuttingFiles(filters, { page: 1 })
  await searchCuttingFiles(filters, { page: 2, sort: [{ field: 'document_no', direction: 'desc' }] })
  assert.equal(calls.length, 3)
  assert.deepEqual(calls.map((call) => call.page), [1, 2, 2])
  assert.deepEqual(calls[2].sort, [{ field: 'document_no', direction: 'desc' }])
})

test('new conditions, manual refresh and logout do not reuse stale file lists', async () => {
  const calls = mockApi()
  await searchCuttingFiles(filters)
  await searchCuttingFiles(filters, { page: 2 })
  await searchCuttingFiles(filters, { force: true })
  await searchCuttingFiles(filters, { page: 2 })
  await searchCuttingFiles({ logic: 'and', rules: [{ field: 'document_no', operator: 'contains', value: '.pdf' }] })
  stopReportPreload()
  await searchCuttingFiles(filters)
  assert.equal(calls.length, 6)
})

test('a failed background page can be retried when opened', async () => {
  let failed = true
  globalThis.fetch = async () => {
    if (failed) throw new Error('query timeout')
    return { ok: true, json: async () => ({ success: true, data: { items: ['retry'], total: 1 } }) }
  }
  await assert.rejects(searchCuttingFiles(filters, { page: 2 }), /query timeout/)
  failed = false
  assert.deepEqual((await searchCuttingFiles(filters, { page: 2 })).items, ['retry'])
})
