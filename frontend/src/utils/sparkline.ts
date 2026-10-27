export function sparklineGeometry(values: unknown, { width = 76, height = 28, padding = 3, maxPoints = 12 } = {}) {
  const source = Array.isArray(values) ? values.slice(-Math.max(1, maxPoints)) : []
  const samples = source.map((value) => {
    if (value === null || value === undefined || value === '') return null
    const number = Number(value)
    return Number.isFinite(number) ? number : null
  })
  const valid = samples.filter((value) => value !== null)
  if (!valid.length) return { path: '', lastPoint: null, bars: [], count: 0, direction: 'flat' }

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
  const firstPoint = points.find((point) => point !== null)!
  const lastPoint = [...points].reverse().find((point) => point !== null)!
  const direction = lastPoint.value > firstPoint.value
    ? 'up'
    : lastPoint.value < firstPoint.value ? 'down' : 'flat'
  const barStep = drawableWidth / samples.length
  const bars = samples.flatMap((value, index) => {
    if (value === null) return []
    const barHeight = Math.max(0, value) / (max > 0 ? max : 1) * drawableHeight
    return [{ x: padding + index * barStep, y: height - padding - barHeight, width: barStep * 0.65, height: barHeight }]
  })
  return { path, lastPoint, bars, count: valid.length, direction }
}

