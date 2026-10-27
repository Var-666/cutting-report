const DATE_PARTS = /^(\d{4})-(\d{1,2})(?:-(\d{1,2}))?/
const TIME_PARTS = /[T\s](\d{1,2}):(\d{1,2})(?::(\d{1,2}))?/

function parseDateParts(value: unknown) {
  const match = String(value || '').match(DATE_PARTS)
  if (!match) return null
  return { year: match[1], month: Number(match[2]), day: match[3] ? Number(match[3]) : null }
}

export function formatChineseMonth(value: unknown, fallback = '--') {
  const parts = parseDateParts(value)
  return parts ? `${parts.year}年${parts.month}月` : fallback
}

export function formatChineseDate(value: unknown, fallback = '--') {
  const parts = parseDateParts(value)
  if (!parts) return fallback
  if (parts.day == null) return `${parts.year}年${parts.month}月`
  return `${parts.year}年${parts.month}月${parts.day}日`
}

export function formatChineseDateTime(value: unknown, fallback = '--') {
  const date = formatChineseDate(value, '')
  if (!date) return fallback
  const match = String(value).match(TIME_PARTS)
  if (!match) return date
  const time = [match[1], match[2], match[3] || '00']
    .map((part) => String(part).padStart(2, '0'))
    .join(':')
  return `${date} ${time}`
}

export function formatTableDateValue(value: unknown, fallback = '--') {
  if (value === null || value === undefined || value === '') return fallback
  const text = String(value)
  if (!DATE_PARTS.test(text)) return value
  if (TIME_PARTS.test(text)) return formatChineseDateTime(text, fallback)
  if (/^\d{4}-\d{1,2}$/.test(text)) return formatChineseMonth(text, fallback)
  return formatChineseDate(text, fallback)
}

export function sortReportPeriods<T extends { report_date?: string | null; period_start?: string | null }>(rows: T[], direction = 'asc') {
  const factor = direction === 'desc' ? -1 : 1
  return [...rows].sort((left, right) => (
    String(left?.report_date || left?.period_start || '')
      .localeCompare(String(right?.report_date || right?.period_start || '')) * factor
  ))
}
