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
                    created_by_name, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    downloaded_at = COALESCE(?, downloaded_at),
                    sent_at = COALESCE(?, sent_at)
                WHERE dispatch_id = ? AND status IN ({placeholders})
                """,
                (
                    status,
                    now,
                    last_error,
                    remote_task_id,
                    downloaded_at,
                    sent_at,
                    dispatch_id,
                    *expected_statuses,
                ),
            )
            if cursor.rowcount != 1:
                return None
        return self.get(dispatch_id)
