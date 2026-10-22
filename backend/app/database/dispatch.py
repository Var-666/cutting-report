from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from app.database.sqlite import SQLiteDatabase, SQLiteRepository


DISPATCH_STATUSES = {
    "waiting_source",
    "ready",
    "downloading",
    "downloaded",
    "pushing",
    "sent",
    "download_failed",
    "push_failed",
    "cancelled",
}

ACTIVE_DISPATCH_STATUSES = {
    "waiting_source",
    "ready",
    "downloading",
    "downloaded",
    "pushing",
}

# Only an explicit, pre-effect upload rejection can release the file/machine
# identity. Legacy and uncertain failures must remain visible until reconciled.
UNRESOLVED_DISPATCH_SQL = """(
    status = 'push_failed' AND (
        COALESCE(stage, '') != 'upload_failed' OR remote_task_id IS NOT NULL
    )
)"""


def dispatch_retry_safe(item: dict[str, Any]) -> bool:
    if item["status"] != "push_failed":
        return False
    return item.get("stage") in (
        {"assign_failed", "uploaded"} if item.get("remote_task_id") else {"upload_failed"}
    )


class DispatchRepository(SQLiteRepository):
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
            # No foreign key to history: retention must not erase file status.
            self._sync_file_summaries(connection)
            connection.execute("PRAGMA optimize")

    @staticmethod
    def _sync_file_summaries(connection: sqlite3.Connection, dispatch_id: str | None = None) -> None:
        where = "WHERE dispatch_id = ?" if dispatch_id is not None else ""
        connection.execute(
            f"""
            INSERT INTO cutting_file_dispatch_status (
                document_id, version_id, dispatch_id, machine_ip, status, stage,
                created_at, updated_at, sent_at
            )
            SELECT document_id, COALESCE(version_id, ''), dispatch_id, machine_ip,
                   status, COALESCE(stage, 'created'), created_at, updated_at, sent_at
            FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY document_id, COALESCE(version_id, '')
                    ORDER BY created_at DESC, updated_at DESC, dispatch_id DESC
                ) AS file_rank
                FROM cutting_dispatches
                {where}
            ) WHERE file_rank = 1
            ON CONFLICT(document_id, version_id) DO UPDATE SET
                dispatch_id = excluded.dispatch_id,
                machine_ip = excluded.machine_ip,
                status = excluded.status,
                stage = excluded.stage,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at,
                sent_at = excluded.sent_at
            WHERE excluded.dispatch_id = cutting_file_dispatch_status.dispatch_id
               OR (excluded.created_at, excluded.updated_at, excluded.dispatch_id) >
                  (cutting_file_dispatch_status.created_at,
                   cutting_file_dispatch_status.updated_at, cutting_file_dispatch_status.dispatch_id)
            """,
            (dispatch_id,) if dispatch_id is not None else (),
        )

    @staticmethod
    def _decode(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        item["is_simulation"] = bool(item["is_simulation"])
        return item

    @staticmethod
    def _active_row(
        connection: sqlite3.Connection,
        document_id: str,
        version_id: str | None,
        machine_ip: str,
        *,
        exclude_dispatch_id: str | None = None,
    ) -> sqlite3.Row | None:
        placeholders = ", ".join("?" for _ in ACTIVE_DISPATCH_STATUSES)
        return connection.execute(
            f"""
            SELECT * FROM cutting_dispatches
            WHERE document_id = ?
              AND COALESCE(version_id, '') = COALESCE(?, '')
              AND machine_ip = ?
              AND (? IS NULL OR dispatch_id != ?)
              AND (status IN ({placeholders}) OR {UNRESOLVED_DISPATCH_SQL})
            ORDER BY created_at DESC, dispatch_id DESC
            LIMIT 1
            """,
            (document_id, version_id, machine_ip, exclude_dispatch_id,
             exclude_dispatch_id, *sorted(ACTIVE_DISPATCH_STATUSES)),
        ).fetchone()

    @staticmethod
    def _insert(
        connection: sqlite3.Connection,
        dispatch_id: str,
        data: dict[str, Any],
        *,
        user: dict,
        now: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO cutting_dispatches (
                dispatch_id, document_id, project_id, project_name,
                catalog_id, catalog_title, version_id, version, document_no,
                relative_path, machine_ip, material, thickness, quantity,
                status, source_mode, is_simulation, created_by_id,
                created_by_name, created_at, updated_at, task_identifier,
                stage, attempt_count, download_id, file_size, file_sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                dispatch_id,
                data["document_id"],
                data["project_id"],
                data.get("project_name"),
                data.get("catalog_id"),
                data.get("catalog_title"),
                data.get("version_id"),
                data.get("version"),
                data["document_no"],
                data["relative_path"],
                data["machine_ip"],
                data.get("material"),
                data.get("thickness"),
                data["quantity"],
                data["status"],
                data["source_mode"],
                int(data.get("is_simulation", False)),
                user["user_id"],
                user["display_name"],
                now,
                now,
                dispatch_id,
                data.get("stage", "created"),
                0,
                data.get("download_id"),
                data.get("file_size"),
                data.get("file_sha256"),
            ),
        )
        DispatchRepository._sync_file_summaries(connection, dispatch_id)

    def create(self, data: dict[str, Any], *, user: dict, now: str) -> dict[str, Any]:
        if data["status"] not in DISPATCH_STATUSES:
            raise ValueError("不支持的派发状态")
        dispatch_id = f"dsp_{uuid4().hex[:18]}"
        with self.connect() as connection:
            self._insert(connection, dispatch_id, data, user=user, now=now)
        item = self.get(dispatch_id)
        if item is None:
            raise RuntimeError("派发任务保存失败")
        return item

    def create_unless_active(
        self,
        data: dict[str, Any],
        *,
        user: dict,
        now: str,
    ) -> tuple[dict[str, Any], bool]:
        """Atomically return an active duplicate or create a new dispatch."""
        if data["status"] not in DISPATCH_STATUSES:
            raise ValueError("不支持的派发状态")
        with self.connect() as connection:
            # Serializing this short decision transaction closes the race between
            # checking for an active task and inserting its replacement.
            self.begin_immediate(connection)
            existing = self._active_row(
                connection,
                data["document_id"],
                data.get("version_id"),
                data["machine_ip"],
            )
            if existing is not None:
                return self._decode(existing), False

            dispatch_id = f"dsp_{uuid4().hex[:18]}"
            self._insert(connection, dispatch_id, data, user=user, now=now)
            created = connection.execute(
                "SELECT * FROM cutting_dispatches WHERE dispatch_id = ?",
                (dispatch_id,),
            ).fetchone()
            if created is None:
                raise RuntimeError("派发任务保存失败")
            return self._decode(created), True

    def get(self, dispatch_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM cutting_dispatches WHERE dispatch_id = ?",
                (dispatch_id,),
            ).fetchone()
        return self._decode(row) if row else None

    def list(self, *, page: int, page_size: int) -> tuple[list[dict[str, Any]], int]:
        offset = (page - 1) * page_size
        with self.connect() as connection:
            total = int(
                connection.execute(
                    "SELECT COUNT(*) FROM cutting_dispatches"
                ).fetchone()[0]
            )
            rows = connection.execute(
                """
                SELECT * FROM cutting_dispatches
                ORDER BY created_at DESC, dispatch_id DESC
                LIMIT ? OFFSET ?
                """,
                (page_size, offset),
            ).fetchall()
        return [self._decode(row) for row in rows], total

    def latest_for_files(self, files: Iterable[tuple[str, str]]) -> dict[tuple[str, str], dict[str, Any]]:
        keys = list(dict.fromkeys(files))
        if not keys:
            return {}
        placeholders = ", ".join("(?, ?)" for _ in keys)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                WITH requested(document_id, version_id) AS (VALUES {placeholders})
                SELECT summary.* FROM requested
                JOIN cutting_file_dispatch_status AS summary
                  ON summary.document_id = requested.document_id
                 AND summary.version_id = requested.version_id
                """,
                tuple(value for key in keys for value in key),
            ).fetchall()
        return {(row["document_id"], row["version_id"]): dict(row) for row in rows}

    def transition(
        self,
        dispatch_id: str,
        *,
        expected: Iterable[str],
        status: str,
        now: str,
        last_error: str | None = None,
        remote_task_id: str | None = None,
        stage: str | None = None,
        download_id: str | None = None,
        file_size: int | None = None,
        file_sha256: str | None = None,
        increment_attempt: bool = False,
        expected_stage: str | None = None,
        expected_attempt_count: int | None = None,
        claim_exclusive: bool = False,
    ) -> dict[str, Any] | None:
        if status not in DISPATCH_STATUSES:
            raise ValueError("不支持的派发状态")
        expected_statuses = sorted(set(expected))
        if not expected_statuses:
            raise ValueError("必须指定允许迁移的原状态")
        placeholders = ", ".join("?" for _ in expected_statuses)
        downloaded_at = now if status == "downloaded" else None
        sent_at = now if status == "sent" else None
        with self.connect() as connection:
            self.begin_immediate(connection)
            if claim_exclusive:
                item = connection.execute(
                    "SELECT * FROM cutting_dispatches WHERE dispatch_id = ?", (dispatch_id,)
                ).fetchone()
                if item is None or self._active_row(
                    connection, item["document_id"], item["version_id"], item["machine_ip"],
                    exclude_dispatch_id=dispatch_id,
                ) is not None:
                    return None
            cursor = connection.execute(
                f"""
                UPDATE cutting_dispatches
                SET status = ?, updated_at = ?, last_error = ?,
                    remote_task_id = COALESCE(?, remote_task_id),
                    stage = COALESCE(?, stage),
                    download_id = COALESCE(?, download_id),
                    file_size = COALESCE(?, file_size),
                    file_sha256 = COALESCE(?, file_sha256),
                    attempt_count = attempt_count + ?,
                    attempted_at = CASE WHEN ? = 1 THEN ? ELSE attempted_at END,
                    downloaded_at = COALESCE(?, downloaded_at),
                    sent_at = COALESCE(?, sent_at)
                WHERE dispatch_id = ? AND status IN ({placeholders})
                  AND (? IS NULL OR stage = ?)
                  AND (? IS NULL OR attempt_count = ?)
                """,
                (
                    status,
                    now,
                    last_error,
                    remote_task_id,
                    stage,
                    download_id,
                    file_size,
                    file_sha256,
                    int(increment_attempt),
                    int(increment_attempt),
                    now,
                    downloaded_at,
                    sent_at,
                    dispatch_id,
                    *expected_statuses,
                    expected_stage,
                    expected_stage,
                    expected_attempt_count,
                    expected_attempt_count,
                ),
            )
            if cursor.rowcount != 1:
                return None
            row = connection.execute(
                "SELECT * FROM cutting_dispatches WHERE dispatch_id = ?", (dispatch_id,)
            ).fetchone()
            self._sync_file_summaries(connection, dispatch_id)
            return self._decode(row)

    def recover_interrupted(self, now: str) -> None:
        """Fail closed after a restart; never re-upload an uncertain request."""
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE cutting_dispatches
                SET status = CASE
                        WHEN status = 'downloading' THEN 'download_failed'
                        ELSE 'push_failed'
                    END,
                    stage = CASE
                        WHEN status = 'downloading' THEN 'download_interrupted'
                        WHEN stage = 'cancelling' THEN 'cancel_unknown'
                        WHEN remote_task_id IS NOT NULL AND stage = 'uploaded' THEN 'assign_failed'
                        WHEN remote_task_id IS NOT NULL THEN 'assign_unknown'
                        ELSE 'upload_unknown'
                    END,
                    last_error = CASE
                        WHEN status = 'downloading'
                            THEN '服务重启中断文件准备，可安全重试下载'
                        WHEN stage = 'cancelling'
                            THEN '服务重启中断取消，远端结果未知，禁止盲目重复执行'
                        WHEN remote_task_id IS NOT NULL AND stage = 'uploaded'
                            THEN '文件已上传，尚未开始指派，可安全重试指派'
                        WHEN remote_task_id IS NOT NULL
                            THEN '服务重启中断机床指派，远端结果未知，禁止盲目重复指派'
                        ELSE '服务重启发生在上传阶段，远端结果未知，禁止自动重复上传'
                    END,
                    updated_at = ?
                WHERE status IN ('downloading', 'pushing')
                """,
                (now,),
            )
            if cursor.rowcount:
                self._sync_file_summaries(connection)
