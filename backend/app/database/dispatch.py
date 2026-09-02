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
            connection.execute("PRAGMA optimize")

    @staticmethod
    def _decode(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        item["is_simulation"] = bool(item["is_simulation"])
        return item

    def create(self, data: dict[str, Any], *, user: dict, now: str) -> dict[str, Any]:
        if data["status"] not in DISPATCH_STATUSES:
            raise ValueError("不支持的派发状态")
        dispatch_id = f"dsp_{uuid4().hex[:18]}"
        with self.connect() as connection:
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
        item = self.get(dispatch_id)
        if item is None:
            raise RuntimeError("派发任务保存失败")
        return item

    def get(self, dispatch_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM cutting_dispatches WHERE dispatch_id = ?",
                (dispatch_id,),
            ).fetchone()
        return self._decode(row) if row else None

    def find_active(
        self, document_id: str, version_id: str | None, machine_ip: str
    ) -> dict[str, Any] | None:
        placeholders = ", ".join("?" for _ in ACTIVE_DISPATCH_STATUSES)
        with self.connect() as connection:
            row = connection.execute(
                f"""
                SELECT * FROM cutting_dispatches
                WHERE document_id = ?
                  AND COALESCE(version_id, '') = COALESCE(?, '')
                  AND machine_ip = ?
                  AND status IN ({placeholders})
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (document_id, version_id, machine_ip, *sorted(ACTIVE_DISPATCH_STATUSES)),
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
                ),
            )
            if cursor.rowcount != 1:
                return None
        return self.get(dispatch_id)

    def update_machine(
        self,
        dispatch_id: str,
        *,
        machine_ip: str,
        expected: Iterable[str],
        now: str,
    ) -> dict[str, Any] | None:
        expected_statuses = sorted(set(expected))
        if not expected_statuses:
            raise ValueError("必须指定允许修改的原状态")
        placeholders = ", ".join("?" for _ in expected_statuses)
        with self.connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE cutting_dispatches
                SET machine_ip = ?, updated_at = ?
                WHERE dispatch_id = ? AND status IN ({placeholders})
                """,
                (machine_ip, now, dispatch_id, *expected_statuses),
            )
            if cursor.rowcount != 1:
                return None
        return self.get(dispatch_id)

    def recover_interrupted(self, now: str) -> None:
        """Fail closed after a restart; never re-upload an uncertain request."""
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE cutting_dispatches
                SET status = CASE
                        WHEN status = 'downloading' THEN 'download_failed'
                        ELSE 'push_failed'
                    END,
                    stage = CASE
                        WHEN status = 'downloading' THEN 'download_interrupted'
                        WHEN remote_task_id IS NOT NULL THEN 'assign_interrupted'
                        ELSE 'upload_unknown'
                    END,
                    last_error = CASE
                        WHEN status = 'downloading'
                            THEN '服务重启中断文件准备，可安全重试下载'
                        WHEN remote_task_id IS NOT NULL
                            THEN '服务重启中断机床指派，请人工核对后重试指派'
                        ELSE '服务重启发生在上传阶段，远端结果未知，禁止自动重复上传'
                    END,
                    updated_at = ?
                WHERE status IN ('downloading', 'pushing')
                """,
                (now,),
            )
