import type { WeldingReport, WeldingReportKind } from '../types/welding'
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

export function weldingDisplay(value: unknown, key = '') {
  if (value === null || value === undefined || value === '') return ['completed_part_count', 'arc_rate'].includes(key) ? '待核实' : '未记录'
  if (Array.isArray(value)) return value.join('；') || '—'
  if (typeof value === 'number') return value.toLocaleString('zh-CN', { maximumFractionDigits: /length|hours|output|rate/.test(key) ? 3 : 0 })
  return String(value)
}

export function weldingKpis(kind: WeldingReportKind, report: WeldingReport | null) {
  const data = kind === 'dashboard' ? report?.today : report?.summary
  const definitions = {
    dashboard: [['weld_length_m', '今日焊接长度', 'm'], ['involved_part_count', '今日参与焊接工件数', '件'], ['job_count', '今日焊接作业次数', '次'], ['working_hours', '今日累计作业用时', 'h']],
    daily: [['weld_length_m', '区间焊接总长度', 'm'], ['involved_part_count', '区间参与焊接工件数', '件'], ['completed_part_count', '完成工件数', '件'], ['arc_rate', '综合燃弧率', '%']],
    parts: [['involved_part_count', '区间涉及工件数', '件'], ['completed_part_count', '完成工件数', '件'], ['part_actual_length_m', '涉及工件累计实际长度', 'm'], ['weld_rate', '整体焊接率', '%']],
    jobs: [['job_count', '区间作业次数', '次'], ['flat_length_m', '平焊长度（不含包角类型）', 'm'], ['vertical_length_m', '立焊长度（不含包角类型）', 'm'], ['working_hours', '累计作业用时', 'h']],
    equipment: [['device_count', '参与作业设备数', '台'], ['robot_station_count', '活跃设备机器人工位数', '组'], ['weld_length_m', '累计焊接长度', 'm'], ['output_per_hour', '单位作业用时产出', 'm/h']],
  }
  return (definitions[kind] || definitions.daily).map(([key, title, unit]) => ({ key, title, unit, value: data ? data[key] ?? '待核实' : null, pending: Boolean(report?.meta?.pending_metrics?.[key]) }))
}
