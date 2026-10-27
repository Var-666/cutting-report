import type { DataRow } from './api';
export interface ColumnDefinition {
  field: string;
  label: string;
  type: 'text' | 'number' | 'date' | 'datetime' | 'duration';
  unit: string;
}
export interface ResourceBase {
  code: string;
  name: string;
  description: string;
  enabled: boolean;
  is_builtin: boolean;
  version?: number;
}
export interface QueryTask extends ResourceBase {
  query_task_id: string;
  sql_text: string;
  columns: ColumnDefinition[];
}
export interface TemplateConfig {
  title_prefix: string;
  logo_text: string;
  primary_color: string;
  accent_color: string;
  background_color: string;
  layout: 'split' | 'chart_first' | 'table_first';
  table_density: 'compact' | 'normal' | 'comfortable';
  chart_height: number;
  show_meta: boolean;
}
export interface ReportTemplate extends ResourceBase {
  template_id: string;
  config: TemplateConfig;
}
export interface DesignerDefinition extends ResourceBase {
  definition_id: string;
  query_task_name?: string;
  query_task_id: string;
  template_id: string;
  period_type: 'day' | 'month';
  visual_config: {
    columns: string[];
    metrics: {
    field: string;
    label: string;
    aggregation: string;
    unit: string;
    }[];
    chart: {
    type: string;
    category_field: string;
    value_fields: string[];
    title: string;
    };
  };
}
export interface DesignerResources {
  'query-tasks': QueryTask;
  templates: ReportTemplate;
  definitions: DesignerDefinition;
}
export type DesignerResource = DesignerResources[keyof DesignerResources];
export interface SqlValidation {
  formatted_sql: string;
  tables: string[];
  parameters?: string[];
  valid?: boolean;
}
export interface QueryPreview {
  columns: ColumnDefinition[];
  items: DataRow[];
  returned: number;
  mode: string;
}
export interface QueryPreviewRequest {
  report_date: string;
  project_code: string | null;
  ship_no: string | null;
  parameters: Record<string, string | number | boolean | null>;
  max_rows: number;
}
