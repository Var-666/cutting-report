from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from app.database.sqlite import SQLiteDatabase, SQLiteRepository, execution_owner, require_worker_recovery
from app.database.device_migrations import rebuild_table


class MetadataRepository(SQLiteRepository):
    def __init__(
        self,
        database: Path | SQLiteDatabase,
        *,
        connection: sqlite3.Connection | None = None,
        device_id: str | None = None,
    ) -> None:
        super().__init__(database, connection=connection)
        self.device_id = device_id

    def initialize(self) -> None:
        self.database.initialize()
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS report_jobs (
                    report_id TEXT PRIMARY KEY,
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
                    PRIMARY KEY(report_type, report_date)
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
                ("device_id", "TEXT NOT NULL DEFAULT 'Cutter01'"),
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

        with self.connect() as connection:
            columns = {row['name'] for row in connection.execute('PRAGMA table_info(analysis_source_versions)')}
            if 'device_id' not in columns:
                connection.execute("ALTER TABLE analysis_source_versions ADD COLUMN device_id TEXT NOT NULL DEFAULT 'Cutter01'")
        rebuild_table(self.database, 'analysis_source_versions', lambda sql: sql.replace(
            'PRIMARY KEY(report_type, report_date)', 'PRIMARY KEY(report_type, report_date, device_id)'))

        with self.connect() as connection:
            columns = {row['name'] for row in connection.execute('PRAGMA table_info(report_jobs)')}
            if 'execution_owner' not in columns:
                connection.execute('ALTER TABLE report_jobs ADD COLUMN execution_owner TEXT')
            connection.execute("""CREATE TABLE IF NOT EXISTS analysis_generations (
                cache_key TEXT PRIMARY KEY, attempt_id TEXT NOT NULL, status TEXT NOT NULL,
                updated_at REAL NOT NULL, error_type TEXT, error_message TEXT
            )""")

    def generation(self, cache_key: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM analysis_generations WHERE cache_key=?", (cache_key,)).fetchone()
            return dict(row) if row else None

    def start_generation(self, cache_key: str, attempt_id: str, now: float) -> None:
        with self.connect() as connection:
            connection.execute("""INSERT INTO analysis_generations VALUES (?, ?, 'running', ?, NULL, NULL)
                ON CONFLICT(cache_key) DO UPDATE SET attempt_id=excluded.attempt_id,
                    status='running', updated_at=excluded.updated_at, error_type=NULL, error_message=NULL""",
                (cache_key, attempt_id, now))

    def finish_generation(self, cache_key: str, attempt_id: str, status: str, now: float,
                          error_type: str | None = None, error_message: str | None = None) -> None:
        with self.connect() as connection:
            connection.execute("""UPDATE analysis_generations SET status=?, updated_at=?, error_type=?, error_message=?
                WHERE cache_key=? AND attempt_id=?""", (status, now, error_type, error_message, cache_key, attempt_id))

    def queue_observation(self) -> dict:
        with self.connect() as connection:
            row = connection.execute("""
                SELECT COUNT(CASE WHEN status='pending' THEN 1 END) AS queued_count,
                    MIN(CASE WHEN status='pending' THEN created_at END) AS oldest_queued_at,
                    COUNT(CASE WHEN status IN ('querying','calculating','analyzing','rendering')
                        THEN 1 END) AS running_count
                FROM report_jobs WHERE status IN
                    ('pending','querying','calculating','analyzing','rendering')
            """).fetchone()
        return dict(row)

    def recover_interrupted_jobs(self) -> int:
        with self.connect() as connection:
            self.begin_immediate(connection)
            require_worker_recovery(connection)
            cursor = connection.execute(
                """
                UPDATE report_jobs
                SET status = 'pending', stage_message = '服务已恢复，等待重新生成', started_at = NULL, execution_owner = NULL
                WHERE status IN ('querying', 'calculating', 'analyzing', 'rendering')
                """
            )
            return cursor.rowcount

    def create_job(
        self,
        *,
        report_id: str,
        report_date: str,
        project_code: str | None,
        ship_no: str | None,
        request_id: str,
        created_at: str,
        report_type: str = "workshop",
        definition_id: str | None = None,
        report_title: str | None = None,
        parameters: dict[str, Any] | None = None,
        period_start: str | None = None,
        period_end: str | None = None,
        device_id: str | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO report_jobs (
                    report_id, report_type, status, stage_message, report_date, project_code,
                    ship_no, created_at, request_id, definition_id, report_title, parameters_json,
                    period_start, period_end, device_id
                ) VALUES (?, ?, 'pending', '等待生成', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report_id,
                    report_type,
                    report_date,
                    project_code,
                    ship_no,
                    created_at,
                    request_id,
                    definition_id,
                    report_title,
                    json.dumps(parameters or {}, ensure_ascii=False),
                    period_start,
                    period_end,
                    device_id or self.device_id or "Cutter01",
                ),
            )

    def claim_next_job(self, started_at: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            self.begin_immediate(connection)
            row = connection.execute(
                """
                SELECT * FROM report_jobs
                WHERE status = 'pending'
                ORDER BY created_at ASC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            updated = connection.execute(
                """
                UPDATE report_jobs
                SET status = 'querying', stage_message = '正在查询生产数据', started_at = ?, execution_owner = ?
                WHERE report_id = ? AND status = 'pending'
                """,
                (started_at, execution_owner(), row["report_id"]),
            )
            if updated.rowcount != 1:
                return None
            return {**dict(row), "execution_owner": execution_owner()}

    def update_status(
        self,
        report_id: str,
        status: str,
        stage_message: str,
        *,
        ai_status: str | None = None,
        finished_at: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE report_jobs
                SET status = ?, stage_message = ?,
                    ai_status = COALESCE(?, ai_status),
                    finished_at = COALESCE(?, finished_at),
                    error_code = ?, error_message = ?
                WHERE report_id = ?
                """,
                (
                    status,
                    stage_message,
                    ai_status,
                    finished_at,
                    error_code,
                    error_message,
                    report_id,
                ),
            )

    def save_report(
        self,
        *,
        report_id: str,
        filename: str,
        relative_path: str,
        file_size: int,
        sha256: str,
        task_count: int,
        generated_at: str,
        system_version: str,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO reports (
                    report_id, filename, relative_path, file_size, sha256,
                    task_count, generated_at, system_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report_id,
                    filename,
                    relative_path,
                    file_size,
                    sha256,
                    task_count,
                    generated_at,
                    system_version,
                ),
            )

    def get_job(self, report_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT j.*, r.filename, r.relative_path, r.file_size, r.task_count,
                       r.generated_at, r.sha256
                FROM report_jobs j
                LEFT JOIN reports r ON r.report_id = j.report_id
                WHERE j.report_id = ? AND (? IS NULL OR j.device_id = ?)
                """,
                (report_id, self.device_id, self.device_id),
            ).fetchone()
            return dict(row) if row else None

    def get_analysis(
        self,
        cache_key: str,
        *,
        report_type: str,
        report_date: str,
        data_version: str,
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT result_json
                FROM report_analyses
                WHERE cache_key = ?
                  AND report_type = ?
                  AND report_date = ?
                  AND COALESCE(data_version, input_hash) = ?
                """,
                (cache_key, report_type, report_date, data_version),
            ).fetchone()
            if row is None:
                return None
            value = json.loads(row["result_json"])
            return value if isinstance(value, dict) else None

    def get_latest_analysis(
        self, *, report_type: str, report_date: str, model_name: str, prompt_version: str,
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT result_json FROM report_analyses
                WHERE display_scope = ? AND report_type = ? AND report_date = ?
                  AND model_name = ? AND prompt_version = ? AND mode = 'model'
                ORDER BY generated_at DESC, rowid DESC LIMIT 1
                """,
                (self.device_id or "Cutter01", report_type, report_date, model_name, prompt_version),
            ).fetchone()
            value = json.loads(row["result_json"]) if row else None
            return value if isinstance(value, dict) else None

    def get_analysis_source_version(
        self,
        *,
        report_type: str,
        report_date: str,
    ) -> dict[str, str] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT data_version, updated_at
                FROM analysis_source_versions
                WHERE report_type = ? AND report_date = ? AND device_id = ?
                """,
                (report_type, report_date, self.device_id or "Cutter01"),
            ).fetchone()
            if row is None:
                return None
            return {
                "data_version": row["data_version"],
                "updated_at": row["updated_at"],
            }

    def save_analysis_source_version(
        self,
        *,
        report_type: str,
        report_date: str,
        data_version: str,
        updated_at: str,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO analysis_source_versions (
                    report_type, report_date, data_version, updated_at, device_id
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(report_type, report_date, device_id) DO UPDATE SET
                    data_version = excluded.data_version,
                    updated_at = excluded.updated_at
                """,
                (report_type, report_date, data_version, updated_at, self.device_id or "Cutter01"),
            )

    def save_analysis(
        self,
        *,
        cache_key: str,
        report_type: str,
        report_date: str,
        input_hash: str,
        data_version: str,
        status: str,
        mode: str,
        model_name: str | None,
        prompt_version: str,
        result: dict[str, Any],
        generated_at: str,
        publish_latest: bool = False,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO report_analyses (
                    cache_key, report_type, report_date, input_hash, data_version, status, mode,
                    model_name, prompt_version, result_json, generated_at, display_scope
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    input_hash = excluded.input_hash,
                    data_version = excluded.data_version,
                    status = excluded.status,
                    mode = excluded.mode,
                    model_name = excluded.model_name,
                    prompt_version = excluded.prompt_version,
                    result_json = excluded.result_json,
                    generated_at = excluded.generated_at,
                    display_scope = COALESCE(excluded.display_scope, report_analyses.display_scope)
                WHERE excluded.generated_at >= report_analyses.generated_at
                """,
                (
                    cache_key,
                    report_type,
                    report_date,
                    input_hash,
                    data_version,
                    status,
                    mode,
                    model_name,
                    prompt_version,
                    json.dumps(result, ensure_ascii=False),
                    generated_at,
                    (self.device_id or "Cutter01") if publish_latest else None,
                ),
            )

    def list_reports(
        self,
        *,
        page: int,
        page_size: int,
        report_date: str | None = None,
        project_code: str | None = None,
        ship_no: str | None = None,
        report_type: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        filters: list[str] = []
        parameters: list[Any] = []
        if self.device_id is not None:
            filters.append("j.device_id = ?")
            parameters.append(self.device_id)
        for column, value in (
            ("j.report_date", report_date),
            ("j.project_code", project_code),
            ("j.ship_no", ship_no),
            ("j.report_type", report_type),
        ):
            if value:
                filters.append(f"{column} = ?")
                parameters.append(value)
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        with self.connect() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM report_jobs j {where}", parameters
            ).fetchone()[0]
            rows = connection.execute(
                f"""
                SELECT j.*, r.filename, r.file_size, r.task_count, r.generated_at
                FROM report_jobs j
                LEFT JOIN reports r ON r.report_id = j.report_id
                {where}
                ORDER BY j.created_at DESC
                LIMIT ? OFFSET ?
                """,
                [*parameters, page_size, (page - 1) * page_size],
            ).fetchall()
            return [dict(row) for row in rows], total
