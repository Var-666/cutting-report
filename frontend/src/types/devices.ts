export interface CuttingDevice {
  id: string;
  name: string;
  machine_no: string;
  production_line_id: string;
  energy_connected: boolean;
  alarm_start_at: string;
  type?: 'cutting';
  type_name?: string;
  capabilities?: ('reports' | 'alarms' | 'dispatch' | 'settings')[];
  enabled?: boolean;
}
export interface WeldingLine {
  id: string;
  name: string;
  type: 'welding';
  type_name: string;
  enabled: boolean;
  capabilities: ('reports' | 'analysis' | 'settings')[];
  machine_no?: never;
  production_line_id?: never;
  energy_connected?: never;
  alarm_start_at?: never;
}
export interface ProductionLine {
  id: string;
  name: string;
  type?: string;
  type_name?: string;
  enabled?: boolean;
  capabilities?: string[];
  home_path?: string;
  machine_no?: string;
  production_line_id?: string;
  energy_connected?: boolean;
  alarm_start_at?: string;
}
export interface DeviceCatalog {
  default_device_id: string;
  items: ProductionLine[];
}
