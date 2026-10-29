"""Baseline adoption: preserve existing schema/data upgrades; append future migrations instead of editing."""
from app.database.migrations.runner import execute_script


def upgrade(connection):
    execute_script(connection,
        """
        CREATE TABLE IF NOT EXISTS alarm_sync_state (
            source_name TEXT PRIMARY KEY,
            last_alarm_id INTEGER NOT NULL DEFAULT 0,
            last_success_at TEXT,
            monitor_start_at TEXT,
            lease_owner TEXT,
            lease_until TEXT
        );

        CREATE TABLE IF NOT EXISTS alarm_incidents (
            incident_id TEXT PRIMARY KEY,
            fingerprint TEXT NOT NULL,
            machine_no TEXT NOT NULL,
            alarm_type TEXT NOT NULL,
            alarm_code TEXT NOT NULL,
            alarm_class TEXT NOT NULL,
            alarm_desc TEXT NOT NULL,
            severity TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('open', 'closed')),
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            closed_at TEXT,
            first_start_time TEXT,
            latest_remove_time TEXT,
            first_alarm_id INTEGER NOT NULL,
            latest_alarm_id INTEGER NOT NULL,
            pdf_report_id TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_alarm_incidents_one_open
            ON alarm_incidents(fingerprint) WHERE status = 'open';
        CREATE INDEX IF NOT EXISTS idx_alarm_incidents_recent
            ON alarm_incidents(first_seen_at DESC, incident_id DESC);
        CREATE INDEX IF NOT EXISTS idx_alarm_incidents_start
            ON alarm_incidents(first_start_time DESC, first_seen_at DESC);
        CREATE INDEX IF NOT EXISTS idx_alarm_incidents_unreported
            ON alarm_incidents(pdf_report_id, first_seen_at);
        CREATE INDEX IF NOT EXISTS idx_alarm_incidents_retention
            ON alarm_incidents(status, closed_at, incident_id);

        CREATE TABLE IF NOT EXISTS alarm_source_events (
            source_alarm_id INTEGER PRIMARY KEY,
            incident_id TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            start_time TEXT,
            remove_time TEXT,
            is_open INTEGER NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            FOREIGN KEY(incident_id) REFERENCES alarm_incidents(incident_id)
        );
        CREATE INDEX IF NOT EXISTS idx_alarm_source_events_open
            ON alarm_source_events(is_open, source_alarm_id);
        CREATE INDEX IF NOT EXISTS idx_alarm_source_events_incident
            ON alarm_source_events(incident_id, is_open);

        CREATE TABLE IF NOT EXISTS alarm_reports (
            report_id TEXT PRIMARY KEY,
            emission_key TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL CHECK(status IN ('generating', 'completed', 'failed')),
            machine_no TEXT NOT NULL,
            incident_ids_json TEXT NOT NULL,
            analysis_json TEXT,
            html_relative_path TEXT,
            pdf_relative_path TEXT,
            pdf_filename TEXT,
            file_size INTEGER,
            sha256 TEXT,
            model_status TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            completed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_alarm_reports_created
            ON alarm_reports(created_at DESC, report_id DESC);
        CREATE INDEX IF NOT EXISTS idx_alarm_reports_retention
            ON alarm_reports(status, completed_at, report_id);
        """
    )
    sync_columns = {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(alarm_sync_state)"
        ).fetchall()
    }
    if "monitor_start_at" not in sync_columns:
        connection.execute(
            "ALTER TABLE alarm_sync_state ADD COLUMN monitor_start_at TEXT"
        )
    for column in ("open_recheck_after_id", "history_recheck_after_id"):
        if column not in sync_columns:
            connection.execute(
                f"ALTER TABLE alarm_sync_state ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0"
            )
    report_columns = {row[1] for row in connection.execute("PRAGMA table_info(alarm_reports)")}
    if "execution_owner" not in report_columns:
        connection.execute("ALTER TABLE alarm_reports ADD COLUMN execution_owner TEXT")
