from __future__ import annotations

import json
from psycopg import Connection
from psycopg.errors import UniqueViolation

from typing import Any
from uuid import uuid4

from app.database.postgres import PostgresDatabase, PostgresRepository, execution_owner, require_role_recovery


class AutomationRepository(PostgresRepository):
    def __init__(
        self,
        database: PostgresDatabase,
        *,
        connection: Connection | None = None,
    ) -> None:
        super().__init__(database, connection=connection)


    def recover_interrupted_deliveries(self) -> None:
        """Only the newly elected Automation process may classify abandoned SMTP attempts."""
        with self.connect() as connection:
            require_role_recovery(connection, 'automation')
            interrupted = connection.execute(
                "SELECT delivery_id, recipients_json, recipient_results_json FROM email_deliveries WHERE status = 'sending'"
            ).fetchall()
            for row in interrupted:
                results = json.loads(row["recipient_results_json"] or "{}")
                results.update({recipient: {"status": "unknown"}
                                for recipient in json.loads(row["recipients_json"] or "[]")})
                connection.execute(
                    "UPDATE email_deliveries SET recipient_results_json = %s WHERE delivery_id = %s",
                    (json.dumps(results), row["delivery_id"]),
                )
            connection.execute(
                """
                UPDATE email_deliveries
                SET status = 'unknown', last_error = '发送中断，结果待核对，禁止自动重发'
                WHERE status = 'sending'
                """
            )
            connection.execute(
                """UPDATE scheduled_report_runs SET status = 'unknown',
                   last_error = '发送中断，结果待核对，禁止自动重发'
                   WHERE report_id IN (SELECT report_id FROM email_deliveries WHERE status = 'unknown')"""
            )

    @staticmethod
    def _decode_schedule(row: dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        item["recipients"] = json.loads(item.pop("recipients_json") or "[]")
        item["enabled"] = bool(item["enabled"])
        item["attach_report"] = bool(item["attach_report"])
        return item

    @staticmethod
    def _decode_delivery(row: dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        item.pop("execution_owner", None)
        item["recipients"] = json.loads(item.pop("recipients_json") or "[]")
        item["recipient_results"] = json.loads(item.pop("recipient_results_json", "{}") or "{}")
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

    def get_schedule(self, schedule_id: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM report_schedules WHERE schedule_id = %s", (schedule_id,)
            ).fetchone()
        return self._decode_schedule(row) if row else None

    def list_schedules(self) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT s.*,
                       COUNT(r.run_id) AS run_count,
                       SUM(CASE WHEN r.status = 'sent' THEN 1 ELSE 0 END) AS sent_count,
                       SUM(CASE WHEN r.status = 'failed' THEN 1 ELSE 0 END) AS failed_count
                FROM report_schedules AS s
                LEFT JOIN scheduled_report_runs AS r ON r.schedule_id = s.schedule_id
                GROUP BY s.schedule_id
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
            row = connection.execute(
                "SELECT * FROM scheduled_report_runs WHERE run_id = %s" + (" FOR UPDATE" if for_update else ""), (run_id,)
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["recipients"] = json.loads(item.pop("recipients_json") or "[]")
        item["attach_report"] = bool(item["attach_report"])
        return item

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

    def incomplete_runs(self, limit: int = 100) -> list[dict]:
        """Find committed runs interrupted between durable workflow steps."""
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT r.run_id, r.report_id, j.status AS report_status, j.error_message
                FROM scheduled_report_runs r
                LEFT JOIN report_jobs j ON j.report_id = r.report_id
                WHERE (r.status = 'queued' AND r.report_id IS NULL)
                   OR (j.status = 'completed'
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
            row = connection.execute(
                "SELECT schedule_id FROM scheduled_report_runs WHERE run_id = %s", (run_id,)
            ).fetchone()
            connection.execute(
                """
                UPDATE scheduled_report_runs
                SET status = %s, last_error = %s, updated_at = %s WHERE run_id = %s
                """,
                (status, error, now, run_id),
            )
            if row and row["schedule_id"]:
                connection.execute(
                    """
                    UPDATE report_schedules SET last_status = %s, updated_at = %s
                    WHERE schedule_id = %s
                    """,
                    (status, now, row["schedule_id"]),
                )

    def enqueue_delivery(self, report_id: str, *, subject: str, now: str) -> dict | None:
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT delivery_id FROM email_deliveries WHERE report_id = %s", (report_id,)
            ).fetchone()
            if existing is not None:
                return AutomationRepository(self.database, connection=connection).delivery_for_report(report_id)
            run = connection.execute(
                "SELECT * FROM scheduled_report_runs WHERE report_id = %s", (report_id,)
            ).fetchone()
            if run is None:
                return None
            delivery_id = f"mail_{uuid4().hex[:18]}"
            connection.execute(
                """
                INSERT INTO email_deliveries (
                    delivery_id, run_id, report_id, recipients_json, subject,
                    status, max_attempts, next_attempt_at, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, 'pending', %s, %s, %s, %s) ON CONFLICT(report_id) DO NOTHING
                """,
                (
                    delivery_id, run["run_id"], report_id, run["recipients_json"],
                    subject, run["max_attempts"], now, now, now,
                ),
            )
            connection.execute(
                """
                UPDATE scheduled_report_runs SET status = 'email_pending', updated_at = %s
                WHERE run_id = %s
                """,
                (now, run["run_id"]),
            )
        return self.delivery_for_report(report_id)

    def delivery_for_report(self, report_id: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT d.*, r.schedule_name, r.report_type, r.report_date, r.attach_report,
                       p.filename, p.relative_path, p.generated_at
                FROM email_deliveries AS d
                JOIN scheduled_report_runs AS r ON r.run_id = d.run_id
                JOIN reports AS p ON p.report_id = d.report_id
                WHERE d.report_id = %s
                """,
                (report_id,),
            ).fetchone()
        return self._decode_delivery(row) if row else None

    def claim_delivery(self, now: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT d.*, r.schedule_name, r.report_type, r.report_date, r.attach_report,
                       p.filename, p.relative_path, p.generated_at
                FROM email_deliveries AS d
                JOIN scheduled_report_runs AS r ON r.run_id = d.run_id
                JOIN reports AS p ON p.report_id = d.report_id
                WHERE d.status IN ('pending', 'retry_wait') AND d.next_attempt_at <= %s
                ORDER BY d.next_attempt_at, d.created_at
                LIMIT 1 FOR UPDATE OF d SKIP LOCKED
                """,
                (now,),
            ).fetchone()
            if row is None:
                return None
            updated = connection.execute(
                """
                UPDATE email_deliveries
                SET status = 'sending', attempt_count = attempt_count + 1, updated_at = %s,
                    message_id = COALESCE(message_id, %s), execution_owner = %s
                WHERE delivery_id = %s AND status IN ('pending', 'retry_wait')
                """,
                (now, f"<{row['delivery_id']}@cutting-report.local>", execution_owner(), row["delivery_id"]),
            )
            if updated.rowcount != 1:
                return None
            item = dict(row)
            item["message_id"] = row["message_id"] or f"<{row['delivery_id']}@cutting-report.local>"
            item["attempt_count"] = int(item["attempt_count"]) + 1
            return self._decode_delivery(item)

    def mark_delivery_sent(self, delivery: dict, *, message_id: str, now: str) -> None:
        with self.connect() as connection:
            results = dict(delivery.get("recipient_results", {}))
            results.update({recipient: {"status": "accepted"} for recipient in delivery["recipients"]})
            updated = connection.execute(
                """
                UPDATE email_deliveries
                SET status = 'sent', message_id = %s, last_error = NULL,
                    sent_at = %s, updated_at = %s, recipient_results_json = %s
                WHERE delivery_id = %s AND status = 'sending'
                """,
                (message_id, now, now, json.dumps(results), delivery["delivery_id"]),
            )
            if updated.rowcount != 1:
                raise LookupError("邮件不在发送状态，拒绝写入迟到结果")
            AutomationRepository(self.database, connection=connection).mark_run(delivery["run_id"], "sent", now=now)

    def mark_delivery_failed(
        self, delivery: dict, *, error: str, next_attempt_at: str, now: str
    ) -> None:
        exhausted = int(delivery["attempt_count"]) >= int(delivery["max_attempts"])
        status = "failed" if exhausted else "retry_wait"
        with self.connect() as connection:
            updated = connection.execute(
                """
                UPDATE email_deliveries
                SET status = %s, last_error = %s, next_attempt_at = %s, updated_at = %s
                WHERE delivery_id = %s AND status = 'sending'
                """,
                (status, error[:500], next_attempt_at, now, delivery["delivery_id"]),
            )
            if updated.rowcount != 1:
                raise LookupError("邮件不在发送状态，拒绝写入迟到结果")
            AutomationRepository(self.database, connection=connection).mark_run(
                delivery["run_id"], status, now=now, error=error[:500] if exhausted else None
            )

    def mark_delivery_refused(self, delivery: dict, refused: dict, *, message_id: str,
                             next_attempt_at: str, now: str) -> None:
        results = dict(delivery.get("recipient_results", {}))
        remaining = []
        for recipient in delivery["recipients"]:
            if recipient in refused:
                remaining.append(recipient)
                results[recipient] = {"status": "refused", "code": int(refused[recipient][0])}
            else:
                results[recipient] = {"status": "accepted"}
        if not remaining:
            raise ValueError("SMTP 拒收结果没有匹配的收件人")
        with self.connect() as connection:
            connection.execute(
                """UPDATE email_deliveries SET recipients_json = %s, recipient_results_json = %s,
                   message_id = %s WHERE delivery_id = %s AND status = 'sending'""",
                (json.dumps(remaining), json.dumps(results), message_id, delivery["delivery_id"]),
            )
            AutomationRepository(self.database, connection=connection).mark_delivery_failed(
                delivery, error="SMTP 拒收收件人，仅重试拒收部分", next_attempt_at=next_attempt_at, now=now
            )

    def mark_delivery_unknown(self, delivery: dict, *, now: str) -> None:
        error = "SMTP 接受结果未知，请按 Message-ID 核对服务器记录；禁止自动重发"
        results = dict(delivery.get("recipient_results", {}))
        results.update({recipient: {"status": "unknown"} for recipient in delivery["recipients"]})
        with self.connect() as connection:
            updated = connection.execute(
                """UPDATE email_deliveries SET status = 'unknown', last_error = %s,
                   updated_at = %s, recipient_results_json = %s WHERE delivery_id = %s AND status = 'sending'""",
                (error, now, json.dumps(results), delivery["delivery_id"]),
            )
            if updated.rowcount != 1:
                raise LookupError("邮件不在发送状态，拒绝写入迟到结果")
            AutomationRepository(self.database, connection=connection).mark_run(
                delivery["run_id"], "unknown", now=now, error=error
            )

    def delivery_state(self, delivery_id: str) -> str | None:
        with self.connect() as connection:
            row = connection.execute("SELECT status FROM email_deliveries WHERE delivery_id = %s", (delivery_id,)).fetchone()
        return row['status'] if row else None

    def resolve_unknown_delivery(self, delivery_id: str, *, message_id: str,
                                 outcomes: list[dict], evidence_note: str,
                                 user: dict, now: str) -> bool:
        if not evidence_note.strip() or len(evidence_note) > 1000:
            raise ValueError("必须填写不超过 1000 字的邮件服务器核对依据")
        decisions = {item["recipient"]: item["outcome"] for item in outcomes}
        if len(decisions) != len(outcomes) or any(value not in {"accepted", "not_accepted"}
                                                for value in decisions.values()):
            raise ValueError("收件人核对结果重复或无效")
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM email_deliveries WHERE delivery_id=%s",
                                     (delivery_id,)).fetchone()
            if row is None:
                raise LookupError("邮件发送记录不存在")
            if row["status"] != "unknown":
                return False
            if not message_id or row["message_id"] != message_id:
                raise ValueError("Message-ID 与当前发送记录不一致")
            delivery = self._decode_delivery(row)
            previous = delivery["recipient_results"]
            unknown = {recipient for recipient in delivery["recipients"]
                       if previous.get(recipient, {}).get("status") != "accepted"}
            if not unknown or set(decisions) != unknown:
                raise ValueError("必须逐一核对全部待确认收件人，不能修改已接收结果")
            results = {**previous, **{recipient: {"status": outcome}
                                     for recipient, outcome in decisions.items()}}
            remaining = [recipient for recipient in delivery["recipients"]
                         if decisions.get(recipient) == "not_accepted"]
            status = "failed" if remaining else "sent"
            error = "人工核对服务器确认未接收，等待人工重试" if remaining else None
            connection.execute("""
                INSERT INTO email_delivery_resolutions VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (uuid4().hex, delivery_id, delivery["run_id"], message_id,
                  user["user_id"], user["display_name"], evidence_note.strip(),
                  json.dumps(previous), json.dumps(results), status, now))
            connection.execute("""
                UPDATE email_deliveries SET status=%s, recipients_json=%s, recipient_results_json=%s,
                    last_error=%s, updated_at=%s WHERE delivery_id=%s AND status='unknown'
            """, (status, json.dumps(remaining if remaining else delivery["recipients"]),
                  json.dumps(results), error, now, delivery_id))
            AutomationRepository(self.database, connection=connection).mark_run(
                delivery["run_id"], status, now=now, error=error,
            )
        return True

    def delivery_resolutions(self, delivery_id: str) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute("""
                SELECT * FROM email_delivery_resolutions WHERE delivery_id=%s
                ORDER BY created_at, resolution_id
            """, (delivery_id,)).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item["before_results"] = json.loads(item.pop("before_results_json"))
            item["after_results"] = json.loads(item.pop("after_results_json"))
            results.append(item)
        return results

    def retry_delivery(self, delivery_id: str, *, now: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT run_id FROM email_deliveries WHERE delivery_id = %s AND status = 'failed'", (delivery_id,)
            ).fetchone()
            if row is None:
                return False
            connection.execute(
                """
                UPDATE email_deliveries
                SET status = 'pending', attempt_count = 0, next_attempt_at = %s,
                    last_error = NULL, updated_at = %s WHERE delivery_id = %s
                """,
                (now, now, delivery_id),
            )
            connection.execute(
                """
                UPDATE scheduled_report_runs SET status = 'email_pending',
                    last_error = NULL, updated_at = %s WHERE run_id = %s
                """,
                (now, row["run_id"]),
            )
        return True

    def list_deliveries(self, *, page: int, page_size: int) -> tuple[list[dict], int]:
        with self.connect() as connection:
            total = int(connection.execute("SELECT COUNT(*) AS count FROM email_deliveries").fetchone()['count'])
            rows = connection.execute(
                """
                SELECT d.*, r.schedule_name, r.report_type, r.report_date, r.trigger_type,
                       p.filename, p.relative_path, p.generated_at
                FROM email_deliveries AS d
                JOIN scheduled_report_runs AS r ON r.run_id = d.run_id
                JOIN reports AS p ON p.report_id = d.report_id
                ORDER BY d.created_at DESC
                LIMIT %s OFFSET %s
                """,
                (page_size, (page - 1) * page_size),
            ).fetchall()
        return [self._decode_delivery(row) for row in rows], total

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
                       SUM(CASE WHEN status IN ('pending', 'sending', 'retry_wait') THEN 1 ELSE 0 END) AS pending,
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
