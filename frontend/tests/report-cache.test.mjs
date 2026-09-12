import assert from 'node:assert/strict'
import test from 'node:test'
import { createReportCache } from '../src/api/reportCache.js'
import { recentTwelveMonthStart, reportToday } from '../src/api/reportDates.js'

function deferred() {
  let resolve
  const promise = new Promise((done) => { resolve = done })
  return { promise, resolve }
}

test('report date is stable in Shanghai, including month rollover', () => {
  assert.equal(reportToday(new Date('2026-08-31T16:00:01Z')), '2026-09-01')
  assert.equal(reportToday(new Date('2026-08-31T15:59:59Z')), '2026-08-31')
})

test('recent twelve-month window includes the selected month and previous eleven months', () => {
  assert.equal(recentTwelveMonthStart('2026-09-11'), '2025-10-01')
  assert.equal(recentTwelveMonthStart('2026-01-31'), '2025-02-01')
})

test('shares inflight requests and cached data, separates keys and expires results', async () => {
  let now = 0
  let calls = 0
  const cache = createReportCache({ now: () => now })
  const data = deferred()
  const load = () => { calls++; return data.promise }
  const a = cache.read('a', load, { ttl: 10 })
  const b = cache.read('a', load)
  data.resolve({ count: 7 })
  assert.equal(await a, await b)
  assert.equal(calls, 1)
  assert.equal(await cache.read('a', load), await a)
  assert.equal(calls, 1)
  await cache.read('b', load)
  now = 11
  await cache.read('a', load)
  assert.equal(calls, 3)
})

test('navigation abort cancels only that subscriber, preserving login prefetch', async () => {
  const cache = createReportCache()
  const data = deferred()
  const controller = new AbortController()
  let signal
  const preload = cache.read('report', (s) => { signal = s; return data.promise })
  const page = cache.read('report', () => assert.fail('duplicate'), { signal: controller.signal })
  const rejected = assert.rejects(page, { name: 'AbortError' })
  controller.abort()
  await rejected
  assert.equal(signal.aborted, false)
  data.resolve(42)
  assert.equal(await preload, 42)
  assert.equal(await cache.read('report', () => assert.fail('cache missed')), 42)
})

test('force replaces an old preload and coalesces concurrent forced refreshes', async () => {
  const cache = createReportCache()
  const stale = deferred()
  const fresh = deferred()
  let oldSignal
  const a = cache.read('report', (signal) => { oldSignal = signal; return stale.promise })
  const rejected = assert.rejects(a, { name: 'AbortError' })
  await Promise.resolve()
  const b = cache.read('report', () => fresh.promise, { force: true })
  const c = cache.read('report', () => assert.fail('duplicate force'), { force: true })
  assert.equal(oldSignal.aborted, true)
  fresh.resolve('new')
  assert.equal(await b, 'new')
  assert.equal(await c, 'new')
  stale.resolve('old')
  await rejected
  assert.equal(await cache.read('report', () => assert.fail('stale cache')), 'new')
})

test('logout clears cached results and a late response cannot restore another account data', async () => {
  const cache = createReportCache()
  const stale = deferred()
  const a = cache.read('report', () => stale.promise)
  const rejected = assert.rejects(a, { name: 'AbortError' })
  await Promise.resolve()
  cache.clear()
  assert.equal(await cache.read('report', () => 'new user'), 'new user')
  stale.resolve('old user')
  await rejected
  assert.equal(await cache.read('report', () => assert.fail('late data cached')), 'new user')
})

test('failed requests can be retried and cache size is bounded', async () => {
  const cache = createReportCache({ limit: 2 })
  await assert.rejects(cache.read('a', () => { throw new Error('offline') }), /offline/)
  assert.equal(await cache.read('a', () => 1), 1)
  await cache.read('b', () => 2)
  await cache.read('c', () => 3)
  assert.equal(await cache.read('a', () => 4), 4)
})
