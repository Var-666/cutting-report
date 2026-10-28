/** JSON returned by dynamic SQL previews and configurable report columns. */
export type JsonValue = string | number | boolean | null | JsonValue[] | {
  [key: string]: JsonValue;
};
export type DataRow = Record<string, unknown>;
export interface ApiResponse<T> {
  success: boolean;
  data: T;
  request_id?: string;
  error?: {
    message: string;
  };
  detail?: string;
}
export interface RequestOptions extends Omit<RequestInit, 'headers'> {
  headers?: Record<string, string>;
  responseType?: 'blob';
}
export interface ReadOptions {
  signal?: AbortSignal;
  force?: boolean;
  warmup?: boolean;
}
export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}
export interface Items<T> {
  items: T[];
}
export interface ApiError extends Error {
  code?: string;
  status?: number;
  retryAfterMs?: number;
}
export type ExportFormat = 'html' | 'pdf';
export type ChartType = 'bar_line' | 'bar' | 'line' | 'area';
export interface DateRange {
  startDate: string;
  endDate: string;
}
export interface ChangeEvent<T = string> {
  target: {
    value: T;
  };
  value: T;
}
