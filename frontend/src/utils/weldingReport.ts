import type { DataTableBaseColumn } from 'naive-ui'
import type { WeldingReport, WeldingReportKind, WeldingRow, WeldingMetrics } from '../types/welding'
export const weldingTitles = { dashboard: '生产首页', daily: '生产统计报表', parts: '工件生产报表', jobs: '焊接作业报表', equipment: '设备效能报表' }
const common = [['device_no', '设备'], ['robot_no', '机器人']]
const metrics = [['involved_part_count', '参与工件数'], ['job_count', '作业次数'], ['flat_length_m', '平焊长度(m)'], ['vertical_length_m', '立焊长度(m)'], ['wrap_length_m', '包角类型长度(m)'], ['unknown_length_m', '未知工艺长度(m)'], ['weld_length_m', '总长度(m)'], ['working_hours', '累计作业用时(h)'], ['output_per_hour', '单位作业用时产出(m/h)']]
export const weldingColumns: Record<string, string[][]> = {
  daily: [['production_date', '生产日期'], ...metrics, ['completed_part_count', '完成工件数(待核实)'], ['arc_rate', '燃弧率(待核实)']],
  equipment: [...common, ...metrics],
  jobs: [['id', '作业ID'], ['end_time', '结束时间'], ['serial_number', '工件流水号'], ...common, ['riser_no', '立板'], ['welding_seam_no', '焊缝序号'], ['weld_type_label', '焊接类型'], ['weld_length_m', '长度(m)'], ['weld_speed', '源表速度(mm/min)'], ['working_hours', '作业用时(h)'], ['state_label', '状态'], ['quality_reasons', '数据核查提示']],
  parts: [['id', '工件ID'], ['project_no', '项目编号'], ['project_name', '项目名称'], ['section_no', '分段'], ['part_no', '工件编号'], ['serial_number', '流水号'], ['device_no', '工件记录设备'], ['theoretical_length_m', '累计理论长度(m)'], ['actual_length_m', '累计实际长度(m)'], ['weld_rate', '源表焊接率(待核实)'], ['arc_rate', '源表燃弧率(待核实)'], ['start_time', '开始时间'], ['end_time', '结束时间'], ['state_label', '状态'], ['quality_reasons', '数据核查提示']],
  seams: [['id', '焊缝ID'], ['riser_no', '立板'], ['welding_seam_no', '焊缝序号'], ...common, ['theoretical_length_m', '理论长度(m)'], ['actual_length_m', '实际长度(m)'], ['working_hours', '焊缝用时(h)'], ['start_time', '开始时间'], ['end_time', '结束时间'], ['state_label', '状态'], ['quality_reasons', '数据核查提示']],
}

// Full field lists above remain available in record details; keep the main tables focused.
export const weldingVisibleColumns: Record<string, string[]> = {
  daily: ['production_date', 'job_count', 'involved_part_count', 'weld_length_m', 'working_hours', 'output_per_hour'],
  equipment: ['device_no', 'robot_no', 'job_count', 'involved_part_count', 'weld_length_m', 'working_hours', 'output_per_hour'],
  jobs: ['end_time', 'serial_number', 'device_no', 'robot_no', 'weld_type_label', 'weld_length_m', 'working_hours', 'state_label'],
  parts: ['project_no', 'section_no', 'part_no', 'serial_number', 'device_no', 'actual_length_m', 'state_label'],
  seams: ['riser_no', 'welding_seam_no', 'device_no', 'robot_no', 'theoretical_length_m', 'actual_length_m', 'working_hours', 'state_label'],
}

export const weldingDescriptions: Record<WeldingReportKind, string> = {
  dashboard: '今日生产概览与近七天生产动态',
  daily: '总览焊接生产成果，查看每日趋势与设备贡献。',
  parts: '核对工件生产归属、累计产出与关联焊缝。',
  jobs: '查询现场作业记录，分析工艺结构与数据异常候选。',
  equipment: '对比设备和机器人工位的生产贡献与有效作业数据。',
}

export const weldingProcesses = [
  ['flat_length_m', '平焊'], ['flat_wrap_length_m', '平焊包角'],
  ['vertical_length_m', '立焊'], ['vertical_wrap_length_m', '立焊包角'], ['unknown_length_m', '未知工艺'],
] as const

