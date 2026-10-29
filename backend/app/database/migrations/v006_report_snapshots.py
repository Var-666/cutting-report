"""Baseline adoption: preserve existing schema/data upgrades; append future migrations instead of editing."""
from app.database.migrations.runner import execute_script


def upgrade(connection):
    connection.execute('''CREATE TABLE IF NOT EXISTS report_snapshots (
        line_id TEXT NOT NULL, report_type TEXT NOT NULL,
        period_start TEXT NOT NULL, period_end TEXT NOT NULL,
        query_fingerprint TEXT NOT NULL, source_fingerprint TEXT NOT NULL,
        snapshot_schema_version INTEGER NOT NULL, data_version TEXT NOT NULL,
        data_as_of TEXT NOT NULL, refreshed_at TEXT NOT NULL, expires_at TEXT NOT NULL,
        payload TEXT NOT NULL,
        PRIMARY KEY (line_id, report_type, period_start, period_end,
                     query_fingerprint, source_fingerprint, snapshot_schema_version))''')
