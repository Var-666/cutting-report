ALTER TABLE report_analyses ADD COLUMN reuse_scope TEXT;
ALTER TABLE report_analyses ADD COLUMN reuse_key TEXT;
CREATE INDEX idx_report_analyses_reuse ON report_analyses(reuse_scope, reuse_key, generated_at DESC, insertion_order DESC)
    WHERE status='success' AND mode='model';
ALTER TABLE report_jobs ADD COLUMN analysis_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE report_jobs ADD COLUMN analysis_next_attempt DOUBLE PRECISION NOT NULL DEFAULT 0;
