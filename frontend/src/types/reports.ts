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
export interface WorkshopTask {
  task_id: string;
  project_code: string | null;
  ship_no: string | null;
  segment_name: string | null;
  program_no: string | null;
  cut_length: number | null;
  groove_length: number | null;
  work_time: number | null;
  remark?: string | null;
  production_time?: string | null;
  production_date?: string | null;
  snapshot_revision: number;
  confirmed_by_id?: string | null;
  confirmed_by?: string | null;
  confirmed_by_company_no?: string | null;
  confirmed_at?: string | null;
}
export interface WorkshopReportInfo {
  report_date?: string;
  period_start?: string;
  period_end?: string;
  item_count: number;
  confirmed_count: number;
  analysis_source_version?: string;
  data_as_of?: string;
  snapshot_revision?: number;
  generated_by_name?: string;
  generated_at?: string;
}
export interface WorkshopResult extends Page<WorkshopTask> {
  analysis_source_version?: string;
  report: WorkshopReportInfo | null;
  project_names: string[];
  confirmed_count: number;
  worker_summaries: WorkerSummary[];
  duration_distribution: DistributionItem[];
  status_distribution: DistributionItem[];
  top_cut_length_tasks: TopCutTask[];
  trend?: {
    report_date: string;
    task_count: number;
    pending_count: number;
    confirmed_count: number;
    project_count: number;
    confirmation_rate: number;
  }[];
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
export interface DistributionItem {
  key: string;
  label: string;
  count: number;
}
export interface TopCutTask {
  task_id: string;
  program_no: string | null;
  cut_length_m: number | null;
}
export interface TaskConfirmation {
  task_id: string;
  worker_id: string;
  worker_name: string;
  worker_company_no: string | null;
  confirmed_at: string;
}
export interface ConfirmationAudit {
  audit_id: string;
  action: string;
  actor_name: string;
  reason: string;
  created_at: string;
  previous_worker_name?: string | null;
  new_worker_name?: string | null;
  action_label: string;
  operated_by_name: string;
  operated_at: string;
}
export interface WorkerSummary {
  worker_id: string;
  company_no: string | null;
  worker_name: string;
  confirmed_task_count: number;
  total_cut_length: number;
  total_work_time_seconds: number;
  last_confirmed_at: string | null;
  work_time_share: number;
}
