import type { ProductionLine } from '../types/devices'
import type { ReportDataMeta } from './generated/types.gen'
import type { LeadershipReport } from '../types/reports'
import { getLeadershipReport } from './cutting.ts'
import { getWeldingReport } from './welding.ts'

export interface ProductionOverview {
  metrics: { title: string; value: number | null; unit: string; precision: number }[]
  dataAsOf: string | null
  calculatedAt?: string | null
  source?: ReportDataMeta['source']
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

export function cuttingOverviewFreshness(report: LeadershipReport | null) {
  const meta = report?.meta?.report_data
  const dataAsOf = meta?.data_as_of || report?.snapshot?.data_as_of || null
  const stale = meta?.stale ?? report?.snapshot?.stale ?? false
  const refreshing = meta?.refreshing ?? report?.snapshot?.refreshing ?? false
  const refreshFailed = meta?.refresh_failed ?? false
  return { dataAsOf, stale, refreshing, refreshFailed, preparing: !dataAsOf && (stale || refreshing || refreshFailed), source: meta?.source }
}

export async function getProductionOverview(line: ProductionLine, month: string, signal: AbortSignal, force = false, onPreparing?: () => void, mode: 'read' | 'reload' | 'refresh' | 'poll' = force ? 'poll' : 'read'): Promise<ProductionOverview> {
  if (line.type === 'cutting') {
    const report = await getLeadershipReport(month, { lineId: line.id, signal, mode, onPreparing })
    const total = report.current_month_total
    const freshness = cuttingOverviewFreshness(report)
    const { preparing } = freshness
    return {
      metrics: [
        { title: '任务数', value: preparing ? null : total.task_count ?? null, unit: '项', precision: 0 },
        { title: '切割长度', value: preparing ? null : total.cut_length_m ?? null, unit: '米', precision: 2 },
        { title: '加工工时', value: preparing || total.work_time_seconds == null ? null : total.work_time_seconds / 3600, unit: '小时', precision: 2 },
      ],
      ...freshness, timezone: null, empty: !preparing && total.task_count === 0,
      demo: report.source_mode === 'demo',
    }
  }
  const report = await getWeldingReport('daily', { ...overviewPeriod(month), page: 1, page_size: 10 }, signal, force, { lineId: line.id, onPreparing, mode })
  const meta = report.meta.report_data
  return {
    metrics: [
      { title: '作业次数', value: report.summary.job_count ?? null, unit: '次', precision: 0 },
      { title: '焊接长度', value: report.summary.weld_length_m ?? null, unit: '米', precision: 2 },
      { title: '累计作业用时', value: report.summary.working_hours ?? null, unit: '小时', precision: 2 },
    ],
    dataAsOf: meta?.data_as_of || null, calculatedAt: meta ? null : report.meta.calculated_at || null, source: meta?.source, timezone: report.meta.timezone,
    stale: meta?.stale ?? false, refreshing: meta?.refreshing ?? false,
    refreshFailed: meta?.refresh_failed ?? false, empty: report.summary.job_count === 0, preparing: false,
  }
}
