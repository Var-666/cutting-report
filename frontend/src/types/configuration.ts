export type DatabaseSource = 'production' | 'worker' | 'file';
export interface SqlConnection {
  host: string;
  port: number;
  database: string;
  username: string;
  password?: string | null;
  driver: string;
  encrypt: boolean;
  trust_server_certificate: boolean;
  password_configured?: boolean;
}
export interface WeldingConnection {
  enabled?: boolean;
  host: string;
  port: number;
  database: string;
  username: string;
  password?: string | null;
  timezone: string;
  password_configured?: boolean;
}
export interface DatabaseConfiguration {
  data_source_mode: string;
  worker_database_auth_enabled: boolean;
  file_database_enabled: boolean;
  production: SqlConnection;
  worker: SqlConnection;
  file: SqlConnection;
  welding: WeldingConnection;
}
export interface DatabaseStatus {
  mode: string;
  driver: string;
  configured_host: string | null;
  configured_database: string | null;
  connection_configured: boolean;
  pyodbc_installed: boolean;
  all_queries_ready: boolean;
  data_sources: {
    source: string;
    label: string;
    enabled: boolean;
    configured: boolean;
    host: string | null;
    port: number;
    database: string | null;
    uses_primary_fallback: boolean;
  }[];
}
export interface SaveResult {
  message: string;
}
export interface ConnectionTestResult {
  database?: string;
  elapsed_ms?: number;
  message?: string;
  connected?: boolean;
}
export type DiagnosticStatus = 'effective' | 'pending' | 'error' | 'unconfigured';
export interface DiagnosticProcess {
  value: unknown;
  source: string;
  matches_saved: boolean;
  status: DiagnosticStatus;
}
export interface ConfigurationDiagnostics {
  categories: string[];
  errors: string[];
  precedence: string;
  consistent: boolean;
  notes: string[];
  items: {
    key: string;
    category: string;
    status: DiagnosticStatus;
    saved_value: unknown;
    saved_source: string;
    valid: boolean;
    process_mismatch: boolean;
    restart_required: boolean;
    rule: string;
    processes: {
    web: DiagnosticProcess;
    worker: DiagnosticProcess;
    };
  }[];
  processes: {
    web: {
    loaded_at: string;
    hot_loaded_at: string;
    hot_revision: string;
    version: string;
    };
    worker: {
    state: string;
    available: boolean;
    loaded_at?: string;
    hot_loaded_at?: string;
    hot_revision?: string;
    version?: string;
    };
  };
}
export interface DatabasePreviewRequest {
  dataset: 'summary' | 'workshop' | 'equipment';
  report_date: string;
  project_code?: string | null;
  ship_no?: string | null;
  limit?: number;
}