export function weldingProcessRows(summary: WeldingReport['summary']) {
  return weldingProcesses.map(([key, name]) => ({ key, name, length: summary[key] ?? null }))
}

export function weldingMetricSeries(report: WeldingReport | null, key: string) {
  return [...(report?.daily || [])].sort((a, b) => a.production_date.localeCompare(b.production_date))
    .map(row => row[key as keyof WeldingMetrics] ?? null)
}

export const weldingColumnLayout: Record<string, Pick<DataTableBaseColumn<WeldingRow>, 'width' | 'minWidth' | 'align'>> = {
  production_date: { width: 152, align: 'left' },
  device_no: { width: 96, align: 'left' },
  robot_no: { width: 88, align: 'left' },
  involved_part_count: { width: 112, align: 'right' },
  job_count: { width: 100, align: 'right' },
  flat_length_m: { width: 120, align: 'right' },
  vertical_length_m: { width: 120, align: 'right' },
  wrap_length_m: { width: 144, align: 'right' },
  unknown_length_m: { width: 144, align: 'right' },
  weld_length_m: { width: 120, align: 'right' },
  working_hours: { width: 152, align: 'right' },
  output_per_hour: { width: 184, align: 'right' },
  completed_part_count: { width: 168, align: 'right' },
  arc_rate: { width: 152, align: 'right' },
  id: { width: 88, align: 'left' },
  end_time: { width: 184, align: 'left' },
  serial_number: { width: 200, align: 'left' },
  riser_no: { width: 88, align: 'left' },
  welding_seam_no: { width: 104, align: 'left' },
  weld_type_label: { width: 104, align: 'center' },
  weld_speed: { width: 168, align: 'right' },
  state_label: { width: 176, align: 'center' },
  quality_reasons: { width: 200, align: 'left' },
  project_no: { width: 120, align: 'left' },
  project_name: { minWidth: 180, align: 'left' },
  section_no: { width: 100, align: 'left' },
  part_no: { width: 152, align: 'left' },
  theoretical_length_m: { width: 152, align: 'right' },
  actual_length_m: { width: 152, align: 'right' },
  weld_rate: { width: 168, align: 'right' },
  start_time: { width: 184, align: 'left' },
}

export function weldingDisplay(value: unknown, key = '') {
  if (value === null || value === undefined || value === '') return ['completed_part_count', 'arc_rate'].includes(key) ? '待核实' : '未记录'
  if (Array.isArray(value)) return value.join('；') || '—'
  if (typeof value === 'number') {
    if (!/length|hours|count|output|speed|duration|seconds|consumption|rate/.test(key)) return String(value)
    const digits = /count/.test(key) ? 0 : 3
    return value.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits })
  }
  return String(value)
}

export function weldingKpis(kind: WeldingReportKind, report: WeldingReport | null) {
  const data = kind === 'dashboard' ? report?.today : report?.summary
  const definitions = {
    dashboard: [['weld_length_m', '今日焊接长度', 'm'], ['job_count', '今日焊接作业次数', '次'], ['involved_part_count', '今日参与工件数', '件'], ['working_hours', '今日累计作业用时', 'h']],
    daily: [['job_count', '焊接作业次数', '次'], ['weld_length_m', '焊接总长度', 'm'], ['involved_part_count', '参与焊接工件数', '件'], ['working_hours', '累计作业用时', 'h']],
    parts: [['involved_part_count', '涉及工件数', '件'], ['part_row_count', '工件记录数', '条'], ['part_actual_length_m', '累计实际焊接长度', 'm'], ['ambiguous_part_count', '待核查关联数', '个']],
    jobs: [['job_count', '作业次数', '次'], ['weld_length_m', '焊接总长度', 'm'], ['working_hours', '累计作业用时', 'h'], ['output_per_hour', '单位用时产出', 'm/h']],
    equipment: [['device_count', '参与作业设备数', '台'], ['robot_station_count', '活跃设备机器人工位数', '组'], ['weld_length_m', '累计焊接长度', 'm'], ['output_per_hour', '单位作业用时产出', 'm/h']],
  }
  return definitions[kind].map(([key, title, unit]) => {
    const pending = Boolean(report?.meta?.pending_metrics?.[key])
    const value = key === 'ambiguous_part_count' ? report?.meta?.ambiguous_part_count : data?.[key]
    return { key, title, unit, value: pending ? '待核实' : value ?? null, pending }
  })
}
