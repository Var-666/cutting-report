"""Baseline adoption: preserve existing schema/data upgrades; append future migrations instead of editing."""
from app.database.migrations.runner import execute_script


def upgrade(connection):
    execute_script(connection,
        """
        CREATE TABLE IF NOT EXISTS cutting_dispatches (
            dispatch_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            project_name TEXT,
            catalog_id TEXT,
            catalog_title TEXT,
            version_id TEXT,
            version TEXT,
            document_no TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            machine_ip TEXT NOT NULL,
            material TEXT,
            thickness REAL,
            quantity INTEGER NOT NULL CHECK(quantity > 0),
            status TEXT NOT NULL CHECK(status IN (
                'waiting_source', 'ready', 'downloading', 'downloaded',
                'pushing', 'sent', 'download_failed', 'push_failed', 'cancelled'
            )),
            source_mode TEXT NOT NULL,
            is_simulation INTEGER NOT NULL DEFAULT 0,
            remote_task_id TEXT,
            last_error TEXT,
            created_by_id TEXT NOT NULL,
            created_by_name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            downloaded_at TEXT,
            sent_at TEXT,
            FOREIGN KEY(created_by_id) REFERENCES users(user_id)
        );
        CREATE INDEX IF NOT EXISTS idx_cutting_dispatches_created
            ON cutting_dispatches(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_cutting_dispatches_status
            ON cutting_dispatches(status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_cutting_dispatches_file_machine
            ON cutting_dispatches(document_id, version_id, machine_ip);
        """
    )
    existing = {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(cutting_dispatches)"
        ).fetchall()
    }
    migrations = {
        "ship_no": "ALTER TABLE cutting_dispatches ADD COLUMN ship_no TEXT",
        "segment_name": "ALTER TABLE cutting_dispatches ADD COLUMN segment_name TEXT",
        "task_identifier": "ALTER TABLE cutting_dispatches ADD COLUMN task_identifier TEXT",
        "download_id": "ALTER TABLE cutting_dispatches ADD COLUMN download_id TEXT",
        "file_size": "ALTER TABLE cutting_dispatches ADD COLUMN file_size INTEGER",
        "file_sha256": "ALTER TABLE cutting_dispatches ADD COLUMN file_sha256 TEXT",
        "stage": "ALTER TABLE cutting_dispatches ADD COLUMN stage TEXT",
        "attempt_count": "ALTER TABLE cutting_dispatches ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0",
        "attempted_at": "ALTER TABLE cutting_dispatches ADD COLUMN attempted_at TEXT",
    }
    for column, statement in migrations.items():
        if column not in existing:
            connection.execute(statement)
    connection.execute(
        """
        UPDATE cutting_dispatches
        SET task_identifier = COALESCE(task_identifier, dispatch_id),
            stage = COALESCE(stage,
                CASE status
                    WHEN 'sent' THEN 'complete'
                    WHEN 'downloaded' THEN 'file_ready'
                    WHEN 'cancelled' THEN 'cancelled'
                    ELSE 'created'
                END)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS cutting_file_dispatch_status (
            document_id TEXT NOT NULL,
            version_id TEXT NOT NULL,
            dispatch_id TEXT NOT NULL,
            machine_ip TEXT NOT NULL,
            status TEXT NOT NULL,
            stage TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            sent_at TEXT,
            PRIMARY KEY (document_id, version_id)
        )
        """
    )
    connection.execute("PRAGMA optimize")
