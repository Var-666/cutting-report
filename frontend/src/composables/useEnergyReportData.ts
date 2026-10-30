import type { Ref } from 'vue'
import type { EnergyChannel, EnergyPeriodReport, TaskEnergyReport } from '../types/energy'
import { computed } from 'vue'
import { taskProductionDate } from '../api/reportDates'
import { formatChineseMonth, sortReportPeriods } from '../utils/dateDisplay'
import { formatDeviceDisplay } from '../utils/deviceDisplay'
import { deviceSelection } from '../state/devices'

export function useEnergyReportData({ taskDay, monthlyReport, summaryPeriodStart, summaryPeriodEnd }: {
  taskDay: Ref<TaskEnergyReport | null>
  monthlyReport: Ref<EnergyPeriodReport | null>
  summaryPeriodStart: Ref<string>
  summaryPeriodEnd: Ref<string>
}) {
  const taskRows = computed(() => taskDay.value?.records || [])
  const taskChartRows = computed(() => taskRows.value
    .slice(0, 50)
    .sort((left, right) => String(left.production_time || '').localeCompare(String(right.production_time || ''))))
  const summaryRows = computed(() => sortReportPeriods(monthlyReport.value?.rows || [], 'desc'))
  const summaryChartRows = computed(() => sortReportPeriods(monthlyReport.value?.rows || []))
  const month = computed(() => monthlyReport.value)
  const units = computed(() => taskDay.value?.units || { electricity: 'kWh', gas: 'Nm³' })
  const summary = computed(() => taskDay.value?.summary || {
    task_count: null,
    total_work_time: null,
    total_gas_consumption: null,
    total_electricity_consumption: null,
    gas_record_count: 0,
    electricity_record_count: 0,
  })
  const equipmentScopeLabel = computed(() => formatDeviceDisplay(taskDay.value?.equipment_scope?.join(' / ') || deviceSelection.id))
  const energyConnected = computed(() => taskDay.value?.energy_connected !== false)
  const configurationError = computed(() => taskDay.value?.configuration_error || '')
  const energyCoverage = computed(() => {
    const taskCount = Number(summary.value.task_count || 0)
    if (!taskCount) return 0
    const matchedRecords = Number(summary.value.electricity_record_count || 0)
      + Number(summary.value.gas_record_count || 0)
    return Math.max(0, Math.min(100, Math.round((matchedRecords / (taskCount * 2)) * 100)))
  })
  const deviceMetricSeries = computed(() => {
    const rows = [...taskRows.value].sort((left, right) => String(left.production_time || '').localeCompare(String(right.production_time || '')))
    const countsByDay = new Map<string, number>()
    for (const row of rows) {
      const stamp = taskProductionDate(row)
      if (stamp) countsByDay.set(stamp, (countsByDay.get(stamp) || 0) + 1)
    }
    return {
      electricity: rows.map((row) => row.electricity_consumption),
      gas: rows.map((row) => row.gas_consumption),
      coverage: rows.map((row) => (
        (row.electricity_consumption == null ? 0 : 50)
        + (row.gas_consumption == null ? 0 : 50)
      )),
      tasks: [...countsByDay.values()],
      workTime: rows.map((row) => row.work_time),
    }
  })
  const summaryCountLabel = computed(() => `${summaryRows.value.length} 个月`)
  const summarySubtitle = computed(() => {
    const base = `${summaryPeriodStart.value} 至 ${summaryPeriodEnd.value} · ${summaryCountLabel.value}`
    if (!energyConnected.value) return `${base} · 能耗暂未接入`
    const missing = Number(monthlyReport.value?.missing_day_count || 0)
    if (!missing) return base
    return monthlyReport.value?.syncing
      ? `${base} · 后台补齐 ${missing} 天能耗中`
      : `${base} · ${missing} 天暂无能耗数据`
  })

  function format(value: unknown, digits = 1) {
    if (!energyConnected.value && value == null) return '暂未接入'
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return '--'
    return new Intl.NumberFormat('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(Number(value))
  }

  function duration(value: unknown) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return '--'
    const seconds = Math.max(0, Math.round(Number(value)))
    return [Math.floor(seconds / 3600), Math.floor(seconds % 3600 / 60), seconds % 60]
      .map((item) => String(item).padStart(2, '0')).join(':')
  }

  function valueText(item: EnergyChannel | null | undefined) { return format(item?.value) }

  function summaryPeriod(value: unknown) { return formatChineseMonth(value) }

  return { taskRows, taskChartRows, summaryRows, summaryChartRows, month, units, summary, equipmentScopeLabel, energyConnected, configurationError, energyCoverage, deviceMetricSeries, summaryCountLabel, summarySubtitle, format, duration, valueText, summaryPeriod }
}
