"""Baseline adoption: preserve existing schema/data upgrades; append future migrations instead of editing."""
from app.database.migrations.runner import execute_script


def upgrade(connection):
    execute_script(connection, """
        CREATE TABLE IF NOT EXISTS cutting_downloads (
            download_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('downloading', 'downloaded', 'failed')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            payload TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_cutting_downloads_created
            ON cutting_downloads(created_at DESC, download_id DESC);
        CREATE INDEX IF NOT EXISTS idx_cutting_downloads_retention
            ON cutting_downloads(status, updated_at, download_id);
    """)
