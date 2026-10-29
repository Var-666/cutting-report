import type { ReportDataMeta } from './reportData'
import type { AnalysisResult } from './reports'

export type WeldingReportType = 'daily' | 'parts' | 'jobs' | 'equipment'
export type WeldingReportKind = 'dashboard' | WeldingReportType
export interface WeldingQuery {
  start: string
  end: string
  device_no?: string | number | null
  robot_no?: string | number | null
  weld_type?: string | number | null
  state?: string | number | null
  part_state?: string | number | null
  serial_number?: string
  project_no?: string
  section_no?: string
  part_no?: string
  anomalies_only?: boolean
  sort_by?: 'time' | 'length' | 'id'
  descending?: boolean
  page?: number
  page_size?: number
}
// Draft filters may be incomplete; API requests require both dates.
export type WeldingFilterDraft = Partial<WeldingQuery>

export interface WeldingMetrics {
  source_rows: number
  job_count: number
  involved_part_count: number
  weld_length_m: number
  working_hours: number
  output_per_hour: number | null
  device_count: number
  robot_station_count: number
  invalid_time_rows: number
  invalid_serial_rows: number
  invalid_length_rows: number
  invalid_duration_rows: number
  unverified_state_rows: number
  flat_wrap_length_m: number
  flat_length_m: number
  vertical_wrap_length_m: number
  vertical_length_m: number
  unknown_length_m: number
  wrap_length_m: number
  completed_part_count: null
  arc_rate: null
  weld_rate: null
  additional_wrap_length_m: null
}
export interface WeldingDailyRow extends WeldingMetrics {
  production_date: string
}
export interface WeldingDeviceRow extends WeldingMetrics {
  device_no: number | null
}
export interface WeldingEquipmentRow extends WeldingDeviceRow {
  robot_no: number | null
}
interface WeldingDetailRow {
  id: number
  serial_number: string | null
  device_no: number | null
  start_time: string | null
  end_time: string | null
  working_hours: number | null
  state_label: string
  quality_reasons: string[]
}
export interface WeldingJobRow extends WeldingDetailRow {
  robot_no: number | null
  riser_no: number | null
  welding_seam_no: number | null
  weld_type_label: string
  weld_length_m: number | null
  weld_speed: number | null
}
export interface WeldingPartRow extends WeldingDetailRow {
  project_no: string | null
  project_name: string | null
  section_no: string | null
  part_no: string | null
  theoretical_length_m: number | null
  actual_length_m: number | null
  weld_rate: number | null
  arc_rate: number | null
}
export interface WeldingSeamRow extends WeldingDetailRow {
  robot_no: number | null
  riser_no: number | null
  welding_seam_no: number | null
  theoretical_length_m: number | null
  actual_length_m: number | null
}
export interface WeldingRows {
  daily: WeldingDailyRow
  equipment: WeldingEquipmentRow
  jobs: WeldingJobRow
  parts: WeldingPartRow
}
export type WeldingRow = WeldingRows[WeldingReportType] | WeldingSeamRow
export interface WeldingMeta {
  report_data?: ReportDataMeta
  calculated_at: string
  source_info_as_of?: string
  data_updated_at: Record<string, string | null>
  devices: number[]
  pending_metrics: Record<string, string>
  quality: Record<string, number>
  notices: string[]
  unlinked_part_count: number
  ambiguous_part_count: number
}
interface WeldingReportBase {
  query: WeldingQuery
  summary: Record<string, number | null>
  today: Record<string, number | null> | null
  daily: WeldingDailyRow[]
  equipment: WeldingEquipmentRow[]
  // Device distribution groups by device only, without robot_no or pending metrics.
  device_distribution: Omit<WeldingDeviceRow, 'wrap_length_m' | 'completed_part_count' | 'arc_rate' | 'weld_rate' | 'additional_wrap_length_m'>[]
  projects: WeldingProject[]
  speed_distribution: { speed_band: string; job_count: number }[]
  total: number
  meta: WeldingMeta
  data_version: string
  projects_truncated: boolean
}
export type WeldingReport<K extends WeldingReportType = WeldingReportType> = {
  [P in K]: WeldingReportBase & { report_type: P; items: WeldingRows[P][] }
}[K]
export interface WeldingPart {
  part: WeldingPartRow
  items: WeldingSeamRow[]
  total: number
  association_status: string
  notice: string
}
export type WeldingAnalysis = AnalysisResult
export interface WeldingProject {
  project_name: string | null
  project_no: string | null
  cumulative_actual_length_m: number | null
  part_count: number
}
