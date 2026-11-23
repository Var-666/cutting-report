from __future__ import annotations

import json
from psycopg import Connection
from psycopg.errors import LockNotAvailable, UniqueViolation

from typing import Any
from uuid import uuid4

from app.database.postgres import PostgresDatabase, PostgresRepository
from app.modules.automation.delivery_repository import DeliveryRepository
from app.modules.automation.run_state import read_run, update_run


class AutomationRepository(PostgresRepository):
    def __init__(
        self,
        database: PostgresDatabase,
        *,
        connection: Connection | None = None,
    ) -> None:
        super().__init__(database, connection=connection)
        self.deliveries = DeliveryRepository(database, connection=connection)

    @staticmethod
    def _decode_schedule(row: dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        item["recipients"] = json.loads(item.pop("recipients_json") or "[]")
        item["enabled"] = bool(item["enabled"])
        item["attach_report"] = bool(item["attach_report"])
        return item

    def create_schedule(self, data: dict[str, Any], *, user: dict, now: str) -> dict:
        schedule_id = f"sch_{uuid4().hex[:16]}"
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO report_schedules (
                    schedule_id, name, report_type, frequency, run_time, weekday,
                    month_day, report_period, recipients_json, subject_template,
                    chart_type, attach_report, max_attempts, enabled, next_run_at,
                    created_by_id, created_by_name, created_at, updated_at, device_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    schedule_id,
                    data["name"], data["report_type"], data["frequency"],
                    data["run_time"], data.get("weekday"), data.get("month_day"),
                    data["report_period"], json.dumps(data["recipients"], ensure_ascii=False),
                    data["subject_template"], data["chart_type"],
                    int(data["attach_report"]), data["max_attempts"],
                    int(data["enabled"]), data["next_run_at"],
                    user["user_id"], user["display_name"], now, now, data["device_id"],
                ),
            )
        return self.get_schedule(schedule_id)

    def update_schedule(self, schedule_id: str, data: dict[str, Any], *, now: str) -> dict | None:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE report_schedules
                SET name = %s, report_type = %s, frequency = %s, run_time = %s,
                    weekday = %s, month_day = %s, report_period = %s, recipients_json = %s,
                    subject_template = %s, chart_type = %s, attach_report = %s,
                    max_attempts = %s, enabled = %s, next_run_at = %s, updated_at = %s, device_id = %s
                WHERE schedule_id = %s
                """,
                (
                    data["name"], data["report_type"], data["frequency"],
                    data["run_time"], data.get("weekday"), data.get("month_day"),
                    data["report_period"], json.dumps(data["recipients"], ensure_ascii=False),
                    data["subject_template"], data["chart_type"], int(data["attach_report"]),
                    data["max_attempts"], int(data["enabled"]), data["next_run_at"],
                    now, data["device_id"], schedule_id,
                ),
            )
            if cursor.rowcount != 1:
                return None
        return self.get_schedule(schedule_id)

    def get_schedule(self, schedule_id: str, *, for_update: bool = False) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM report_schedules WHERE schedule_id = %s" + (" FOR UPDATE" if for_update else ""),
                (schedule_id,),
            ).fetchone()
        return self._decode_schedule(row) if row else None

    def set_schedule_enabled(self, schedule_id: str, enabled: bool, *, next_run_at: str, now: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                """UPDATE report_schedules SET enabled = %s, next_run_at = %s, updated_at = %s
                   WHERE schedule_id = %s RETURNING *""",
                (int(enabled), next_run_at, now, schedule_id),
            ).fetchone()
        return self._decode_schedule(row) if row else None

    def list_schedules(self) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT s.*,
                       COUNT(r.run_id) AS run_count,
                       SUM(CASE WHEN r.status = 'sent' THEN 1 ELSE 0 END) AS sent_count,
                       SUM(CASE WHEN r.status = 'failed' THEN 1 ELSE 0 END) AS failed_count,
                       active.run_id AS active_manual_run_id, active.status AS active_manual_run_status
                FROM report_schedules AS s
                LEFT JOIN scheduled_report_runs AS r ON r.schedule_id = s.schedule_id
                LEFT JOIN LATERAL (
                    SELECT run.run_id, CASE WHEN d.status='pending' THEN 'email_pending'
                                           ELSE COALESCE(d.status, run.status) END AS status
                    FROM scheduled_report_runs run LEFT JOIN email_deliveries d ON d.run_id=run.run_id
                    WHERE run.schedule_id=s.schedule_id AND run.trigger_type='manual'
                      AND run.status IN ('queued','generating','email_pending','retry_wait')
                    ORDER BY run.scheduled_for DESC LIMIT 1
                ) active ON true
                GROUP BY s.schedule_id, active.run_id, active.status
                ORDER BY s.enabled DESC, s.next_run_at, s.created_at DESC
                """
            ).fetchall()
        return [self._decode_schedule(row) for row in rows]

    def delete_schedule(self, schedule_id: str) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM report_schedules WHERE schedule_id = %s", (schedule_id,)
            )
            return cursor.rowcount == 1

    def due_schedules(self, now: str, limit: int = 10) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM report_schedules
                WHERE enabled = 1 AND next_run_at <= %s
                ORDER BY COALESCE(last_run_at, ''), next_run_at, schedule_id
                LIMIT %s
                """,
                (now, limit),
            ).fetchall()
        return [self._decode_schedule(row) for row in rows]

    def reserve_run(
        self,
        schedule: dict,
        *,
        scheduled_for: str,
        report_date: str,
        trigger_type: str,
        now: str,
        next_run_at: str | None = None,
    ) -> dict | None:
        run_id = f"run_{uuid4().hex[:18]}"
        try:
            with self.connect() as connection, connection.transaction():
                current = connection.execute(
                    "SELECT * FROM report_schedules WHERE schedule_id = %s FOR UPDATE",
                    (schedule["schedule_id"],),
                ).fetchone()
                if current is None or (trigger_type == "scheduled" and not current["enabled"]):
                    return None
                if trigger_type == "scheduled":
                    updated = connection.execute(
                        """
                        UPDATE report_schedules
                        SET next_run_at = %s, last_run_at = %s, last_status = 'queued', updated_at = %s
                        WHERE schedule_id = %s AND next_run_at = %s AND enabled = 1
                        """,
                        (next_run_at, now, now, schedule["schedule_id"], scheduled_for),
                    )
                    if updated.rowcount != 1:
                        return None
                else:
                    connection.execute(
                        """
                        UPDATE report_schedules
                        SET last_run_at = %s, last_status = 'queued', updated_at = %s
                        WHERE schedule_id = %s
                        """,
                        (now, now, schedule["schedule_id"]),
                    )
                connection.execute(
                    """
                    INSERT INTO scheduled_report_runs (
                        run_id, schedule_id, schedule_name, report_type, report_date,
                        scheduled_for, trigger_type, status, recipients_json,
                        subject_template, chart_type, attach_report, max_attempts, created_by_id,
                        created_by_name, created_at, updated_at, device_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'queued', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        run_id, schedule["schedule_id"], schedule["name"],
                        schedule["report_type"], report_date, scheduled_for, trigger_type,
                        json.dumps(schedule["recipients"], ensure_ascii=False),
                        schedule["subject_template"], schedule["chart_type"],
                        int(schedule["attach_report"]),
                        schedule["max_attempts"], schedule["created_by_id"],
                        schedule["created_by_name"], now, now, current["device_id"],
                    ),
                )
        except UniqueViolation:
            return None
        return self.get_run(run_id)

    def get_run(self, run_id: str, *, for_update: bool = False) -> dict | None:
        with self.connect() as connection:
            return read_run(connection, run_id, for_update=for_update)

    def get_run_for_report(self, report_id: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM scheduled_report_runs WHERE report_id = %s",
                (report_id,),
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["recipients"] = json.loads(item.pop("recipients_json") or "[]")
        item["attach_report"] = bool(item["attach_report"])
        return item

    def cancel_manual_run(self, run_id: str, *, now: str) -> dict | None:
        try:
            with self.connect() as connection:
                repository = AutomationRepository(self.database, connection=connection)
                run = repository.get_run(run_id, for_update=True)
                if run is None or run['status'] == 'cancelled':
                    return run
                if run['trigger_type'] != 'manual' or run['status'] not in ('queued', 'generating', 'email_pending', 'retry_wait'):
                    raise ValueError('当前执行状态无法取消，请刷新后查看进度')
                # SMTP updates its delivery row first. A concurrent sender wins the claim without blocking on this run lock.
                delivery = connection.execute('SELECT status FROM email_deliveries WHERE run_id=%s FOR UPDATE NOWAIT',
                                              (run_id,)).fetchone()
                if delivery is not None:
                    if delivery['status'] not in ('pending', 'retry_wait', 'analysis_wait'):
                        raise ValueError('当前邮件已进入发送或结果确认阶段，请刷新后查看状态')
                    connection.execute("""UPDATE email_deliveries SET status='cancelled',updated_at=%s,last_error=NULL
                        WHERE run_id=%s""", (now, run_id))
                repository.mark_run(run_id, 'cancelled', now=now)
                return repository.get_run(run_id)
        except LockNotAvailable as exc:
            raise ValueError('邮件状态正在更新，请刷新后查看是否仍可取消') from exc

    def incomplete_runs(self, limit: int = 100) -> list[dict]:
        """Find committed runs interrupted between durable workflow steps."""
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT r.run_id, r.report_id, j.status AS report_status, j.error_message
                FROM scheduled_report_runs r
                LEFT JOIN report_jobs j ON j.report_id = r.report_id
                WHERE (r.status = 'queued' AND r.report_id IS NULL)
                   OR (r.status <> 'cancelled' AND j.status = 'completed'
                       AND EXISTS (SELECT 1 FROM reports p WHERE p.report_id = r.report_id)
                       AND NOT EXISTS (
                           SELECT 1 FROM email_deliveries d WHERE d.report_id = r.report_id
                       ))
                   OR (r.status = 'generating' AND j.status = 'failed')
                ORDER BY r.created_at, r.run_id LIMIT %s
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def attach_report(self, run_id: str, report_id: str, *, now: str) -> None:
        with self.connect() as connection:
            updated = connection.execute(
                """
                UPDATE scheduled_report_runs
                SET report_id = %s, status = 'generating', updated_at = %s
                WHERE run_id = %s AND report_id IS NULL
                """,
                (report_id, now, run_id),
            )
            if updated.rowcount != 1:
                raise LookupError("自动化运行记录不存在或已经关联报表")

    def mark_run(self, run_id: str, status: str, *, now: str, error: str | None = None) -> None:
        with self.connect() as connection:
            update_run(connection, run_id, status, now=now, error=error)

    def summary(self) -> dict[str, int]:
        with self.connect() as connection:
            schedule = connection.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN enabled = 1 THEN 1 ELSE 0 END) AS enabled
                FROM report_schedules
                """
            ).fetchone()
            delivery = connection.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN status = 'sent' THEN 1 ELSE 0 END) AS sent,
                       SUM(CASE WHEN status IN ('pending', 'sending', 'retry_wait', 'analysis_wait') THEN 1 ELSE 0 END) AS pending,
                       SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
                       SUM(CASE WHEN status = 'unknown' THEN 1 ELSE 0 END) AS unknown
                FROM email_deliveries
                """
            ).fetchone()
        return {
            "schedule_total": int(schedule["total"] or 0),
            "schedule_enabled": int(schedule["enabled"] or 0),
            "delivery_total": int(delivery["total"] or 0),
            "delivery_sent": int(delivery["sent"] or 0),
            "delivery_pending": int(delivery["pending"] or 0),
            "delivery_failed": int(delivery["failed"] or 0),
            "delivery_unknown": int(delivery["unknown"] or 0),
        }
