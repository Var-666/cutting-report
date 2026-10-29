"""Baseline adoption: preserve existing schema/data upgrades; append future migrations instead of editing."""
from app.database.migrations.runner import execute_script


def upgrade(connection):
    execute_script(connection, """
        CREATE TABLE IF NOT EXISTS process_leases (
            name TEXT PRIMARY KEY, owner TEXT NOT NULL,
            expires_at REAL NOT NULL, heartbeat_at REAL NOT NULL,
            status_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS background_tasks (
            task_id TEXT PRIMARY KEY, kind TEXT NOT NULL, device_id TEXT NOT NULL,
            payload TEXT NOT NULL, dedup_key TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL, owner TEXT, created_at REAL NOT NULL,
            updated_at REAL NOT NULL, next_attempt REAL NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_background_tasks_ready
            ON background_tasks(status, next_attempt, created_at);
        CREATE TABLE IF NOT EXISTS background_schedules (
            schedule_key TEXT PRIMARY KEY, next_due REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS llm_waiters (
            waiter_id TEXT PRIMARY KEY, expires_at REAL NOT NULL
        );
    """)
