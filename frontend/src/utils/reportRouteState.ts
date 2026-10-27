import type { LocationQuery } from 'vue-router'
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/

export function queryText(value: unknown) {
  if (Array.isArray(value)) return String(value[0] || '')
  return typeof value === 'string' ? value : ''
}

export function queryDate(value: unknown) {
  const text = queryText(value)
  if (!ISO_DATE.test(text)) return ''
  const date = new Date(`${text}T00:00:00Z`)
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === text ? text : ''
}

export function queryDateRange(query: LocationQuery, { maxDays = 366 } = {}) {
  const start = queryDate(query.start)
  const end = queryDate(query.end)
  if (!start || !end || end < start) return null
  const days = Math.floor((Date.parse(`${end}T00:00:00Z`) - Date.parse(`${start}T00:00:00Z`)) / 86_400_000) + 1
  return days <= maxDays ? { start, end } : null
}

export function queryChoice<T extends string>(value: unknown, choices: readonly T[], fallback: T): T {
  const text = queryText(value)
  return choices.includes(text as T) ? text as T : fallback
}

export function queryPositiveInteger(value: unknown, fallback = 1) {
  const text = queryText(value)
  if (!/^\d+$/.test(text)) return fallback
  const number = Number(text)
  return Number.isSafeInteger(number) && number > 0 ? number : fallback
}

export function mergeOwnedQuery(current: LocationQuery, ownedKeys: string[], values: Record<string, unknown>) {
  const query = { ...current }
  for (const key of ownedKeys) delete query[key]
  for (const [key, value] of Object.entries(values)) {
    if (value !== '' && value !== null && value !== undefined) query[key] = String(value)
  }
  return query
}

export function sameRouteQuery(left: LocationQuery, right: LocationQuery) {
  const keys = [...new Set([...Object.keys(left), ...Object.keys(right)])].sort()
  return keys.every((key) => JSON.stringify(left[key] ?? null) === JSON.stringify(right[key] ?? null))
}
