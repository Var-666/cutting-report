const DATE_PARTS = /^(\d{4})-(\d{1,2})(?:-(\d{1,2}))?/
const TIME_PARTS = /[T\s](\d{1,2}):(\d{1,2})(?::(\d{1,2}))?/

function parseDateParts(value: unknown) {
  const match = String(value || '').match(DATE_PARTS)
  if (!match) return null
  return { year: match[1], month: Number(match[2]), day: match[3] ? Number(match[3]) : null }
}

export function formatChineseMonth(value: unknown, fallback = '--') {
  const parts = parseDateParts(value)
  return parts ? `${parts.year}年${String(parts.month).padStart(2, '0')}月` : fallback
}

export function formatChineseDate(value: unknown, fallback = '--') {
  const parts = parseDateParts(value)
  if (!parts) return fallback
  if (parts.day == null) return formatChineseMonth(value, fallback)
  return `${parts.year}年${String(parts.month).padStart(2, '0')}月${String(parts.day).padStart(2, '0')}日`
}

export function formatChineseDateTime(value: unknown, fallback = '--', timeZone?: string) {
  if (value instanceof Date) {
    if (!Number.isFinite(value.getTime())) return fallback
    value = value.toISOString()
  }
  if (timeZone && /(?:Z|[+-]\d{2}:?\d{2})$/i.test(String(value))) {
    const instant = new Date(String(value))
    if (!Number.isFinite(instant.getTime())) return fallback
    const parts = new Intl.DateTimeFormat('en-CA', {
      timeZone, year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
    }).formatToParts(instant)
    const part = (type: Intl.DateTimeFormatPartTypes) => parts.find(item => item.type === type)?.value
    return `${part('year')}年${part('month')}月${part('day')}日 ${part('hour')}:${part('minute')}:${part('second')}`
  }
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
    String(left.report_date || left.period_start || '')
      .localeCompare(String(right.report_date || right.period_start || '')) * factor
  ))
}
