import type { DataRow } from './api';
export interface SystemHealth {
  status: string;
  background_ready: boolean;
  background_status: string;
  workers: DataRow;
  version: string;
  demo_mode: boolean;
  data_source_mode: string;
  data_source_name: string;
}
