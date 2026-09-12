function numeric(value) {
  const number = Number(value)
  return Number.isFinite(number) ? number : 0
}

function aggregateChannel(rows, name) {
  let total = 0
  let hasValue = false
  let validCount = 0
  let completeCount = 0
  let expectedCount = 0
  let estimatedCount = 0

  for (const row of rows) {
    const channel = row?.[name] || {}
    if (channel.value !== null && channel.value !== undefined && Number.isFinite(Number(channel.value))) {
      total += Number(channel.value)
      hasValue = true
    }
    validCount += numeric(channel.valid_count)
    completeCount += numeric(channel.complete_count)
    expectedCount += numeric(channel.expected_count)
    estimatedCount += numeric(channel.estimated_count)
  }

  const status = !hasValue
    ? 'missing'
    : expectedCount > 0 && validCount < expectedCount
      ? 'partial'
      : 'measured'
  return {
    value: hasValue ? total : null,
    status,
    valid_count: validCount,
    complete_count: completeCount,
    expected_count: expectedCount,
    estimated_count: estimatedCount,
  }
}

export function aggregateEnergyRowsByMonth(rows = []) {
  const groups = new Map()
  for (const row of rows) {
    const reportMonth = String(row?.report_date || '').slice(0, 7)
    if (!/^\d{4}-\d{2}$/.test(reportMonth)) continue
    if (!groups.has(reportMonth)) groups.set(reportMonth, [])
    groups.get(reportMonth).push(row)
  }

  return [...groups.entries()].map(([reportMonth, monthRows]) => ({
    report_date: reportMonth,
    task_count: monthRows.reduce((total, row) => total + numeric(row.task_count), 0),
    work_time_seconds: monthRows.reduce((total, row) => total + numeric(row.work_time_seconds), 0),
    electricity: aggregateChannel(monthRows, 'electricity'),
    gas: aggregateChannel(monthRows, 'gas'),
  }))
}
