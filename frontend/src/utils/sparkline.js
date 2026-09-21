export function sparklineGeometry(values, { width = 76, height = 28, padding = 3, maxPoints = 12 } = {}) {
  const source = Array.isArray(values) ? values.slice(-Math.max(1, maxPoints)) : []
  const samples = source.map((value) => {
    if (value === null || value === undefined || value === '') return null
    const number = Number(value)
    return Number.isFinite(number) ? number : null
  })
  const valid = samples.filter((value) => value !== null)
  if (!valid.length) return { path: '', lastPoint: null, count: 0, direction: 'flat' }

  const min = Math.min(...valid)
  const max = Math.max(...valid)
  const spread = max - min
  const drawableWidth = Math.max(1, width - padding * 2)
  const drawableHeight = Math.max(1, height - padding * 2)
  const denominator = Math.max(1, samples.length - 1)
  const points = samples.map((value, index) => {
    if (value === null) return null
    const x = padding + (index / denominator) * drawableWidth
    const y = spread === 0
      ? height / 2
      : padding + ((max - value) / spread) * drawableHeight
    return { x, y, value }
  })
  let path = ''
  let drawing = false
  for (const point of points) {
    if (!point) {
      drawing = false
      continue
    }
    path += `${drawing ? ' L' : 'M'}${point.x.toFixed(2)} ${point.y.toFixed(2)}`
    drawing = true
  }
  const firstPoint = points.find(Boolean)
  const lastPoint = [...points].reverse().find(Boolean)
  const direction = lastPoint.value > firstPoint.value
    ? 'up'
    : lastPoint.value < firstPoint.value ? 'down' : 'flat'
  return { path, lastPoint, count: valid.length, direction }
}

