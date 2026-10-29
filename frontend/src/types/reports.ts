export type { WorkshopTask, WorkshopReportInfo, WorkshopResult, DistributionItem, TopCutTask, TaskConfirmation, ConfirmationAudit, WorkerSummary } from '../api/generated/types.gen'
import type { WorkshopResult } from '../api/generated/types.gen'
import type { Page } from './api';
export type ReportType = 'leadership' | 'workshop' | 'equipment' | 'custom';
export type ReportStatus = 'pending' | 'querying' | 'calculating' | 'analyzing' | 'rendering' | 'completed' | 'failed';
export interface ReportCreateRequest {
  report_type: ReportType;
  report_date: string;
  period_start?: string | null;
  period_end?: string | null;
  project_code?: string | null;
  ship_no?: string | null;
  definition_id?: string | null;
  parameters?: {
    chart_type?: string;
    [key: string]: string | number | boolean | null | undefined;
  };
}
export interface ReportJob {
  report_id: string;
  report_type: ReportType;
  report_date: string;
  status: ReportStatus;
  stage_message: string;
  error?: {
    message: string;
  } | null;
  report_title?: string | null;
  period_start?: string;
  period_end?: string;
  project_code?: string | null;
  ship_no?: string | null;
  task_count?: number | null;
  generated_at?: string | null;
  created_at: string;
  view_url?: string | null;
  download_url?: string | null;
  ai_status?: string | null;
}
export interface AnalysisFact {
  id?: string;
  fact_id?: string;
  label: string;
  value: string | number | null;
  source?: string;
  description?: string;
  unit?: string;
}
export interface AnalysisResult {
  status: string;
  overview: string;
  features: string;
  alerts: string;
  suggestions: string;
  mode: string;
  facts: AnalysisFact[];
  data_version: string | null;
  generated_at: string | null;
  cached: boolean;
  error?: string | null;
}
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
export interface LeadershipPeriodRecord {
  period_start: string;
  task_count: number;
  cut_length_m: number;
  groove_length_m: number;
  work_time_seconds: number;
  remark?: string | null;
}
export interface SnapshotState {
  data_as_of?: string | null;
  stale?: boolean;
  refreshing?: boolean;
  cached?: boolean;
  source?: string;
  query_duration_ms?: number | null;
}
export interface LeadershipReport {
  report_month: string;
  source_mode: string;
  daily_records: LeadershipPeriodRecord[];
  monthly_records: LeadershipPeriodRecord[];
  current_month_total: LeadershipPeriodRecord;
  twelve_month_total: LeadershipPeriodRecord;
  year_ago_total?: LeadershipPeriodRecord | null;
  project_distribution: {
    project_name: string;
    task_count: number;
    cut_length_m: number;
    work_time_seconds: number;
  }[];
  snapshot: SnapshotState;
  period_start?: string;
  period_end?: string;
  comparison_total?: LeadershipPeriodRecord | null;
  analysis_source_version?: string;
  analysis_summary_source_version?: string;
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
}
export interface ReportDefinition {
  definition_id: string;
  name: string;
  period_type: string;
}
