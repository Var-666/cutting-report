from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from psycopg import Connection

from app.database.postgres import PostgresDatabase, PostgresRepository, lock_key
from app.modules.alarms.leases import AlarmLeaseLostError, require_alarm_lease


class AlarmReportRepository(PostgresRepository):
    def __init__(
        self,
        database: PostgresDatabase,
        *,
        connection: Connection | None = None,
        machine_no: str | None = None,
        source_name: str,
    ) -> None:
        super().__init__(database, connection=connection)
        self.machine_no = machine_no
        self.source_name = source_name

    def recover_interrupted_reports(self, owner: str, now: datetime, limit: int = 100) -> int:
        with self.connect() as connection:
            require_alarm_lease(connection, owner, now, self.source_name)
            rows = connection.execute(
                """SELECT report_id FROM alarm_reports WHERE status = 'generating'
                   AND (execution_owner IS NULL OR execution_owner != %s)
                   AND (%s::text IS NULL OR sqlite_nocase(machine_no) = sqlite_nocase(%s))
                   ORDER BY created_at, report_id LIMIT %s""", (owner, self.machine_no, self.machine_no, limit),
            ).fetchall()
            for row in rows:
                connection.execute(
                    """UPDATE alarm_reports SET status = 'failed', execution_owner = NULL,
                       error_message = '报告生成中断，已恢复待处理事件' WHERE report_id = %s""",
                    (row["report_id"],),
                )
                connection.execute("UPDATE alarm_incidents SET pdf_report_id = NULL WHERE pdf_report_id = %s",
                                   (row["report_id"],))
        return len(rows)

    def list_unreported_incidents(
        self, limit: int = 100, *, monitor_start_at: str | None = None
    ) -> list[dict[str, Any]]:
        where = "WHERE pdf_report_id IS NULL"
        if self.machine_no is not None:
            where += " AND sqlite_nocase(machine_no) = sqlite_nocase(%s)"
        parameters: tuple[Any, ...]
        if monitor_start_at:
            where += " AND first_start_time >= %s"
            parameters = (*((self.machine_no,) if self.machine_no is not None else ()), monitor_start_at, limit)
        else:
            parameters = (*((self.machine_no,) if self.machine_no is not None else ()), limit)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM alarm_incidents
                {where}
                ORDER BY first_seen_at, incident_id LIMIT %s
                """,
                parameters,
            ).fetchall()
        return [dict(row) for row in rows]

    def reserve_report(
        self,
        *,
        proposed_report_id: str,
        emission_key: str,
        machine_no: str,
        incident_ids: list[str],
        now: str,
        lease_owner: str | None = None,
        lease_now: datetime | None = None,
    ) -> str:
        payload = json.dumps(incident_ids, ensure_ascii=False)
        with self.connect() as connection:
            lock_key(connection, "alarm-source:" + self.source_name)
            if lease_owner is not None:
                require_alarm_lease(connection, lease_owner, lease_now or datetime.fromisoformat(now), self.source_name)
            existing = connection.execute(
                "SELECT report_id, execution_owner FROM alarm_reports WHERE emission_key = %s",
                (emission_key,),
            ).fetchone()
            if existing and existing["execution_owner"] is not None and lease_owner is None:
                raise AlarmLeaseLostError("已认领的告警报告必须提供执行所有权")
            report_id = str(existing["report_id"]) if existing else proposed_report_id
            if existing:
                connection.execute(
                    """
                    UPDATE alarm_reports
                    SET status = 'generating', error_message = NULL, execution_owner = %s
                    WHERE report_id = %s AND status != 'completed'
                    """,
                    (lease_owner, report_id),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO alarm_reports (
                        report_id, emission_key, status, machine_no,
                        incident_ids_json, created_at, execution_owner
                    ) VALUES (%s, %s, 'generating', %s, %s, %s, %s)
                    """,
                    (report_id, emission_key, machine_no, payload, now, lease_owner),
                )
            placeholders = ",".join("%s" for _ in incident_ids)
            connection.execute(
                f"""
                UPDATE alarm_incidents SET pdf_report_id = %s
                WHERE incident_id IN ({placeholders}) AND pdf_report_id IS NULL
                """,
                (report_id, *incident_ids),
            )
        return report_id

    def complete_report(
        self,
        report_id: str,
        *,
        analysis: dict[str, Any],
        html_relative_path: str,
        pdf_relative_path: str,
        pdf_filename: str,
        file_size: int,
        sha256: str,
        model_status: str,
        completed_at: str,
        lease_owner: str | None = None,
        lease_now: datetime | None = None,
    ) -> None:
        with self.connect() as connection:
            if lease_owner is not None:
                require_alarm_lease(connection, lease_owner, lease_now or datetime.fromisoformat(completed_at), self.source_name)
            updated = connection.execute(
                """
                UPDATE alarm_reports
                SET status = 'completed', analysis_json = %s, html_relative_path = %s,
                    pdf_relative_path = %s, pdf_filename = %s, file_size = %s, sha256 = %s,
                    model_status = %s, error_message = NULL, completed_at = %s
                WHERE report_id = %s AND status = 'generating' AND execution_owner IS NOT DISTINCT FROM %s
                """,
                (
                    json.dumps(analysis, ensure_ascii=False),
                    html_relative_path,
                    pdf_relative_path,
                    pdf_filename,
                    file_size,
                    sha256,
                    model_status,
                    completed_at,
                    report_id,
                    lease_owner,
                ),
            )
            if updated.rowcount != 1:
                raise AlarmLeaseLostError("告警报告已被其他执行接管或完成")

    def fail_report(self, report_id: str, error: str, *, lease_owner: str | None = None,
                    lease_now: datetime | None = None) -> None:
        with self.connect() as connection:
            if lease_owner is not None:
                if lease_now is None:
                    raise ValueError("写入租约执行失败必须提供当前时间")
                require_alarm_lease(connection, lease_owner, lease_now, self.source_name)
            updated = connection.execute(
                """
                UPDATE alarm_reports SET status = 'failed', error_message = %s
                WHERE report_id = %s AND status = 'generating' AND execution_owner IS NOT DISTINCT FROM %s
                """,
                (error[:500], report_id, lease_owner),
            )
            if updated.rowcount != 1:
                raise AlarmLeaseLostError("告警报告已被其他执行接管或完成")
            connection.execute(
                """
                UPDATE alarm_incidents SET pdf_report_id = NULL
                WHERE pdf_report_id = %s
                """,
                (report_id,),
            )

    def get_report(self, report_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM alarm_reports WHERE report_id = %s AND (%s::text IS NULL OR sqlite_nocase(machine_no) = sqlite_nocase(%s))", (report_id, self.machine_no, self.machine_no)
            ).fetchone()
        return self._decode_report(row)

    def list_reports(
        self, limit: int = 50, *, monitor_start_at: str | None = None
    ) -> list[dict[str, Any]]:
        join = ""
        where = ""
        parameters: tuple[Any, ...]
        if monitor_start_at:
            join = "LEFT JOIN alarm_incidents i ON i.pdf_report_id = r.report_id"
            where = """
                WHERE i.first_start_time >= %s
                   OR (
                        r.status IN ('generating', 'failed')
                        AND substr(r.created_at, 1, 19) >= replace(substr(%s, 1, 19), ' ', 'T')
                   )
            """
            parameters = (monitor_start_at, monitor_start_at, limit)
        else:
            parameters = (limit,)
        if self.machine_no is not None:
            where = ("WHERE (" + where.strip()[6:] + ") AND sqlite_nocase(r.machine_no) = sqlite_nocase(%s)") if where else "WHERE sqlite_nocase(r.machine_no) = sqlite_nocase(%s)"
            parameters = (*parameters[:-1], self.machine_no, parameters[-1])
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT DISTINCT r.* FROM alarm_reports r
                {join} {where}
                ORDER BY r.created_at DESC, r.report_id DESC LIMIT %s
                """,
                parameters,
            ).fetchall()
        return [self._decode_report(row) for row in rows]

    def page_reports(
        self,
        *,
        monitor_start_at: str | None,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        join = ""
        where = ""
        values: tuple[Any, ...] = ()
        if monitor_start_at:
            join = "LEFT JOIN alarm_incidents i ON i.pdf_report_id = r.report_id"
            where = """
                WHERE i.first_start_time >= %s
                   OR (
                        r.status IN ('generating', 'failed')
                        AND substr(r.created_at, 1, 19) >= replace(substr(%s, 1, 19), ' ', 'T')
                   )
            """
            values = (monitor_start_at, monitor_start_at)
        if self.machine_no is not None:
            where = ("WHERE (" + where.strip()[6:] + ") AND sqlite_nocase(r.machine_no) = sqlite_nocase(%s)") if where else "WHERE sqlite_nocase(r.machine_no) = sqlite_nocase(%s)"
            values = (*values, self.machine_no)
        with self.connect() as connection:
            summary = connection.execute(
                f"""
                SELECT COUNT(DISTINCT r.report_id) AS total,
                       COUNT(DISTINCT CASE WHEN r.status = 'completed' THEN r.report_id END) AS completed_count
                FROM alarm_reports r {join} {where}
                """,
                values,
            ).fetchone()
            total = int(summary["total"] or 0)
            page_count = max(1, (total + page_size - 1) // page_size)
            current_page = min(page, page_count)
            rows = connection.execute(
                f"""
                SELECT DISTINCT r.* FROM alarm_reports r
                {join} {where}
                ORDER BY r.created_at DESC, r.report_id DESC LIMIT %s OFFSET %s
                """,
                (*values, page_size, (current_page - 1) * page_size),
            ).fetchall()
        return {
            "items": [self._decode_report(row) for row in rows],
            "page": current_page,
            "page_size": page_size,
            "page_count": page_count,
            "total": total,
            "completed_count": int(summary["completed_count"] or 0),
        }

    @staticmethod
    def _decode_report(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        item["incident_ids"] = json.loads(item.pop("incident_ids_json"))
        item["analysis"] = (
            json.loads(item.pop("analysis_json")) if item.get("analysis_json") else None
        )
        item.pop("analysis_json", None)
        return item
