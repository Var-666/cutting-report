export function reportToday(stamp = new Date()) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(stamp).map(({ type, value }) => [type, value]))
  return `${parts.year}-${parts.month}-${parts.day}`
}

export function recentTwelveMonthStart(reportDate) {
  const [year, month] = String(reportDate).split('-').map(Number)
  const monthIndex = year * 12 + month - 1 - 11
  const startYear = Math.floor(monthIndex / 12)
  const startMonth = monthIndex - startYear * 12 + 1
  return `${startYear}-${String(startMonth).padStart(2, '0')}-01`
}

export function taskProductionDate(row, fallback = '') {
  return String(row?.production_date || row?.production_time || fallback).slice(0, 10)
}
