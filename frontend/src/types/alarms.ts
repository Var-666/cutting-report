import type { Page } from './api';
export interface AlarmIncident {
  incident_id: string;
  alarm_code: string;
  alarm_desc: string;
  alarm_class: string;
  alarm_type: string;
  machine_no: string;
  severity: string;
  status: string;
  last_seen_at: string;
  first_seen_at: string;
  first_start_time: string | null;
  latest_remove_time: string | null;
  pdf_report_id: string | null;
  report_status: string | null;
  report_view_url: string | null;
  report_error_message: string | null;
}
export interface AlarmStatus {
  enabled: boolean;
  machine_nos: string[];
  worker_running: boolean;
  monitor_start_at: string | null;
  last_success_at: string | null;
  last_alarm_id: number;
}
export interface AlarmFact {
  id: string;
  incident_id: string;
  machine_no: string;
  alarm_type: string;
  alarm_code: string;
  alarm_class: string;
  alarm_desc: string;
  severity: string;
  status: string;
  start_time: string | null;
  remove_time: string | null;
}
export interface AlarmAnalysis {
  summary: string;
  overall_risk: string;
  limitations: string[];
  facts: AlarmFact[];
  findings: {
    title: string;
    severity: string;
    confidence: number;
    analysis: string;
    possible_causes: string[];
    evidence_ids: string[];
  }[];
  actions: {
    priority: number;
    action: string;
    evidence_ids: string[];
  }[];
}
export interface AlarmReport {
  report_id: string;
  status: string;
  model_status: string;
  created_at: string;
  completed_at?: string | null;
  analysis: AlarmAnalysis | null;
  machine_no: string;
  pdf_filename: string | null;
  file_size: number | null;
  incident_ids: string[];
  incident_count: number;
  view_url: string | null;
  download_url: string | null;
  error_message: string | null;
}
export interface AlarmIncidentSummary {
  total: number;
  all_total: number;
  open_count: number;
  page_count: number;
  severity_counts: Record<string, number>;
  dominant_alarm: {
    alarm_desc: string;
    count: number;
  } | null;
}
export interface AlarmIncidentPage extends Page<AlarmIncident>, AlarmIncidentSummary {
}
export interface AlarmReportPage extends Page<AlarmReport> {
  completed_count: number;
  page_count: number;
}
