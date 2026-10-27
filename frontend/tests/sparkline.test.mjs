import assert from 'node:assert/strict'
import { test } from 'node:test'
import { sparklineGeometry } from '../src/utils/sparkline.ts'

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
  assert.deepEqual(empty, { path: '', lastPoint: null, bars: [], count: 0, direction: 'flat' })
})

test('sparkline limits large series to the most recent samples', () => {
  const result = sparklineGeometry([1, 2, 3, 4, 5], { maxPoints: 3 })
  assert.equal(result.count, 3)
  assert.equal(result.lastPoint.value, 5)
})

test('sparkline bars use a zero baseline and keep missing days as gaps', () => {
  const { bars } = sparklineGeometry([0, 10, null, 20])
  assert.equal(bars.length, 3)
  assert.deepEqual(bars.map(bar => bar.height), [0, 11, 22])
  assert.deepEqual(bars.map(bar => bar.x), [3, 20.5, 55.5])
  assert.ok(bars.every(bar => bar.y + bar.height === 25 && bar.width > 0))
  assert.equal(sparklineGeometry([0, 0]).bars[0].height, 0)
})

test('bar heights follow rising, falling, flat and fractional input values', () => {
  const heights = values => sparklineGeometry(values).bars.map(bar => bar.height)
  assert.deepEqual(heights([2, 10, 4]), [4.4, 22, 8.8])
  assert.deepEqual(heights([4, 4, 4]), [22, 22, 22])
  assert.deepEqual(heights([0.1, 0.2]), [11, 22])
  assert.notDeepEqual(heights([2, 10, 4]), heights([10, 2, 4]))
})
