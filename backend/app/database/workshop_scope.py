from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import Any
from app.database.sqlite import SQLiteDatabase, SQLiteRepository

class WorkshopScopeRepository(SQLiteRepository):
    def __init__(
        self,
        database: Path | SQLiteDatabase,
        *,
        connection: sqlite3.Connection | None = None,
        scope_machine_no: str | None = None,
    ) -> None:
        super().__init__(database, connection=connection)
        self.scope_machine_no = scope_machine_no

    def require_scope(self) -> str:
        if not self.scope_machine_no:
            raise ValueError("必须指定设备生产数据范围")
        return self.scope_machine_no

    def _check_report_scope(self, connection: sqlite3.Connection, report_id: str | None) -> None:
        if report_id and connection.execute(
            "SELECT 1 FROM workshop_reports WHERE report_id=? AND scope_machine_no=? COLLATE NOCASE",
            (report_id, self.require_scope()),
        ).fetchone() is None:
            raise ValueError("日报不属于当前设备")
