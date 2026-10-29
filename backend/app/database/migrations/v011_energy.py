"""Baseline adoption: preserve existing schema/data upgrades; append future migrations instead of editing."""
from app.database.migrations.runner import execute_script


def upgrade(connection):
    connection.execute("""
        CREATE TABLE IF NOT EXISTS energy_daily_snapshots (
            source_key TEXT NOT NULL,
            report_date TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            report_json TEXT NOT NULL,
            PRIMARY KEY (source_key, report_date)
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS energy_history_queue (
            source_key TEXT NOT NULL,
            report_month TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            claim_token TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            next_attempt_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_error_code TEXT,
            PRIMARY KEY (source_key, report_month)
        )
    """)
    columns = {row['name'] for row in connection.execute('PRAGMA table_info(energy_history_queue)')}
    if 'execution_owner' not in columns:
        connection.execute('ALTER TABLE energy_history_queue ADD COLUMN execution_owner TEXT')
