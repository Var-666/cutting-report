"""Baseline adoption: preserve existing schema/data upgrades; append future migrations instead of editing."""
from app.database.migrations.runner import execute_script


def upgrade(connection):
    execute_script(connection,
        """
        CREATE TABLE IF NOT EXISTS retention_file_deletions (
            relative_path TEXT PRIMARY KEY,
            category TEXT NOT NULL,
            queued_at TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_retention_file_deletions_queued
            ON retention_file_deletions(queued_at, relative_path);
        """
    )
