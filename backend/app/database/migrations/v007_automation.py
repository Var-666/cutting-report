"""Baseline adoption: preserve existing schema/data upgrades; append future migrations instead of editing."""
from app.database.migrations.runner import execute_script


def upgrade(connection):
    execute_script(connection,
        """
        CREATE TABLE IF NOT EXISTS report_schedules (
            schedule_id TEXT PRIMARY KEY,
            device_id TEXT NOT NULL,
            name TEXT NOT NULL,
            report_type TEXT NOT NULL CHECK(report_type IN ('leadership', 'workshop', 'equipment')),
            frequency TEXT NOT NULL CHECK(frequency IN ('daily', 'weekly', 'monthly')),
            run_time TEXT NOT NULL,
            weekday INTEGER,
            month_day INTEGER,
            report_period TEXT NOT NULL,
            recipients_json TEXT NOT NULL,
            subject_template TEXT NOT NULL,
            chart_type TEXT NOT NULL DEFAULT 'bar_line',
            attach_report INTEGER NOT NULL DEFAULT 1,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            enabled INTEGER NOT NULL DEFAULT 1,
            next_run_at TEXT NOT NULL,
            last_run_at TEXT,
            last_status TEXT,
            created_by_id TEXT NOT NULL,
            created_by_name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(created_by_id) REFERENCES users(user_id)
        );
        CREATE INDEX IF NOT EXISTS idx_report_schedules_due
            ON report_schedules(enabled, next_run_at);

        CREATE TABLE IF NOT EXISTS scheduled_report_runs (
            run_id TEXT PRIMARY KEY,
            device_id TEXT NOT NULL,
            schedule_id TEXT,
            schedule_name TEXT NOT NULL,
            report_type TEXT NOT NULL,
            report_date TEXT NOT NULL,
            scheduled_for TEXT NOT NULL,
            trigger_type TEXT NOT NULL CHECK(trigger_type IN ('scheduled', 'manual')),
            report_id TEXT UNIQUE,
            status TEXT NOT NULL,
            recipients_json TEXT NOT NULL,
            subject_template TEXT NOT NULL,
            chart_type TEXT NOT NULL,
            attach_report INTEGER NOT NULL,
            max_attempts INTEGER NOT NULL,
            created_by_id TEXT NOT NULL,
            created_by_name TEXT NOT NULL,
            last_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(schedule_id, scheduled_for, trigger_type),
            FOREIGN KEY(schedule_id) REFERENCES report_schedules(schedule_id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_scheduled_runs_status
            ON scheduled_report_runs(status, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_scheduled_runs_retention
            ON scheduled_report_runs(status, updated_at, run_id);

        CREATE TABLE IF NOT EXISTS email_deliveries (
            delivery_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL UNIQUE,
            report_id TEXT NOT NULL UNIQUE,
            recipients_json TEXT NOT NULL,
            subject TEXT NOT NULL,
            status TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL,
            next_attempt_at TEXT NOT NULL,
            last_error TEXT,
            message_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            sent_at TEXT,
            FOREIGN KEY(run_id) REFERENCES scheduled_report_runs(run_id) ON DELETE CASCADE,
            FOREIGN KEY(report_id) REFERENCES report_jobs(report_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS email_delivery_resolutions (
            resolution_id TEXT PRIMARY KEY,
            delivery_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            actor_id TEXT NOT NULL,
            actor_name TEXT NOT NULL,
            evidence_note TEXT NOT NULL,
            before_results_json TEXT NOT NULL,
            after_results_json TEXT NOT NULL,
            resolved_status TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_email_delivery_resolutions
            ON email_delivery_resolutions(delivery_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_email_deliveries_due
            ON email_deliveries(status, next_attempt_at);
        """
    )
    run_columns = {
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info(scheduled_report_runs)"
        ).fetchall()
    }
    if "chart_type" not in run_columns:
        connection.execute(
            "ALTER TABLE scheduled_report_runs "
            "ADD COLUMN chart_type TEXT NOT NULL DEFAULT 'bar_line'"
        )
    delivery_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(email_deliveries)")
    }
    if "recipient_results_json" not in delivery_columns:
        connection.execute(
            "ALTER TABLE email_deliveries ADD COLUMN recipient_results_json TEXT NOT NULL DEFAULT '{}'"
        )
    if "execution_owner" not in delivery_columns:
        connection.execute("ALTER TABLE email_deliveries ADD COLUMN execution_owner TEXT")
    connection.execute("PRAGMA optimize")
