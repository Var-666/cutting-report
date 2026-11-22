from __future__ import annotations
from psycopg import Connection

from typing import Any
from app.database.postgres import PostgresDatabase, PostgresRepository, lock_key

class WorkshopScopeRepository(PostgresRepository):
    def __init__(
        self,
        database: PostgresDatabase,
        *,
        connection: Connection | None = None,
        scope_machine_no: str | None = None,
    ) -> None:
        super().__init__(database, connection=connection)
        self.scope_machine_no = scope_machine_no

    def require_scope(self) -> str:
        if not self.scope_machine_no:
            raise ValueError("必须指定设备生产数据范围")
        return self.scope_machine_no

    def lock_scope(self, connection: Connection) -> None:
        # Snapshot/revision and confirmation checks span rows, including absent
        # rows. Serialize one machine only; unrelated machines remain concurrent.
        ascii_fold = str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')
        lock_key(connection, 'workshop:' + self.require_scope().translate(ascii_fold))

    def _check_report_scope(self, connection: Connection, report_id: str | None) -> None:
        if report_id and connection.execute(
            "SELECT 1 FROM workshop_reports WHERE report_id=%s AND sqlite_nocase(scope_machine_no) = sqlite_nocase(%s)",
            (report_id, self.require_scope()),
        ).fetchone() is None:
            raise ValueError("日报不属于当前设备")
