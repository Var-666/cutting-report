export type UserRole = 'admin' | 'worker';
export interface User {
  user_id: string;
  username: string;
  display_name: string;
  role: UserRole;
  active: boolean;
  auth_source?: string;
  company_no?: string | null;
  group_no?: string | null;
  external_id?: string | null;
  created_at?: string;
  permission_changed_by?: string | null;
  permission_changed_at?: string | null;
}
export interface LoginRequest {
  username: string;
  password: string;
}
export interface BootstrapRequest extends LoginRequest {
  display_name: string;
}
export interface Authentication {
  user: User;
  expires_at: string;
}
export interface AuthStatus {
  setup_required: boolean;
  worker_database_auth_enabled: boolean;
  user: User | null;
}
export interface HrPerson {
  external_id: string;
  display_name: string;
  company_no: string;
  group_no?: string | null;
  is_admin?: boolean;
  active?: boolean;
}
