export interface CuttingCatalog {
  catalog_id: string;
  parent_id?: string | null;
  title: string;
  children?: CuttingCatalog[];
}
export interface CuttingProject {
  project_id: string;
  project_name: string;
  ship_no: string | null;
}
export interface CuttingFile {
  document_id: string;
  document_no: string | null;
  version_id: string | null;
  version: string | null;
  normalized_path: string;
  physical_file_name: string | null;
  physical_version_code: string | null;
  physical_version_no: string | null;
  path_available: boolean;
  path_error: string | null;
  dispatch_status?: {
    status: string;
    status_label: string;
    stage_label: string;
  } | null;
  pbs_info: string | null;
  operate_time: string | null;
  operator: string | null;
  operator_job_no: string | null;
  physical_file_date: string | null;
  physical_file_operator: string | null;
  physical_file_operator_job_no: string | null;
}
export interface CuttingMachine {
  machine_ip: string;
  server_name?: string;
  online: boolean;
  configured?: boolean;
}
export interface CuttingStatus {
  can_push: boolean;
  can_download: boolean;
  can_query: boolean;
  safe_mode: boolean;
  pdm: {
    mode: string;
    enabled: boolean;
    label: string;
  };
  file_source: {
    enabled: boolean;
    mode: string;
    label: string;
  };
  magic_box: {
    configured: boolean;
    machine_ips: string[];
  };
}
export interface CuttingDispatch {
  dispatch_id: string;
  project_id?: string;
  ship_no?: string | null;
  segment_name?: string | null;
  document_id: string;
  document_no: string;
  version_id: string | null;
  version?: string | null;
  relative_path: string;
  machine_ip: string;
  material: string;
  thickness: number;
  quantity: number;
  status: string;
  status_label: string;
  stage_label: string;
  can_execute: boolean;
  last_error: string | null;
  remote_task_id: string | null;
  task_identifier?: string | null;
  created_at: string;
  local_file_cleanup?: {
    error?: string | null;
  };
}
export interface DispatchBatchItem {
  document_id: string;
  version_id: string | null;
  file_path: string;
  material: string;
  thickness: number;
  quantity: number;
  name_check_confirmed: boolean;
}
export interface DispatchCreationResult {
  document_id: string;
  status: string;
  dispatch?: CuttingDispatch | null;
  error?: string | null;
}
