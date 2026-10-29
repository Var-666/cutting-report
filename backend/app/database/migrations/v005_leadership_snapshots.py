"""Baseline adoption: preserve existing schema/data upgrades; append future migrations instead of editing."""
from app.database.migrations.runner import execute_script
from app.database.device_migrations import _rebuild


def upgrade(connection):
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS leadership_month_snapshots (
            report_month TEXT PRIMARY KEY,
            daily_json TEXT NOT NULL,
            project_json TEXT NOT NULL,
            total_json TEXT NOT NULL,
            data_as_of TEXT NOT NULL,
            refreshed_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            query_duration_ms INTEGER NOT NULL DEFAULT 0,
            source_fingerprint TEXT NOT NULL,
            snapshot_version INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    _rebuild(connection, 'leadership_month_snapshots', lambda sql: sql.replace(
        'report_month TEXT PRIMARY KEY', 'report_month TEXT NOT NULL').replace(
        'snapshot_version INTEGER NOT NULL DEFAULT 1',
        'snapshot_version INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(report_month, source_fingerprint)') if 'report_month TEXT PRIMARY KEY' in sql else sql)
