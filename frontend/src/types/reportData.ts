export interface ReportDataMeta {
  line_id: string
  report_type: string
  data_version: string
  data_as_of: string
  refreshed_at: string
  expires_at: string
  source: 'snapshot' | 'cache' | 'database'
  stale: boolean
  refreshing: boolean
  refresh_failed: boolean
  details_source?: 'snapshot' | 'cache' | 'database'
  today_data_as_of?: string
}
