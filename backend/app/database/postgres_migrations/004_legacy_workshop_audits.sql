-- Preserve retired source-correction history without restoring its business API.
CREATE TABLE workshop_source_correction_audits (
  audit_id TEXT COLLATE "C" PRIMARY KEY,
  report_id TEXT COLLATE "C" NOT NULL,
  task_id TEXT COLLATE "C" NOT NULL,
  payload TEXT COLLATE "C" NOT NULL,
  reason TEXT COLLATE "C" NOT NULL,
  operated_by_id TEXT COLLATE "C" NOT NULL,
  operated_by_name TEXT COLLATE "C" NOT NULL,
  operated_at TEXT COLLATE "C" NOT NULL
);
CREATE INDEX idx_workshop_source_audits_report
  ON workshop_source_correction_audits(report_id NULLS FIRST, operated_at DESC NULLS LAST);
CREATE INDEX idx_workshop_source_audits_retention
  ON workshop_source_correction_audits(operated_at NULLS FIRST, audit_id NULLS FIRST);
