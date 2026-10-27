export interface EnergyChannel {
  value: number | null;
  status: string;
  label: string;
  valid_count?: number;
  expected_count?: number;
  complete_count?: number;
  estimated_count?: number;
}
export interface EnergySummary {
  electricity: EnergyChannel;
  gas: EnergyChannel;
}
export interface EnergyTask {
  task_id: string;
  project_code: string | null;
  ship_no: string | null;
  segment_name: string | null;
  work_time: number | null;
  electricity_consumption: number | null;
  gas_consumption: number | null;
  production_date?: string | null;
  production_time?: string | null;
}
export interface TaskEnergyReport {
  report_date: string;
  period_start: string;
  period_end: string;
  records: EnergyTask[];
  units: {
    electricity: string;
    gas: string;
  };
  equipment_scope: string[];
  energy_connected: boolean;
  configuration_error: string;
  energy_notice: string;
  warning: string;
  analysis_source_version?: string;
  summary: {
    task_count: number;
    total_work_time: number | null;
    total_gas_consumption: number | null;
    total_electricity_consumption: number | null;
    gas_record_count: number;
    electricity_record_count: number;
  };
}
export interface EnergyPeriodRow extends EnergySummary {
  report_date: string;
  task_count: number;
  work_time_seconds: number;
}
export interface EnergyMonthReport {
  units: {
    gas: string;
    electricity: string;
  };
  rows: (EnergySummary & { report_date: string })[];
  summary: EnergySummary;
  energy_connected: boolean;
  elapsed_day_count?: number;
  analysis_source_version?: string;
  warning?: string;
  updated_at?: string | null;
}
export interface EnergyPeriodReport extends EnergyMonthReport {
  rows: EnergyPeriodRow[];
  total_task_count: number;
  total_work_time_seconds: number;
  missing_day_count: number;
  syncing: boolean;
}
export interface EnergyDayReport {
  report_date: string;
  rows: (EnergySummary & { hour: number; label: string; start: string; end: string })[];
  summary: EnergySummary;
  saved: boolean;
  sync_ok: boolean;
  is_today: boolean;
  warning: string;
  updated_at: string | null;
}
export interface EnergyRangeReport {
  task_report: TaskEnergyReport;
}
