"""Baseline adoption: preserve existing schema/data upgrades; append future migrations instead of editing."""
from app.database.migrations.runner import execute_script


def upgrade(connection):
    execute_script(connection,
        """
        CREATE TABLE IF NOT EXISTS report_jobs (
            report_id TEXT PRIMARY KEY,
            device_id TEXT NOT NULL,
            report_type TEXT NOT NULL DEFAULT 'workshop',
            status TEXT NOT NULL,
            stage_message TEXT NOT NULL,
            report_date TEXT NOT NULL,
            project_code TEXT,
            ship_no TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            request_id TEXT NOT NULL,
            ai_status TEXT,
            error_code TEXT,
            error_message TEXT,
            definition_id TEXT,
            report_title TEXT,
            parameters_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_report_jobs_created_at
            ON report_jobs(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_report_jobs_status
            ON report_jobs(status, created_at);

        CREATE TABLE IF NOT EXISTS reports (
            report_id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            relative_path TEXT NOT NULL UNIQUE,
            file_size INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            task_count INTEGER NOT NULL,
            generated_at TEXT NOT NULL,
            system_version TEXT NOT NULL,
            FOREIGN KEY(report_id) REFERENCES report_jobs(report_id)
        );

        CREATE TABLE IF NOT EXISTS report_analyses (
            cache_key TEXT PRIMARY KEY,
            report_type TEXT NOT NULL,
            report_date TEXT NOT NULL,
            input_hash TEXT NOT NULL,
            data_version TEXT NOT NULL,
            status TEXT NOT NULL,
            mode TEXT NOT NULL,
            model_name TEXT,
            prompt_version TEXT NOT NULL,
            result_json TEXT NOT NULL,
            generated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_report_analyses_lookup
            ON report_analyses(report_type, report_date, generated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_report_analyses_retention
            ON report_analyses(generated_at, cache_key);

        CREATE TABLE IF NOT EXISTS analysis_source_versions (
            report_type TEXT NOT NULL,
            report_date TEXT NOT NULL,
            data_version TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            device_id TEXT NOT NULL,
            PRIMARY KEY(report_type, report_date, device_id)
        );
        """
    )
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(report_jobs)").fetchall()
    }
    if "report_type" not in columns:
        connection.execute(
            "ALTER TABLE report_jobs ADD COLUMN report_type TEXT NOT NULL DEFAULT 'workshop'"
        )
    for column, definition in (
        ("definition_id", "TEXT"),
        ("report_title", "TEXT"),
        ("parameters_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("period_start", "TEXT"),
        ("period_end", "TEXT"),
    ):
        if column not in columns:
            connection.execute(
                f"ALTER TABLE report_jobs ADD COLUMN {column} {definition}"
            )
    analysis_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(report_analyses)").fetchall()
    }
    if "data_version" not in analysis_columns:
        connection.execute(
            "ALTER TABLE report_analyses ADD COLUMN data_version TEXT"
        )
        connection.execute(
            "UPDATE report_analyses SET data_version = input_hash WHERE data_version IS NULL"
        )
    if "display_scope" not in analysis_columns:
        connection.execute("ALTER TABLE report_analyses ADD COLUMN display_scope TEXT")
    connection.execute(
        """CREATE INDEX IF NOT EXISTS idx_report_analyses_display
        ON report_analyses(display_scope, report_type, report_date, generated_at DESC)"""
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_report_analyses_version
        ON report_analyses(report_type, report_date, data_version, generated_at DESC)
        """
    )
    columns = {row['name'] for row in connection.execute('PRAGMA table_info(report_jobs)')}
    if 'execution_owner' not in columns:
        connection.execute('ALTER TABLE report_jobs ADD COLUMN execution_owner TEXT')
    connection.execute("""CREATE TABLE IF NOT EXISTS analysis_generations (
        cache_key TEXT PRIMARY KEY, attempt_id TEXT NOT NULL, status TEXT NOT NULL,
        updated_at REAL NOT NULL, error_type TEXT, error_message TEXT
    )""")
