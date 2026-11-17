import type { PdmStatisticsReport, PdmSummaryTable } from '../api/generated/types.gen'
import { clonePdmFilters, pdmDocumentKinds } from './pdmFilters'
import type { PdmDocumentKind, PdmFilterOptions, PdmFilters } from './pdmFilters'
import type { EChartsOption } from 'echarts'

export interface PdmPanelData {
  table: PdmSummaryTable
  filters: PdmFilters
}

interface PdmReportSection {
  key: string
  kind: PdmDocumentKind
  title: string
  groupBy: PdmFilters['groupBy']
}

export const pdmReportSections: readonly PdmReportSection[] = [
  { key: 'equipment:summary', kind: 'equipment', title: '设备资料 · 所有项目汇总表', groupBy: 'summary' },
  { key: 'equipment:all:profession', kind: 'equipment', title: '设备资料 · 所有项目－分专业表', groupBy: 'summary' },
  { key: 'equipment:all:project', kind: 'equipment', title: '设备资料 · 所有项目－分项目表', groupBy: 'summary' },
  { key: 'equipment:project', kind: 'equipment', title: '设备资料 · 单项目－分专业表', groupBy: 'project' },
  { key: 'equipment:profession', kind: 'equipment', title: '设备资料 · 单专业－分项目表', groupBy: 'profession' },
  { key: 'drawing:summary', kind: 'drawing', title: '详设图纸 · 所有项目汇总表', groupBy: 'summary' },
]

export interface PdmReportRegion {
  section: PdmReportSection
  filters: PdmFilters
  panels: PdmPanelData[]
  omittedGroups: number
  queried: boolean
}

export function pdmReportRegion(section: PdmReportSection, filters: PdmFilters, report?: PdmStatisticsReport, options: PdmFilterOptions = {}): PdmReportRegion {
  const applied = clonePdmFilters(filters)
  applied.documentKinds = [section.kind]
  applied.groupBy = section.groupBy
  const queried = !!report && report.group_by === section.groupBy
  const panels = queried ? pdmPanelResults(report, applied, options).filter(panel => section.groupBy === 'summary'
    ? panel.table.key === section.key : panel.table.key.startsWith(`${section.key}:`)) : []
  return { section, filters: applied, panels, queried,
    omittedGroups: queried ? unassignedPdmGroups(report).find(item => item.value === section.kind)?.total ?? 0 : 0 }
}

function pdmPanelResults(report: PdmStatisticsReport, filters: PdmFilters, options: PdmFilterOptions = {}): PdmPanelData[] {
  const tables = report.tables.filter(table => filters.documentKinds.includes(table.kind))
  return tables.map(table => {
    const applied = clonePdmFilters(filters)
    applied.documentKinds = [table.kind]
    if (report.group_by === 'project' && table.key.startsWith(`${table.kind}:project:`)) {
      applied[table.kind].projects = [table.key.slice(`${table.kind}:project:`.length)]
    } else if (report.group_by === 'profession' && table.key.startsWith(`${table.kind}:profession:`)) {
      const name = table.key.slice(`${table.kind}:profession:`.length)
      const selected = applied[table.kind].professions
      const ids = options[table.kind]?.professions?.filter(option => option.label === name && (!selected.length || selected.includes(option.value))).map(option => option.value)
      if (ids?.length) applied[table.kind].professions = ids
    }
    return { table, filters: applied }
  })
}

export function pdmChartRows(table: PdmSummaryTable, detail = false) {
  return table.rows.filter(row => table.group_label && !detail ? row.subtotal : !row.subtotal)
}

export function pdmDetailCharts(table: PdmSummaryTable): PdmSummaryTable[] {
  return table.rows.filter(row => row.subtotal).map(subtotal => ({ ...table,
    key: `${table.key}:${subtotal.category}`, title: subtotal.category,
    rows: table.rows.filter(row => row.category === subtotal.category && !row.subtotal), totals: subtotal,
  }))
}

export function pdmCompletionChartOption(table: PdmSummaryTable, colors: { onTime: string; total: string; text: string; border: string; divider: string }, detail = false): EChartsOption {
  const rows = pdmChartRows(table, detail)
  return {
    grid: { left: 16, right: 24, top: 56, bottom: 56, containLabel: true },
    legend: { top: 8, left: 'center', data: ['按时完成率', '总完成率'], textStyle: { color: colors.text } },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, valueFormatter: value => value == null ? '—' : `${value}%` },
    xAxis: { type: 'category', name: detail ? table.group_label || '' : '类型', nameLocation: 'middle', nameGap: 48, nameTextStyle: { color: colors.text }, data: rows.map(row => detail ? row.group_name ?? '' : row.category),
      axisLabel: { interval: detail && table.group_label === '项目' ? 'auto' : 0, color: colors.text, formatter: value => detail ? value.length > 6 ? `${value.slice(0, 6)}…` : value : value.includes('-') ? value.replace('-', '\n') : value.length > 4 ? `${value.slice(0, 3)}\n${value.slice(3)}` : value },
      axisLine: { lineStyle: { color: colors.border } } },
    yAxis: { type: 'value', name: '完成率（%）', min: 0, max: 100,
      nameTextStyle: { color: colors.text }, axisLabel: { formatter: '{value}%', color: colors.text },
      splitLine: { lineStyle: { color: colors.divider } } },
    series: [
      { name: '按时完成率', type: 'bar', barMaxWidth: 36, itemStyle: { color: colors.onTime }, data: rows.map(row => row.on_time_rate) },
      { name: '总完成率', type: 'bar', barMaxWidth: 36, itemStyle: { color: colors.total }, data: rows.map(row => row.total_rate) },
    ],
  }
}

export function unassignedPdmRows(table: PdmSummaryTable): number {
  return table.group_label
    ? table.totals.total - table.rows.filter(row => !row.subtotal).reduce((sum, row) => sum + row.total, 0) : 0
}

export function unassignedPdmGroups(report: PdmStatisticsReport) {
  if (report.group_by === 'summary') return []
  return pdmDocumentKinds.map(kind => ({
    ...kind,
    total: (report.summaries[kind.value]?.total ?? 0)
      - report.tables.filter(table => table.kind === kind.value).reduce((sum, table) => sum + table.totals.total, 0),
  })).filter(item => item.total > 0)
}
