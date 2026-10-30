import type { Ref } from 'vue'
import type { LeadershipPeriodRecord, LeadershipReport } from '../types/reports'
import { computed } from 'vue'

export function useLeadershipReportData({ report, monthlyReport, appliedPeriodStart, appliedPeriodEnd, defaultMonthlyView }: {
  report: Ref<LeadershipReport | null>
  monthlyReport: Ref<LeadershipReport | null>
  appliedPeriodStart: Ref<string>
  appliedPeriodEnd: Ref<string>
  defaultMonthlyView: Ref<boolean>
}) {
  const dailyRecords = computed(() => report.value?.daily_records || [])
  const dailyChartRecords = computed<LeadershipPeriodRecord[]>(() => {
    if (!report.value) return []
    const byDate = new Map(dailyRecords.value.map(row => [row.period_start, row]))
    const rows: LeadershipPeriodRecord[] = []
    const end = Date.parse(`${appliedPeriodEnd.value}T00:00:00Z`)
    for (let day = Date.parse(`${appliedPeriodStart.value}T00:00:00Z`); day <= end; day += 86_400_000) {
      const date = new Date(day).toISOString().slice(0, 10)
      rows.push(byDate.get(date) || { period_start: date, task_count: 0, cut_length_m: 0, groove_length_m: 0, work_time_seconds: 0 })
    }
    return rows
  })
  const metricSeries = computed(() => {
    const rows = [...dailyRecords.value].sort((left, right) => left.period_start.localeCompare(right.period_start))
    return {
      taskCount: rows.map((row) => row.task_count),
      cutLength: rows.map((row) => row.cut_length_m),
      grooveLength: rows.map((row) => row.groove_length_m),
      workTime: rows.map((row) => row.work_time_seconds),
    }
  })
  const monthlyRecords = computed(() => monthlyReport.value?.monthly_records || [])
  const total = computed(() => report.value?.current_month_total || {
    task_count: null,
    cut_length_m: null,
    groove_length_m: null,
    work_time_seconds: null,
  })
  const monthlyTotal = computed(() => monthlyReport.value?.twelve_month_total || {
    task_count: null,
    cut_length_m: null,
    groove_length_m: null,
    work_time_seconds: null,
  })
  const comparisonTotal = computed(() => report.value?.comparison_total || null)

  function roundNumber(value: unknown, digits = 1) {
    const factor = 10 ** digits
    return Math.round((Number(value || 0) + Number.EPSILON) * factor) / factor
  }

  function formatLength(value: unknown) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return '--'
    return new Intl.NumberFormat('zh-CN', {
      minimumFractionDigits: 1,
      maximumFractionDigits: 1,
    }).format(Number(value || 0))
  }

  function formatDuration(value: unknown) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return '--'
    const totalSeconds = Math.max(0, Math.round(Number(value || 0)))
    const hours = Math.floor(totalSeconds / 3600)
    const minutes = Math.floor((totalSeconds % 3600) / 60)
    const seconds = totalSeconds % 60
    return [hours, minutes, seconds].map((part) => String(part).padStart(2, '0')).join(':')
  }

  function changeRate(current: unknown, previous: unknown) {
    if (previous === null || previous === undefined) return null
    const currentValue = Number(current || 0)
    const previousValue = Number(previous || 0)
    if (!previousValue) return currentValue ? { value: 100, direction: 'up' } : { value: 0, direction: 'flat' }
    const rate = Math.round(((currentValue - previousValue) / Math.abs(previousValue)) * 100)
    return { value: Math.abs(rate), direction: rate > 0 ? 'up' : rate < 0 ? 'down' : 'flat' }
  }
  const metricChanges = computed(() => ({
    taskCount: changeRate(total.value.task_count, comparisonTotal.value?.task_count),
    cutLength: changeRate(total.value.cut_length_m, comparisonTotal.value?.cut_length_m),
    grooveLength: changeRate(total.value.groove_length_m, comparisonTotal.value?.groove_length_m),
    workTime: changeRate(total.value.work_time_seconds, comparisonTotal.value?.work_time_seconds),
  }))

  function changeText(change: ReturnType<typeof changeRate>) {
    return change ? `${change.value}%` : '暂无对比'
  }

  function comparisonLabel(change: ReturnType<typeof changeRate>) {
    if (!change) return '当前统计区间'
    return defaultMonthlyView.value ? '较上月' : '较对比区间'
  }

  return { dailyRecords, dailyChartRecords, metricSeries, monthlyRecords, total, monthlyTotal, roundNumber, formatLength, formatDuration, metricChanges, changeText, comparisonLabel }
}
