import assert from 'node:assert/strict'
import { test } from 'node:test'
import { sparklineGeometry } from '../src/utils/sparkline.js'

test('sparkline geometry is derived from values and keeps the last point', () => {
  const result = sparklineGeometry([10, 15, 12, 20])
  assert.equal(result.count, 4)
  assert.equal(result.direction, 'up')
  assert.match(result.path, /^M/)
  assert.equal(result.lastPoint.value, 20)
})

test('sparkline handles constant, missing, and invalid values without inventing a trend', () => {
  const flat = sparklineGeometry([4, 4, null, 4])
  assert.equal(flat.direction, 'flat')
  assert.match(flat.path, /M.*M/)
  const empty = sparklineGeometry([null, undefined, 'invalid'])
  assert.deepEqual(empty, { path: '', lastPoint: null, count: 0, direction: 'flat' })
})

test('sparkline limits large series to the most recent samples', () => {
  const result = sparklineGeometry([1, 2, 3, 4, 5], { maxPoints: 3 })
  assert.equal(result.count, 3)
  assert.equal(result.lastPoint.value, 5)
})
