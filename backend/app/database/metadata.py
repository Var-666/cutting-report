from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from app.database.sqlite import SQLiteDatabase, SQLiteRepository


TERMINAL_STATUSES = {"completed", "completed_with_warnings", "failed"}


class MetadataRepository(SQLiteRepository):
    def __init__(
        self,
        database: Path | SQLiteDatabase,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        super().__init__(database, connection=connection)

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
                    status TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    model_name TEXT,
                    prompt_version TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    generated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_report_analyses_lookup
                    ON report_analyses(report_type, report_date, generated_at DESC);
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
            ):
                if column not in columns:
                    connection.execute(
                        f"ALTER TABLE report_jobs ADD COLUMN {column} {definition}"
                    )

    def recover_interrupted_jobs(self) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE report_jobs
                SET status = 'pending', stage_message = '服务已恢复，等待重新生成', started_at = NULL
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
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO report_jobs (
                    report_id, report_type, status, stage_message, report_date, project_code,
                    ship_no, created_at, request_id, definition_id, report_title, parameters_json
                ) VALUES (?, ?, 'pending', '等待生成', ?, ?, ?, ?, ?, ?, ?, ?)
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
                SET status = 'querying', stage_message = '正在查询生产数据', started_at = ?
                WHERE report_id = ? AND status = 'pending'
                """,
                (started_at, row["report_id"]),
            )
            if updated.rowcount != 1:
                return None
            return dict(row)

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
                WHERE j.report_id = ?
                """,
                (report_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_analysis(self, cache_key: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT result_json FROM report_analyses WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
            if row is None:
                return None
            value = json.loads(row["result_json"])
            return value if isinstance(value, dict) else None

    def save_analysis(
        self,
        *,
        cache_key: str,
        report_type: str,
        report_date: str,
        input_hash: str,
        status: str,
        mode: str,
        model_name: str | None,
        prompt_version: str,
        result: dict[str, Any],
        generated_at: str,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO report_analyses (
                    cache_key, report_type, report_date, input_hash, status, mode,
                    model_name, prompt_version, result_json, generated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    status = excluded.status,
                    mode = excluded.mode,
                    model_name = excluded.model_name,
                    prompt_version = excluded.prompt_version,
                    result_json = excluded.result_json,
                    generated_at = excluded.generated_at
                """,
                (
                    cache_key,
                    report_type,
                    report_date,
                    input_hash,
                    status,
                    mode,
                    model_name,
                    prompt_version,
                    json.dumps(result, ensure_ascii=False),
                    generated_at,
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
