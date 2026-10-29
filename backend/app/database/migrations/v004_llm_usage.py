"""Baseline adoption: preserve existing schema/data upgrades; append future migrations instead of editing."""
from datetime import datetime, timezone
from app.database.migrations.runner import execute_script


def upgrade(connection):
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS llm_usage_totals (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            call_count INTEGER NOT NULL DEFAULT 0,
            metered_count INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    connection.execute("INSERT OR IGNORE INTO llm_usage_totals(id) VALUES (1)")
    execute_script(connection, """
        CREATE TABLE IF NOT EXISTS llm_usage_calls (
            id INTEGER PRIMARY KEY,
            occurred_at TEXT NOT NULL,
            usage_date TEXT NOT NULL,
            scene TEXT NOT NULL,
            model TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('responded', 'failed')),
            duration_ms INTEGER NOT NULL,
            input_tokens INTEGER,
            output_tokens INTEGER,
            total_tokens INTEGER,
            cached_tokens INTEGER,
            reasoning_tokens INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_llm_usage_calls_date
            ON llm_usage_calls(usage_date, id);
        CREATE TABLE IF NOT EXISTS llm_usage_tracking (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            started_at TEXT NOT NULL
        );
    """)
    connection.execute(
        "INSERT OR IGNORE INTO llm_usage_tracking VALUES (1, ?)",
        (datetime.now(timezone.utc).isoformat(timespec="seconds"),),
    )
