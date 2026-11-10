export type { WorkshopTask, WorkshopReportInfo, WorkshopResult, DistributionItem, TopCutTask, TaskConfirmation, ConfirmationAudit, WorkerSummary } from '../api/generated/types.gen'
import type { ReportCreateRequest as ApiReportCreateRequest, ReportJob } from '../api/generated/types.gen'
export type { ReportJob, AnalysisFact, ReportAnalysis as AnalysisResult, LeadershipReport, LeadershipPeriodRecord, LeadershipSnapshotState as SnapshotState } from '../api/generated/types.gen'
export type ReportType = NonNullable<ApiReportCreateRequest['report_type']>
export type ReportStatus = ReportJob['status']
// The generation form requires a selected type/date; the API also accepts explicit ranges.
export type ReportCreateRequest = Omit<ApiReportCreateRequest, 'report_type' | 'report_date'> & { report_type: ReportType; report_date: string }
// A newly accepted job has only an ID/status; polling supplies the remaining display fields.
export type ReportJobState = Pick<ReportJob, 'report_id' | 'status'> & Partial<ReportJob>
export interface AnalysisRequest {
  reportType: Exclude<ReportType, 'custom'>;
  reportDate: string;
  periodStart?: string;
  periodEnd?: string;
  summaryPeriodStart?: string;
  summaryPeriodEnd?: string;
  dataVersion?: string;
  force?: boolean;
}
export interface WorkshopQuery {
  reportDate: string;
  projectName?: string;
  confirmationStatus?: string;
  sortMode?: string;
  page?: number;
  pageSize?: number;
}
export interface WorkshopRangeQuery extends Omit<WorkshopQuery, 'reportDate'> {
  startDate: string;
  endDate: string;
  taskId?: string;
}
export interface ReportDefinition {
  definition_id: string;
  name: string;
  period_type: string;
}
