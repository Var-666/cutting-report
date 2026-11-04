-- P4 baseline of SQLite 001–016. Published migrations are append-only.
-- INTEGER uses signed 64-bit values; REAL remains IEEE double; TEXT timestamps/JSON stay text.
CREATE FUNCTION sqlite_nocase(value text) RETURNS text
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
RETURN translate(value, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz');

CREATE TABLE admin_role_audits (
  audit_id TEXT COLLATE "C" PRIMARY KEY,
  subject_user_id TEXT COLLATE "C" NOT NULL,
  subject_external_id TEXT COLLATE "C" NOT NULL,
  action TEXT COLLATE "C" NOT NULL CHECK (action IN ('grant', 'revoke')),
  operated_by_id TEXT COLLATE "C" NOT NULL,
  operated_by_name TEXT COLLATE "C" NOT NULL,
  operated_at TEXT COLLATE "C" NOT NULL
);

CREATE TABLE alarm_incidents (
  incident_id TEXT COLLATE "C" PRIMARY KEY,
  fingerprint TEXT COLLATE "C" NOT NULL,
  machine_no TEXT COLLATE "C" NOT NULL,
  alarm_type TEXT COLLATE "C" NOT NULL,
  alarm_code TEXT COLLATE "C" NOT NULL,
  alarm_class TEXT COLLATE "C" NOT NULL,
  alarm_desc TEXT COLLATE "C" NOT NULL,
  severity TEXT COLLATE "C" NOT NULL,
  status TEXT COLLATE "C" NOT NULL CHECK (status IN ('open', 'closed')),
  first_seen_at TEXT COLLATE "C" NOT NULL,
  last_seen_at TEXT COLLATE "C" NOT NULL,
  closed_at TEXT COLLATE "C",
  first_start_time TEXT COLLATE "C",
  latest_remove_time TEXT COLLATE "C",
  first_alarm_id BIGINT NOT NULL,
  latest_alarm_id BIGINT NOT NULL,
  pdf_report_id TEXT COLLATE "C"
);

CREATE TABLE alarm_reports (
  report_id TEXT COLLATE "C" PRIMARY KEY,
  emission_key TEXT COLLATE "C" NOT NULL UNIQUE,
  status TEXT COLLATE "C" NOT NULL CHECK (status IN ('generating', 'completed', 'failed')),
  machine_no TEXT COLLATE "C" NOT NULL,
  incident_ids_json TEXT COLLATE "C" NOT NULL,
  analysis_json TEXT COLLATE "C",
  html_relative_path TEXT COLLATE "C",
  pdf_relative_path TEXT COLLATE "C",
  pdf_filename TEXT COLLATE "C",
  file_size BIGINT,
  sha256 TEXT COLLATE "C",
  model_status TEXT COLLATE "C",
  error_message TEXT COLLATE "C",
  created_at TEXT COLLATE "C" NOT NULL,
  completed_at TEXT COLLATE "C",
  execution_owner TEXT COLLATE "C"
);

CREATE TABLE alarm_source_events (
  source_name TEXT COLLATE "C" NOT NULL DEFAULT '',
  source_alarm_id BIGINT NOT NULL,
  incident_id TEXT COLLATE "C" NOT NULL,
  source_hash TEXT COLLATE "C" NOT NULL,
  start_time TEXT COLLATE "C",
  remove_time TEXT COLLATE "C",
  is_open BIGINT NOT NULL,
  first_seen_at TEXT COLLATE "C" NOT NULL,
  last_seen_at TEXT COLLATE "C" NOT NULL,
  PRIMARY KEY (source_name, source_alarm_id),
  FOREIGN KEY (incident_id) REFERENCES alarm_incidents (
    incident_id
  )
);

CREATE TABLE alarm_sync_state (
  source_name TEXT COLLATE "C" PRIMARY KEY,
  last_alarm_id BIGINT NOT NULL DEFAULT 0,
  last_success_at TEXT COLLATE "C",
  monitor_start_at TEXT COLLATE "C",
  lease_owner TEXT COLLATE "C",
  lease_until TEXT COLLATE "C",
  open_recheck_after_id BIGINT NOT NULL DEFAULT 0,
  history_recheck_after_id BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE analysis_generations (
  cache_key TEXT COLLATE "C" PRIMARY KEY,
  attempt_id TEXT COLLATE "C" NOT NULL,
  status TEXT COLLATE "C" NOT NULL,
  updated_at DOUBLE PRECISION NOT NULL,
  error_type TEXT COLLATE "C",
  error_message TEXT COLLATE "C"
);

CREATE TABLE analysis_source_versions (
  report_type TEXT COLLATE "C" NOT NULL,
  report_date TEXT COLLATE "C" NOT NULL,
  data_version TEXT COLLATE "C" NOT NULL,
  updated_at TEXT COLLATE "C" NOT NULL,
  device_id TEXT COLLATE "C" NOT NULL,
  PRIMARY KEY (report_type, report_date, device_id)
);

CREATE TABLE background_schedules (
  schedule_key TEXT COLLATE "C" PRIMARY KEY,
  next_due DOUBLE PRECISION NOT NULL,
  interval DOUBLE PRECISION
);

CREATE TABLE background_tasks (
  task_id TEXT COLLATE "C" PRIMARY KEY,
  kind TEXT COLLATE "C" NOT NULL,
  device_id TEXT COLLATE "C" NOT NULL,
  payload TEXT COLLATE "C" NOT NULL,
  dedup_key TEXT COLLATE "C" NOT NULL UNIQUE,
  status TEXT COLLATE "C" NOT NULL,
  owner TEXT COLLATE "C",
  created_at DOUBLE PRECISION NOT NULL,
  updated_at DOUBLE PRECISION NOT NULL,
  next_attempt DOUBLE PRECISION NOT NULL,
  attempts BIGINT NOT NULL DEFAULT 0,
  last_error TEXT COLLATE "C"
);

CREATE TABLE cutting_downloads (
  download_id TEXT COLLATE "C" PRIMARY KEY,
  document_id TEXT COLLATE "C" NOT NULL,
  status TEXT COLLATE "C" NOT NULL CHECK (status IN ('downloading', 'downloaded', 'failed')),
  created_at TEXT COLLATE "C" NOT NULL,
  updated_at TEXT COLLATE "C" NOT NULL,
  payload TEXT COLLATE "C" NOT NULL
);

CREATE TABLE cutting_file_dispatch_status (
  document_id TEXT COLLATE "C" NOT NULL,
  version_id TEXT COLLATE "C" NOT NULL,
  dispatch_id TEXT COLLATE "C" NOT NULL,
  machine_ip TEXT COLLATE "C" NOT NULL,
  status TEXT COLLATE "C" NOT NULL,
  stage TEXT COLLATE "C" NOT NULL,
  created_at TEXT COLLATE "C" NOT NULL,
  updated_at TEXT COLLATE "C" NOT NULL,
  sent_at TEXT COLLATE "C",
  PRIMARY KEY (document_id, version_id)
);

CREATE TABLE email_delivery_resolutions (
  resolution_id TEXT COLLATE "C" PRIMARY KEY,
  delivery_id TEXT COLLATE "C" NOT NULL,
  run_id TEXT COLLATE "C" NOT NULL,
  message_id TEXT COLLATE "C" NOT NULL,
  actor_id TEXT COLLATE "C" NOT NULL,
  actor_name TEXT COLLATE "C" NOT NULL,
  evidence_note TEXT COLLATE "C" NOT NULL,
  before_results_json TEXT COLLATE "C" NOT NULL,
  after_results_json TEXT COLLATE "C" NOT NULL,
  resolved_status TEXT COLLATE "C" NOT NULL,
  created_at TEXT COLLATE "C" NOT NULL
);

CREATE TABLE energy_daily_snapshots (
  source_key TEXT COLLATE "C" NOT NULL,
  report_date TEXT COLLATE "C" NOT NULL,
  updated_at TEXT COLLATE "C" NOT NULL,
  report_json TEXT COLLATE "C" NOT NULL,
  PRIMARY KEY (source_key, report_date)
);

CREATE TABLE energy_history_queue (
  source_key TEXT COLLATE "C" NOT NULL,
  report_month TEXT COLLATE "C" NOT NULL,
  status TEXT COLLATE "C" NOT NULL DEFAULT 'pending',
  claim_token TEXT COLLATE "C",
  attempt_count BIGINT NOT NULL DEFAULT 0,
  next_attempt_at TEXT COLLATE "C" NOT NULL,
  updated_at TEXT COLLATE "C" NOT NULL,
  last_error_code TEXT COLLATE "C",
  execution_owner TEXT COLLATE "C",
  PRIMARY KEY (source_key, report_month)
);

CREATE TABLE "leadership_month_snapshots" (
  report_month TEXT COLLATE "C" NOT NULL,
  daily_json TEXT COLLATE "C" NOT NULL,
  project_json TEXT COLLATE "C" NOT NULL,
  total_json TEXT COLLATE "C" NOT NULL,
  data_as_of TEXT COLLATE "C" NOT NULL,
  refreshed_at TEXT COLLATE "C" NOT NULL,
  expires_at TEXT COLLATE "C" NOT NULL,
  query_duration_ms BIGINT NOT NULL DEFAULT 0,
  source_fingerprint TEXT COLLATE "C" NOT NULL,
  snapshot_version BIGINT NOT NULL DEFAULT 1,
  PRIMARY KEY (report_month, source_fingerprint)
);

CREATE TABLE llm_usage_calls (
  id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
  occurred_at TEXT COLLATE "C" NOT NULL,
  usage_date TEXT COLLATE "C" NOT NULL,
  scene TEXT COLLATE "C" NOT NULL,
  model TEXT COLLATE "C" NOT NULL,
  status TEXT COLLATE "C" NOT NULL CHECK (status IN ('responded', 'failed')),
  duration_ms BIGINT NOT NULL,
  input_tokens BIGINT,
  output_tokens BIGINT,
  total_tokens BIGINT,
  cached_tokens BIGINT,
  reasoning_tokens BIGINT
);

CREATE TABLE llm_usage_totals (
  id BIGINT PRIMARY KEY CHECK (id = 1),
  call_count BIGINT NOT NULL DEFAULT 0,
  metered_count BIGINT NOT NULL DEFAULT 0,
  total_tokens BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE llm_usage_tracking (
  id BIGINT PRIMARY KEY CHECK (id = 1),
  started_at TEXT COLLATE "C" NOT NULL
);

CREATE TABLE llm_waiters (
  waiter_id TEXT COLLATE "C" PRIMARY KEY,
  expires_at DOUBLE PRECISION NOT NULL
);

CREATE TABLE process_leases (
  name TEXT COLLATE "C" PRIMARY KEY,
  owner TEXT COLLATE "C" NOT NULL,
  expires_at DOUBLE PRECISION NOT NULL,
  heartbeat_at DOUBLE PRECISION NOT NULL,
  status_json TEXT COLLATE "C" NOT NULL DEFAULT '{}'
);

CREATE TABLE query_tasks (
  query_task_id TEXT COLLATE "C" PRIMARY KEY,
  code TEXT COLLATE "C" NOT NULL UNIQUE,
  name TEXT COLLATE "C" NOT NULL,
  description TEXT COLLATE "C" NOT NULL DEFAULT '',
  sql_text TEXT COLLATE "C" NOT NULL,
  columns_json TEXT COLLATE "C" NOT NULL DEFAULT '[]',
  enabled BIGINT NOT NULL DEFAULT 1,
  is_builtin BIGINT NOT NULL DEFAULT 0,
  version BIGINT NOT NULL DEFAULT 1,
  created_at TEXT COLLATE "C" NOT NULL,
  updated_at TEXT COLLATE "C" NOT NULL
);

CREATE TABLE report_analyses (
  cache_key TEXT COLLATE "C" PRIMARY KEY,
  report_type TEXT COLLATE "C" NOT NULL,
  report_date TEXT COLLATE "C" NOT NULL,
  input_hash TEXT COLLATE "C" NOT NULL,
  data_version TEXT COLLATE "C" NOT NULL,
  status TEXT COLLATE "C" NOT NULL,
  mode TEXT COLLATE "C" NOT NULL,
  model_name TEXT COLLATE "C",
  prompt_version TEXT COLLATE "C" NOT NULL,
  result_json TEXT COLLATE "C" NOT NULL,
  generated_at TEXT COLLATE "C" NOT NULL,
  display_scope TEXT COLLATE "C"
);

CREATE TABLE report_jobs (
  report_id TEXT COLLATE "C" PRIMARY KEY,
  device_id TEXT COLLATE "C" NOT NULL,
  report_type TEXT COLLATE "C" NOT NULL DEFAULT 'workshop',
  status TEXT COLLATE "C" NOT NULL,
  stage_message TEXT COLLATE "C" NOT NULL,
  report_date TEXT COLLATE "C" NOT NULL,
  project_code TEXT COLLATE "C",
  ship_no TEXT COLLATE "C",
  created_at TEXT COLLATE "C" NOT NULL,
  started_at TEXT COLLATE "C",
  finished_at TEXT COLLATE "C",
  request_id TEXT COLLATE "C" NOT NULL,
  ai_status TEXT COLLATE "C",
  error_code TEXT COLLATE "C",
  error_message TEXT COLLATE "C",
  definition_id TEXT COLLATE "C",
  report_title TEXT COLLATE "C",
  parameters_json TEXT COLLATE "C" NOT NULL DEFAULT '{}',
  period_start TEXT COLLATE "C",
  period_end TEXT COLLATE "C",
  execution_owner TEXT COLLATE "C"
);

CREATE TABLE report_snapshots (
  line_id TEXT COLLATE "C" NOT NULL,
  report_type TEXT COLLATE "C" NOT NULL,
  period_start TEXT COLLATE "C" NOT NULL,
  period_end TEXT COLLATE "C" NOT NULL,
  query_fingerprint TEXT COLLATE "C" NOT NULL,
  source_fingerprint TEXT COLLATE "C" NOT NULL,
  snapshot_schema_version BIGINT NOT NULL,
  data_version TEXT COLLATE "C" NOT NULL,
  data_as_of TEXT COLLATE "C" NOT NULL,
  refreshed_at TEXT COLLATE "C" NOT NULL,
  expires_at TEXT COLLATE "C" NOT NULL,
  payload TEXT COLLATE "C" NOT NULL,
  PRIMARY KEY (line_id, report_type, period_start, period_end, query_fingerprint, source_fingerprint, snapshot_schema_version)
);

CREATE TABLE report_templates (
  template_id TEXT COLLATE "C" PRIMARY KEY,
  code TEXT COLLATE "C" NOT NULL UNIQUE,
  name TEXT COLLATE "C" NOT NULL,
  description TEXT COLLATE "C" NOT NULL DEFAULT '',
  config_json TEXT COLLATE "C" NOT NULL,
  enabled BIGINT NOT NULL DEFAULT 1,
  is_builtin BIGINT NOT NULL DEFAULT 0,
  version BIGINT NOT NULL DEFAULT 1,
  created_at TEXT COLLATE "C" NOT NULL,
  updated_at TEXT COLLATE "C" NOT NULL
);

CREATE TABLE reports (
  report_id TEXT COLLATE "C" PRIMARY KEY,
  filename TEXT COLLATE "C" NOT NULL,
  relative_path TEXT COLLATE "C" NOT NULL UNIQUE,
  file_size BIGINT NOT NULL,
  sha256 TEXT COLLATE "C" NOT NULL,
  task_count BIGINT NOT NULL,
  generated_at TEXT COLLATE "C" NOT NULL,
  system_version TEXT COLLATE "C" NOT NULL,
  FOREIGN KEY (report_id) REFERENCES report_jobs (
    report_id
  )
);

CREATE TABLE retention_file_deletions (
  relative_path TEXT COLLATE "C" PRIMARY KEY,
  category TEXT COLLATE "C" NOT NULL,
  queued_at TEXT COLLATE "C" NOT NULL,
  attempt_count BIGINT NOT NULL DEFAULT 0,
  last_error TEXT COLLATE "C",
  updated_at TEXT COLLATE "C" NOT NULL
);

CREATE TABLE users (
  user_id TEXT COLLATE "C" PRIMARY KEY,
  username TEXT COLLATE "C" NOT NULL,
  display_name TEXT COLLATE "C" NOT NULL,
  password_hash TEXT COLLATE "C" NOT NULL,
  role TEXT COLLATE "C" NOT NULL CHECK (role IN ('admin', 'worker')),
  auth_source TEXT COLLATE "C" NOT NULL DEFAULT 'local',
  external_id TEXT COLLATE "C",
  group_no TEXT COLLATE "C",
  company_no TEXT COLLATE "C",
  active BIGINT NOT NULL DEFAULT 1,
  created_at TEXT COLLATE "C" NOT NULL,
  updated_at TEXT COLLATE "C" NOT NULL
);

CREATE TABLE workshop_confirmation_audits (
  audit_id TEXT COLLATE "C" PRIMARY KEY,
  scope_machine_no TEXT COLLATE "C" NOT NULL,
  task_id TEXT COLLATE "C" NOT NULL,
  report_id TEXT COLLATE "C",
  action TEXT COLLATE "C" NOT NULL CHECK (action IN ('confirm', 'worker_revoke', 'admin_revoke', 'admin_reassign')),
  previous_worker_id TEXT COLLATE "C",
  previous_worker_name TEXT COLLATE "C",
  new_worker_id TEXT COLLATE "C",
  new_worker_name TEXT COLLATE "C",
  reason TEXT COLLATE "C" NOT NULL,
  operated_by_id TEXT COLLATE "C" NOT NULL,
  operated_by_name TEXT COLLATE "C" NOT NULL,
  operated_by_role TEXT COLLATE "C" NOT NULL,
  operated_at TEXT COLLATE "C" NOT NULL
);

CREATE TABLE workshop_confirmations (
  task_id TEXT COLLATE "C" NOT NULL,
  scope_machine_no TEXT COLLATE "C" NOT NULL,
  report_id TEXT COLLATE "C",
  worker_id TEXT COLLATE "C" NOT NULL,
  worker_name TEXT COLLATE "C" NOT NULL,
  confirmed_at TEXT COLLATE "C" NOT NULL,
  created_at TEXT COLLATE "C" NOT NULL,
  updated_at TEXT COLLATE "C" NOT NULL,
  PRIMARY KEY (scope_machine_no, task_id),
  FOREIGN KEY (worker_id) REFERENCES users (
    user_id
  )
);

CREATE TABLE workshop_reports (
  report_id TEXT COLLATE "C" PRIMARY KEY,
  report_date TEXT COLLATE "C" NOT NULL,
  source_mode TEXT COLLATE "C" NOT NULL,
  scope_machine_no TEXT COLLATE "C" NOT NULL,
  item_count BIGINT NOT NULL,
  generated_by_id TEXT COLLATE "C" NOT NULL,
  generated_by_name TEXT COLLATE "C" NOT NULL,
  generated_at TEXT COLLATE "C" NOT NULL,
  created_at TEXT COLLATE "C" NOT NULL,
  snapshot_revision_floor BIGINT NOT NULL DEFAULT 1,
  FOREIGN KEY (generated_by_id) REFERENCES users (
    user_id
  )
);

CREATE TABLE auth_sessions (
  session_hash TEXT COLLATE "C" PRIMARY KEY,
  user_id TEXT COLLATE "C" NOT NULL,
  created_at TEXT COLLATE "C" NOT NULL,
  expires_at TEXT COLLATE "C" NOT NULL,
  FOREIGN KEY (user_id) REFERENCES users (
    user_id
  ) ON DELETE CASCADE
);

CREATE TABLE cutting_dispatches (
  dispatch_id TEXT COLLATE "C" PRIMARY KEY,
  document_id TEXT COLLATE "C" NOT NULL,
  project_id TEXT COLLATE "C" NOT NULL,
  project_name TEXT COLLATE "C",
  catalog_id TEXT COLLATE "C",
  catalog_title TEXT COLLATE "C",
  version_id TEXT COLLATE "C",
  version TEXT COLLATE "C",
  document_no TEXT COLLATE "C" NOT NULL,
  relative_path TEXT COLLATE "C" NOT NULL,
  machine_ip TEXT COLLATE "C" NOT NULL,
  material TEXT COLLATE "C",
  thickness DOUBLE PRECISION,
  quantity BIGINT NOT NULL CHECK (quantity > 0),
  status TEXT COLLATE "C" NOT NULL CHECK (status IN (
    'waiting_source',
    'ready',
    'downloading',
    'downloaded',
    'pushing',
    'sent',
    'download_failed',
    'push_failed',
    'cancelled'
  )),
  source_mode TEXT COLLATE "C" NOT NULL,
  is_simulation BIGINT NOT NULL DEFAULT 0,
  remote_task_id TEXT COLLATE "C",
  last_error TEXT COLLATE "C",
  created_by_id TEXT COLLATE "C" NOT NULL,
  created_by_name TEXT COLLATE "C" NOT NULL,
  created_at TEXT COLLATE "C" NOT NULL,
  updated_at TEXT COLLATE "C" NOT NULL,
  downloaded_at TEXT COLLATE "C",
  sent_at TEXT COLLATE "C",
  ship_no TEXT COLLATE "C",
  segment_name TEXT COLLATE "C",
  task_identifier TEXT COLLATE "C",
  download_id TEXT COLLATE "C",
  file_size BIGINT,
  file_sha256 TEXT COLLATE "C",
  stage TEXT COLLATE "C",
  attempt_count BIGINT NOT NULL DEFAULT 0,
  attempted_at TEXT COLLATE "C",
  FOREIGN KEY (created_by_id) REFERENCES users (
    user_id
  )
);

CREATE TABLE report_definitions (
  definition_id TEXT COLLATE "C" PRIMARY KEY,
  code TEXT COLLATE "C" NOT NULL UNIQUE,
  name TEXT COLLATE "C" NOT NULL,
  description TEXT COLLATE "C" NOT NULL DEFAULT '',
  query_task_id TEXT COLLATE "C",
  template_id TEXT COLLATE "C" NOT NULL,
  period_type TEXT COLLATE "C" NOT NULL DEFAULT 'day',
  visual_config_json TEXT COLLATE "C" NOT NULL DEFAULT '{}',
  enabled BIGINT NOT NULL DEFAULT 1,
  is_builtin BIGINT NOT NULL DEFAULT 0,
  builtin_kind TEXT COLLATE "C",
  version BIGINT NOT NULL DEFAULT 1,
  created_at TEXT COLLATE "C" NOT NULL,
  updated_at TEXT COLLATE "C" NOT NULL,
  FOREIGN KEY (query_task_id) REFERENCES query_tasks (
    query_task_id
  ),
  FOREIGN KEY (template_id) REFERENCES report_templates (
    template_id
  )
);

CREATE TABLE report_schedules (
  schedule_id TEXT COLLATE "C" PRIMARY KEY,
  device_id TEXT COLLATE "C" NOT NULL,
  name TEXT COLLATE "C" NOT NULL,
  report_type TEXT COLLATE "C" NOT NULL CHECK (report_type IN ('leadership', 'workshop', 'equipment')),
  frequency TEXT COLLATE "C" NOT NULL CHECK (frequency IN ('daily', 'weekly', 'monthly')),
  run_time TEXT COLLATE "C" NOT NULL,
  weekday BIGINT,
  month_day BIGINT,
  report_period TEXT COLLATE "C" NOT NULL,
  recipients_json TEXT COLLATE "C" NOT NULL,
  subject_template TEXT COLLATE "C" NOT NULL,
  chart_type TEXT COLLATE "C" NOT NULL DEFAULT 'bar_line',
  attach_report BIGINT NOT NULL DEFAULT 1,
  max_attempts BIGINT NOT NULL DEFAULT 3,
  enabled BIGINT NOT NULL DEFAULT 1,
  next_run_at TEXT COLLATE "C" NOT NULL,
  last_run_at TEXT COLLATE "C",
  last_status TEXT COLLATE "C",
  created_by_id TEXT COLLATE "C" NOT NULL,
  created_by_name TEXT COLLATE "C" NOT NULL,
  created_at TEXT COLLATE "C" NOT NULL,
  updated_at TEXT COLLATE "C" NOT NULL,
  FOREIGN KEY (created_by_id) REFERENCES users (
    user_id
  )
);

CREATE TABLE scheduled_report_runs (
  run_id TEXT COLLATE "C" PRIMARY KEY,
  device_id TEXT COLLATE "C" NOT NULL,
  schedule_id TEXT COLLATE "C",
  schedule_name TEXT COLLATE "C" NOT NULL,
  report_type TEXT COLLATE "C" NOT NULL,
  report_date TEXT COLLATE "C" NOT NULL,
  scheduled_for TEXT COLLATE "C" NOT NULL,
  trigger_type TEXT COLLATE "C" NOT NULL CHECK (trigger_type IN ('scheduled', 'manual')),
  report_id TEXT COLLATE "C" UNIQUE,
  status TEXT COLLATE "C" NOT NULL,
  recipients_json TEXT COLLATE "C" NOT NULL,
  subject_template TEXT COLLATE "C" NOT NULL,
  chart_type TEXT COLLATE "C" NOT NULL,
  attach_report BIGINT NOT NULL,
  max_attempts BIGINT NOT NULL,
  created_by_id TEXT COLLATE "C" NOT NULL,
  created_by_name TEXT COLLATE "C" NOT NULL,
  last_error TEXT COLLATE "C",
  created_at TEXT COLLATE "C" NOT NULL,
  updated_at TEXT COLLATE "C" NOT NULL,
  UNIQUE (
    schedule_id,
    scheduled_for,
    trigger_type
  ),
  FOREIGN KEY (schedule_id) REFERENCES report_schedules (
    schedule_id
  ) ON DELETE SET NULL
);

CREATE TABLE workshop_report_items (
  report_item_id TEXT COLLATE "C" PRIMARY KEY,
  report_id TEXT COLLATE "C" NOT NULL,
  task_id TEXT COLLATE "C" NOT NULL,
  ordinal BIGINT NOT NULL,
  project_name TEXT COLLATE "C",
  segment_name TEXT COLLATE "C",
  program_no TEXT COLLATE "C",
  cut_length TEXT COLLATE "C",
  groove_length TEXT COLLATE "C",
  work_time TEXT COLLATE "C",
  production_time TEXT COLLATE "C",
  remark TEXT COLLATE "C",
  production_date TEXT COLLATE "C",
  snapshot_revision BIGINT NOT NULL DEFAULT 1,
  ship_no TEXT COLLATE "C",
  UNIQUE (
    report_id,
    task_id
  ),
  FOREIGN KEY (report_id) REFERENCES workshop_reports (
    report_id
  ) ON DELETE CASCADE
);

CREATE TABLE email_deliveries (
  delivery_id TEXT COLLATE "C" PRIMARY KEY,
  run_id TEXT COLLATE "C" NOT NULL UNIQUE,
  report_id TEXT COLLATE "C" NOT NULL UNIQUE,
  recipients_json TEXT COLLATE "C" NOT NULL,
  subject TEXT COLLATE "C" NOT NULL,
  status TEXT COLLATE "C" NOT NULL,
  attempt_count BIGINT NOT NULL DEFAULT 0,
  max_attempts BIGINT NOT NULL,
  next_attempt_at TEXT COLLATE "C" NOT NULL,
  last_error TEXT COLLATE "C",
  message_id TEXT COLLATE "C",
  created_at TEXT COLLATE "C" NOT NULL,
  updated_at TEXT COLLATE "C" NOT NULL,
  sent_at TEXT COLLATE "C",
  recipient_results_json TEXT COLLATE "C" NOT NULL DEFAULT '{}',
  execution_owner TEXT COLLATE "C",
  FOREIGN KEY (run_id) REFERENCES scheduled_report_runs (
    run_id
  ) ON DELETE CASCADE,
  FOREIGN KEY (report_id) REFERENCES report_jobs (
    report_id
  ) ON DELETE CASCADE
);

CREATE INDEX idx_admin_role_audits_retention ON admin_role_audits(operated_at NULLS FIRST, audit_id NULLS FIRST);

CREATE INDEX idx_admin_role_audits_subject ON admin_role_audits(subject_user_id NULLS FIRST, operated_at DESC NULLS LAST);

CREATE UNIQUE INDEX idx_alarm_incidents_one_open ON alarm_incidents(fingerprint NULLS FIRST) WHERE status = 'open';

CREATE INDEX idx_alarm_incidents_recent ON alarm_incidents(first_seen_at DESC NULLS LAST, incident_id DESC NULLS LAST);

CREATE INDEX idx_alarm_incidents_retention ON alarm_incidents(status NULLS FIRST, closed_at NULLS FIRST, incident_id NULLS FIRST);

CREATE INDEX idx_alarm_incidents_start ON alarm_incidents(first_start_time DESC NULLS LAST, first_seen_at DESC NULLS LAST);

CREATE INDEX idx_alarm_incidents_unreported ON alarm_incidents(pdf_report_id NULLS FIRST, first_seen_at NULLS FIRST);

CREATE INDEX idx_alarm_reports_created ON alarm_reports(created_at DESC NULLS LAST, report_id DESC NULLS LAST);

CREATE INDEX idx_alarm_reports_retention ON alarm_reports(status NULLS FIRST, completed_at NULLS FIRST, report_id NULLS FIRST);

CREATE INDEX idx_alarm_source_events_incident ON alarm_source_events(incident_id NULLS FIRST, is_open NULLS FIRST);

CREATE INDEX idx_alarm_source_events_open ON alarm_source_events(source_name NULLS FIRST, is_open NULLS FIRST, source_alarm_id NULLS FIRST);

CREATE INDEX idx_auth_sessions_expires ON auth_sessions(expires_at NULLS FIRST);

CREATE INDEX idx_background_tasks_ready ON background_tasks(status NULLS FIRST, next_attempt NULLS FIRST, created_at NULLS FIRST);

CREATE INDEX idx_cutting_dispatches_created ON cutting_dispatches(created_at DESC NULLS LAST);

CREATE INDEX idx_cutting_dispatches_file_machine ON cutting_dispatches(document_id NULLS FIRST, version_id NULLS FIRST, machine_ip NULLS FIRST);

CREATE INDEX idx_cutting_dispatches_status ON cutting_dispatches(status NULLS FIRST, updated_at NULLS FIRST);

CREATE INDEX idx_cutting_downloads_created ON cutting_downloads(created_at DESC NULLS LAST, download_id DESC NULLS LAST);

CREATE INDEX idx_cutting_downloads_retention ON cutting_downloads(status NULLS FIRST, updated_at NULLS FIRST, download_id NULLS FIRST);

CREATE INDEX idx_email_deliveries_due ON email_deliveries(status NULLS FIRST, next_attempt_at NULLS FIRST);

CREATE INDEX idx_email_delivery_resolutions ON email_delivery_resolutions(delivery_id NULLS FIRST, created_at NULLS FIRST);

CREATE INDEX idx_llm_usage_calls_date ON llm_usage_calls(usage_date NULLS FIRST, id NULLS FIRST);

CREATE INDEX idx_query_tasks_enabled ON query_tasks(enabled NULLS FIRST, updated_at DESC NULLS LAST);

CREATE INDEX idx_report_analyses_display ON report_analyses(display_scope NULLS FIRST, report_type NULLS FIRST, report_date NULLS FIRST, generated_at DESC NULLS LAST);

CREATE INDEX idx_report_analyses_lookup ON report_analyses(report_type NULLS FIRST, report_date NULLS FIRST, generated_at DESC NULLS LAST);

CREATE INDEX idx_report_analyses_retention ON report_analyses(generated_at NULLS FIRST, cache_key NULLS FIRST);

CREATE INDEX idx_report_analyses_version ON report_analyses(report_type NULLS FIRST, report_date NULLS FIRST, data_version NULLS FIRST, generated_at DESC NULLS LAST);

CREATE INDEX idx_report_definitions_enabled ON report_definitions(enabled NULLS FIRST, updated_at DESC NULLS LAST);

CREATE INDEX idx_report_jobs_created_at ON report_jobs(created_at DESC NULLS LAST);

CREATE INDEX idx_report_jobs_status ON report_jobs(status NULLS FIRST, created_at NULLS FIRST);

CREATE INDEX idx_report_schedules_due ON report_schedules(enabled NULLS FIRST, next_run_at NULLS FIRST);

CREATE INDEX idx_retention_file_deletions_queued ON retention_file_deletions(queued_at NULLS FIRST, relative_path NULLS FIRST);

CREATE INDEX idx_scheduled_runs_retention ON scheduled_report_runs(status NULLS FIRST, updated_at NULLS FIRST, run_id NULLS FIRST);

CREATE INDEX idx_scheduled_runs_status ON scheduled_report_runs(status NULLS FIRST, created_at DESC NULLS LAST);

CREATE INDEX idx_users_active_name ON users(active NULLS FIRST, display_name NULLS FIRST);

CREATE UNIQUE INDEX idx_users_external_identity ON users(auth_source NULLS FIRST, external_id NULLS FIRST) WHERE NOT external_id IS NULL;

CREATE INDEX idx_workshop_confirmation_audits_report ON workshop_confirmation_audits(report_id NULLS FIRST, operated_at DESC NULLS LAST);

CREATE INDEX idx_workshop_confirmation_audits_retention ON workshop_confirmation_audits(operated_at NULLS FIRST, audit_id NULLS FIRST);

CREATE INDEX idx_workshop_confirmation_audits_task ON workshop_confirmation_audits(task_id NULLS FIRST, operated_at DESC NULLS LAST);

CREATE INDEX idx_workshop_confirmations_report ON workshop_confirmations(report_id NULLS FIRST, confirmed_at DESC NULLS LAST);

CREATE INDEX idx_workshop_confirmations_retention ON workshop_confirmations(updated_at NULLS FIRST, task_id NULLS FIRST);

CREATE INDEX idx_workshop_confirmations_worker ON workshop_confirmations(worker_id NULLS FIRST, confirmed_at DESC NULLS LAST);

CREATE INDEX idx_workshop_items_report_order ON workshop_report_items(report_id NULLS FIRST, ordinal NULLS FIRST);

CREATE UNIQUE INDEX idx_workshop_reports_device_date ON workshop_reports(report_date NULLS FIRST, SQLITE_NOCASE(scope_machine_no) NULLS FIRST);

CREATE INDEX idx_workshop_reports_generated ON workshop_reports(generated_at DESC NULLS LAST);

CREATE UNIQUE INDEX users_username_nocase ON users (sqlite_nocase(username) COLLATE "C");

CREATE TABLE _sqlite_imports (
    source_digest text PRIMARY KEY,
    source_schema text NOT NULL,
    imported_at text NOT NULL,
    summary_json text NOT NULL
);
CREATE TABLE _sqlite_schema_versions (
    source_digest text NOT NULL REFERENCES _sqlite_imports(source_digest),
    name text NOT NULL,
    applied_at text NOT NULL,
    PRIMARY KEY(source_digest, name)
);
