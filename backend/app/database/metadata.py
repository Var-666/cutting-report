from __future__ import annotations

import json
from psycopg import Connection

from typing import Any

from app.database.postgres import PostgresDatabase, PostgresRepository, execution_owner, require_role_recovery, ExecutionLeaseLost, recovery_owners


class MetadataRepository(PostgresRepository):
    def __init__(
        self,
        database: PostgresDatabase,
        *,
        connection: Connection | None = None,
        device_id: str | None = None,
        analysis_scope: str | None = None,
    ) -> None:
        super().__init__(database, connection=connection)
        self.device_id = device_id
        self._analysis_scope = analysis_scope

    def require_device_id(self, device_id: str | None = None) -> str:
        selected = device_id if device_id is not None else self.device_id
        if not selected or (self.device_id is not None and selected != self.device_id):
            raise ValueError("必须指定一致的设备上下文")
        return selected

    @property
    def analysis_scope(self) -> str:
        return self._analysis_scope or self.require_device_id()


    def generation(self, cache_key: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM analysis_generations WHERE cache_key=%s", (cache_key,)).fetchone()
            return dict(row) if row else None

    def start_generation(self, cache_key: str, attempt_id: str, now: float) -> None:
        with self.connect() as connection:
            connection.execute("""INSERT INTO analysis_generations VALUES (%s, %s, 'running', %s, NULL, NULL)
                ON CONFLICT(cache_key) DO UPDATE SET attempt_id=excluded.attempt_id,
                    status='running', updated_at=excluded.updated_at, error_type=NULL, error_message=NULL""",
                (cache_key, attempt_id, now))

    def finish_generation(self, cache_key: str, attempt_id: str, status: str, now: float,
                          error_type: str | None = None, error_message: str | None = None) -> None:
        with self.connect() as connection:
            connection.execute("""UPDATE analysis_generations SET status=%s, updated_at=%s, error_type=%s, error_message=%s
                WHERE cache_key=%s AND attempt_id=%s""", (status, now, error_type, error_message, cache_key, attempt_id))

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
            require_role_recovery(connection, 'worker')
            candidates = connection.execute("""SELECT report_id,execution_owner FROM report_jobs
                WHERE status IN ('querying','calculating','analyzing','rendering') AND NOT EXISTS (
                    SELECT 1 FROM process_leases lease WHERE lease.owner=report_jobs.execution_owner
                    AND lease.expires_at>EXTRACT(EPOCH FROM clock_timestamp())) FOR UPDATE SKIP LOCKED""").fetchall()
            if not candidates:
                return 0
            allowed = recovery_owners(connection, [row['execution_owner'] for row in candidates])
            candidates = [row for row in candidates if row['execution_owner'] in allowed]
            cursor = connection.execute(
                """
                UPDATE report_jobs
                SET status = 'pending', stage_message = '服务已恢复，等待重新生成', started_at = NULL, execution_owner = NULL
                WHERE report_id=ANY(%s) AND status IN ('querying', 'calculating', 'analyzing', 'rendering')
                  AND NOT EXISTS (SELECT 1 FROM process_leases lease
                      WHERE lease.owner=report_jobs.execution_owner
                      AND lease.expires_at>EXTRACT(EPOCH FROM clock_timestamp()))
                """, ([r['report_id'] for r in candidates],)
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
                ) VALUES (%s, %s, 'pending', '等待生成', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                    self.require_device_id(device_id),
                ),
            )

    def claim_next_job(self, started_at: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM report_jobs
                WHERE status = 'pending' AND (%s::text IS NULL OR device_id = %s)
                ORDER BY created_at ASC
                LIMIT 1 FOR UPDATE SKIP LOCKED
                """, (self.device_id, self.device_id),
            ).fetchone()
            if row is None:
                return None
            updated = connection.execute(
                """
                UPDATE report_jobs
                SET status = 'querying', stage_message = '正在查询生产数据', started_at = %s, execution_owner = %s
                WHERE report_id = %s AND status = 'pending'
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
            updated = connection.execute(
                """
                UPDATE report_jobs
                SET status = %s, stage_message = %s,
                    ai_status = COALESCE(%s, ai_status),
                    finished_at = COALESCE(%s, finished_at),
                    error_code = %s, error_message = %s
                WHERE report_id = %s AND (%s::text IS NULL OR device_id = %s)
                  AND (%s::text IS NULL OR (execution_owner=%s
                      AND status IN ('querying','calculating','analyzing','rendering')))
                """,
                (
                    status,
                    stage_message,
                    ai_status,
                    finished_at,
                    error_code,
                    error_message,
                    report_id, self.device_id, self.device_id, execution_owner(), execution_owner(),
                ),
            )
            if execution_owner() is not None and updated.rowcount != 1:
                raise ExecutionLeaseLost('报表任务已不属于当前 Consumer，拒绝更新状态')

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
        if self.device_id is not None and self.get_job(report_id) is None:
            raise ValueError("报告不属于当前设备")
        with self.connect() as connection:
            if execution_owner() is not None:
                job = connection.execute("""SELECT report_id FROM report_jobs WHERE report_id=%s
                    AND execution_owner=%s AND status='rendering' FOR UPDATE""",
                    (report_id, execution_owner())).fetchone()
                if job is None:
                    raise ExecutionLeaseLost('报表领取代际已失效，拒绝发布文件')
            connection.execute(
                """
                INSERT INTO reports (
                    report_id, filename, relative_path, file_size, sha256,
                    task_count, generated_at, system_version
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
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
                WHERE j.report_id = %s AND (%s::text IS NULL OR j.device_id = %s)
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
                WHERE cache_key = %s
                  AND report_type = %s
                  AND report_date = %s
                  AND COALESCE(data_version, input_hash) = %s
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
                WHERE display_scope = %s AND report_type = %s AND report_date = %s
                  AND model_name = %s AND prompt_version = %s AND mode = 'model'
                ORDER BY generated_at DESC, insertion_order DESC LIMIT 1
                """,
                (self.analysis_scope, report_type, report_date, model_name, prompt_version),
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
                WHERE report_type = %s AND report_date = %s AND device_id = %s
                """,
                (report_type, report_date, self.analysis_scope),
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
                ) VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT(report_type, report_date, device_id) DO UPDATE SET
                    data_version = excluded.data_version,
                    updated_at = excluded.updated_at
                """,
                (report_type, report_date, data_version, updated_at, self.analysis_scope),
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
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                    self.analysis_scope if publish_latest else None,
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
            filters.append("j.device_id = %s")
            parameters.append(self.device_id)
        for column, value in (
            ("j.report_date", report_date),
            ("j.project_code", project_code),
            ("j.ship_no", ship_no),
            ("j.report_type", report_type),
        ):
            if value:
                filters.append(f"{column} = %s")
                parameters.append(value)
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        with self.connect() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) AS count FROM report_jobs j {where}", parameters
            ).fetchone()['count']
            rows = connection.execute(
                f"""
                SELECT j.*, r.filename, r.file_size, r.task_count, r.generated_at
                FROM report_jobs j
                LEFT JOIN reports r ON r.report_id = j.report_id
                {where}
                ORDER BY j.created_at DESC
                LIMIT %s OFFSET %s
                """,
                [*parameters, page_size, (page - 1) * page_size],
            ).fetchall()
            return [dict(row) for row in rows], total
