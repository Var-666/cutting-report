import type { ProductionLine } from '../types/devices'
import { getLeadershipReport } from './cutting.ts'
import { getWeldingReport } from './welding.ts'

export interface ProductionOverview {
  metrics: { title: string; value: number | null; unit: string; precision: number }[]
  dataAsOf: string | null
  timezone: string | null
  stale: boolean
  refreshing: boolean
  refreshFailed: boolean
  empty: boolean
  preparing: boolean
  demo?: boolean
}

export function overviewPeriod(month: string) {
  const [year, number] = month.split('-').map(Number)
  return { start: `${month}-01`, end: new Date(Date.UTC(year, number, 0)).toISOString().slice(0, 10) }
}

export async function getProductionOverview(line: ProductionLine, month: string, signal: AbortSignal, force = false, onPreparing?: () => void): Promise<ProductionOverview> {
  if (line.type === 'cutting') {
    const report = await getLeadershipReport(month, { lineId: line.id, signal, force })
    const total = report.current_month_total
    const preparing = Boolean(report.snapshot.stale && !report.snapshot.data_as_of)
    return {
      metrics: [
        { title: '任务数', value: preparing ? null : total.task_count ?? null, unit: '项', precision: 0 },
        { title: '切割长度', value: preparing ? null : total.cut_length_m ?? null, unit: '米', precision: 2 },
        { title: '加工工时', value: preparing || total.work_time_seconds == null ? null : total.work_time_seconds / 3600, unit: '小时', precision: 2 },
      ],
      dataAsOf: report.snapshot.data_as_of ?? null, timezone: null, stale: report.snapshot.stale ?? false,
      refreshing: report.snapshot.refreshing ?? false, refreshFailed: false, empty: !preparing && total.task_count === 0, preparing,
      demo: report.source_mode === 'demo',
    }
  }
  const report = await getWeldingReport('daily', { ...overviewPeriod(month), page: 1, page_size: 10 }, signal, force, { lineId: line.id, onPreparing, refresh: false })
  const meta = report.meta.report_data
  return {
    metrics: [
      { title: '作业次数', value: report.summary.job_count ?? null, unit: '次', precision: 0 },
      { title: '焊接长度', value: report.summary.weld_length_m ?? null, unit: '米', precision: 2 },
      { title: '累计作业用时', value: report.summary.working_hours ?? null, unit: '小时', precision: 2 },
    ],
    dataAsOf: meta?.data_as_of || report.meta.calculated_at, timezone: report.meta.timezone,
    stale: meta?.stale ?? false, refreshing: meta?.refreshing ?? false,
    refreshFailed: meta?.refresh_failed ?? false, empty: report.summary.job_count === 0, preparing: false,
  }
}
