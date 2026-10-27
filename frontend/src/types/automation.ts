export interface RecipientResult {
  status: string;
  message?: string;
  code?: number;
  attempts?: number;
}
export interface EmailDelivery {
  delivery_id: string;
  message_id: string;
  status: string;
  recipients: string[];
  recipient_results: Record<string, RecipientResult>;
  schedule_name: string;
  report_date: string;
  attempt_count: number;
  max_attempts: number;
  last_error: string | null;
  sent_at: string | null;
  report_id?: string;
  subject?: string;
  created_at?: string;
  updated_at?: string;
  attempts?: number;
  error_message?: string;
}
export interface EmailResolution {
  resolution_id: string;
  actor_name: string;
  created_at: string;
  evidence_note: string;
  before_results: Record<string, RecipientResult>;
  after_results: Record<string, RecipientResult>;
}
export interface AutomationConfiguration {
  automation_enabled: boolean;
  automation_scan_seconds: number;
  email_retry_base_seconds: number;
  smtp_enabled: boolean;
  smtp_host: string;
  smtp_port: number;
  smtp_username: string;
  smtp_password?: string | null;
  smtp_password_configured?: boolean;
  smtp_from_email: string;
  smtp_from_name: string;
  smtp_use_tls: boolean;
  smtp_use_ssl: boolean;
  smtp_timeout_seconds: number;
}
export interface SchedulePayload {
  device_id: string;
  name: string;
  report_type: string;
  frequency: string;
  run_time: string;
  weekday: number | null;
  month_day: number | null;
  report_period: string;
  recipients: string[];
  subject_template: string;
  chart_type: string;
  attach_report: boolean;
  max_attempts: number;
  enabled: boolean;
}
export interface AutomationSchedule extends SchedulePayload {
  schedule_id: string;
  next_run_at: string | null;
  last_status: string;
  sent_count: number;
  failed_count: number;
}
export interface AutomationStatus {
  smtp: {
    configured: boolean;
    host: string;
    port: number;
  };
  summary: {
    schedule_enabled: number;
    delivery_pending: number;
    delivery_sent: number;
    delivery_failed: number;
    delivery_unknown: number;
  };
}
export type RecipientOutcome = 'accepted' | 'not_accepted';
export interface DeliveryResolutionRequest {
  message_id: string;
  evidence_note: string;
  outcomes: {
    recipient: string;
    outcome: RecipientOutcome;
  }[];
}
