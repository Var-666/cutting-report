import type { AnalysisResult } from './reports';
import type { DataRow } from './api';
export type WeldingReportKind = 'dashboard' | 'daily' | 'parts' | 'jobs' | 'equipment';
export interface WeldingQuery {
  start?: string;
  end?: string;
  device_no?: string | number | null;
  robot_no?: string | number | null;
  weld_type?: string | number | null;
  state?: string | number | null;
  part_state?: string | number | null;
  serial_number?: string;
  project_no?: string;
  section_no?: string;
  part_no?: string;
  anomalies_only?: boolean;
  sort_by?: string;
  descending?: boolean;
  page?: number;
  page_size?: number;
}
export interface WeldingRow extends DataRow {
  id: number;
  serial_number: string;
  device_no: string | number | null;
  robot_no: string | number | null;
}
export interface WeldingMeta {
  calculated_at: string;
  data_updated_at: Record<string, string | null>;
  devices: (string | number)[];
  pending_metrics: Record<string, string>;
  quality: Record<string, number>;
  notices: string[];
  unlinked_part_count: number;
  ambiguous_part_count: number;
}
export interface WeldingReport {
  report_type: Exclude<WeldingReportKind, 'dashboard'>;
  query: WeldingQuery;
  summary: Record<string, number | null>;
  today: Record<string, number | null> | null;
  daily: WeldingAggregate[];
  equipment: WeldingAggregate[];
  device_distribution: WeldingAggregate[];
  projects: WeldingProject[];
  speed_distribution: {
    speed_band: string;
    job_count: number;
  }[];
  items: WeldingRow[];
  total: number;
  meta: WeldingMeta;
  data_version: string;
  projects_truncated: boolean;
}
export interface WeldingPart {
  part: WeldingRow;
  items: WeldingRow[];
  total: number;
  association_status: string;
  notice: string;
}
export type WeldingAnalysis = AnalysisResult;
export interface WeldingAggregate {
  production_date: string;
  [key: string]: string | number | null;
  device_no: string | number | null;
  robot_no: string | number | null;
  weld_length_m: number | null;
  working_hours: number | null;
  involved_part_count: number | null;
}
export interface WeldingProject {
  project_name: string;
  project_no: string;
  cumulative_actual_length_m: number | null;
  part_count: number | null;
}
