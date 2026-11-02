import type { WeldingQuery as ApiQuery, WeldingAnalysisRequest, WeldingDailyReport, WeldingJobsReport, WeldingPartsReport, WeldingEquipmentReport, WeldingSeamRow } from '../api/generated/types.gen'

export type { WeldingDailyRow, WeldingDeviceRow, WeldingEquipmentRow, WeldingJobRow, WeldingPartRow, WeldingSeamRow, WeldingMeta, WeldingPart, WeldingProject, ReportAnalysis as WeldingAnalysis } from '../api/generated/types.gen'
export type WeldingReportType = NonNullable<WeldingAnalysisRequest['report_type']>
export type WeldingReportKind = 'dashboard' | WeldingReportType
// URL/form filter codes remain strings until the existing request serializer sends them.
type FilterCode = 'device_no' | 'robot_no' | 'weld_type' | 'state' | 'part_state'
export type WeldingQuery = Omit<ApiQuery, FilterCode> & { [Key in FilterCode]?: ApiQuery[Key] | string }
export type WeldingFilterDraft = Partial<WeldingQuery>

type WeldingReports = {
  daily: WeldingDailyReport
  jobs: WeldingJobsReport
  parts: WeldingPartsReport
  equipment: WeldingEquipmentReport
}
export type WeldingReport<K extends WeldingReportType = WeldingReportType> = WeldingReports[K]
export type WeldingRows = { [Key in WeldingReportType]: WeldingReports[Key]['items'][number] }
export type WeldingRow = WeldingRows[WeldingReportType] | WeldingSeamRow
export type WeldingMetrics = Omit<import('../api/generated/types.gen').WeldingDailyRow, 'production_date'>
