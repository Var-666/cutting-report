import assert from 'node:assert/strict'
import test from 'node:test'

import { createRequestCancellation } from '../src/api/requestCancellation.ts'

test('abort notifies the backend once using the same request id', async () => {
  const calls = []
  const controller = new AbortController()
  const cancellation = createRequestCancellation(controller.signal, {
    createId: () => 'fixed-id',
    fetchImpl: (...args) => {
      calls.push(args)
      return Promise.resolve({ ok: true })
    },
  })

  assert.deepEqual(cancellation.headers, { 'X-Request-ID': 'web_fixed-id' })
  controller.abort()
  controller.abort()
  await Promise.resolve()

  assert.equal(calls.length, 1)
  assert.equal(calls[0][0], '/api/v1/requests/web_fixed-id/cancel')
  assert.equal(calls[0][1].method, 'POST')
  assert.equal(calls[0][1].keepalive, true)
  assert.equal(calls[0][1].credentials, 'same-origin')
})

test('disposing a completed request removes the abort notification', () => {
  let calls = 0
  const controller = new AbortController()
  const cancellation = createRequestCancellation(controller.signal, {
    createId: () => 'completed',
    fetchImpl: () => {
      calls += 1
      return Promise.resolve({ ok: true })
    },
  })

  cancellation.dispose()
  controller.abort()

  assert.equal(calls, 0)
})
