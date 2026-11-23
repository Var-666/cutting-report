from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from app.database.postgres import PostgresRepository, execution_owner, require_role_recovery
from app.modules.automation.run_state import read_run, update_run


class DeliveryRepository(PostgresRepository):
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
    def _decode_delivery(row: dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        item.pop("execution_owner", None)
        item["recipients"] = json.loads(item.pop("recipients_json") or "[]")
        item["recipient_results"] = json.loads(item.pop("recipient_results_json", "{}") or "{}")
        return item

    def enqueue_delivery(self, report_id: str, *, subject: str, now: str) -> dict | None:
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT delivery_id FROM email_deliveries WHERE report_id = %s", (report_id,)
            ).fetchone()
            if existing is not None:
                return DeliveryRepository(self.database, connection=connection).delivery_for_report(report_id)
            run = connection.execute(
                "SELECT * FROM scheduled_report_runs WHERE report_id = %s FOR UPDATE", (report_id,)
            ).fetchone()
            if run is None or run['status'] == 'cancelled':
                return None
            waiting = connection.execute("SELECT delivery_id FROM email_deliveries WHERE run_id=%s AND status='analysis_wait' FOR UPDATE",
                (run['run_id'],)).fetchone()
            if waiting:
                connection.execute("""UPDATE email_deliveries SET report_id=%s,status='pending',last_error=NULL,
                    next_attempt_at=%s,updated_at=%s WHERE delivery_id=%s""", (report_id, now, now, waiting['delivery_id']))
                update_run(connection, run['run_id'], 'email_pending', now=now)
                return DeliveryRepository(self.database, connection=connection).delivery_for_report(report_id)
            delivery_id = f"mail_{uuid4().hex[:18]}"
            inserted = connection.execute(
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
            if inserted.rowcount == 1:
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
                SELECT d.*, r.schedule_name, r.report_type, r.report_date, r.attach_report, r.device_id,
                       p.filename, p.relative_path, p.file_size, p.sha256, p.generated_at, j.ai_status
                FROM email_deliveries AS d
                JOIN scheduled_report_runs AS r ON r.run_id = d.run_id
                JOIN reports AS p ON p.report_id = d.report_id
                JOIN report_jobs AS j ON j.report_id = d.report_id
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

    def rebuild_unanalyzed_delivery(self, delivery: dict, *, now: str) -> None:
        from app.modules.reports.repository import MetadataRepository
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM email_deliveries WHERE delivery_id=%s AND status='sending' FOR UPDATE",
                (delivery['delivery_id'],)).fetchone()
            if row is None or row['report_id'] != delivery['report_id']:
                raise LookupError('邮件已不在本次发送状态，拒绝重新生成')
            repository = DeliveryRepository(self.database, connection=connection)
            results = json.loads(row['recipient_results_json'])
            if any(result.get('status') == 'unknown' for result in results.values()):
                repository.mark_delivery_unknown(delivery, now=now)
                return
            if any(result.get('status') == 'accepted' for result in results.values()):
                repository.mark_delivery_failed({**delivery, 'attempt_count': delivery['max_attempts']},
                    error='旧报告缺少成功分析，部分收件人已接收或结果未知；已停止自动重生成，请核对记录',
                    next_attempt_at=now, now=now)
                return
            run = read_run(connection, row['run_id'], for_update=True)
            old = MetadataRepository(self.database, connection=connection).get_job(run['report_id'])
            report_id = f'rpt_{uuid4().hex}'
            metadata = MetadataRepository(self.database, connection=connection, device_id=run['device_id'])
            metadata.create_job(report_id=report_id, device_id=run['device_id'], report_date=old['report_date'],
                report_type=old['report_type'], project_code=old['project_code'], ship_no=old['ship_no'],
                request_id=old['request_id'], created_at=now, parameters=json.loads(old['parameters_json'] or '{}'),
                period_start=old['period_start'], period_end=old['period_end'])
            connection.execute("UPDATE report_jobs SET ai_status='waiting',stage_message='等待成功分析后重新生成报告' WHERE report_id=%s",
                (report_id,))
            connection.execute("""UPDATE scheduled_report_runs SET report_id=%s,status='generating',last_error=NULL,updated_at=%s
                WHERE run_id=%s""", (report_id, now, run['run_id']))
            connection.execute("""UPDATE email_deliveries SET status='analysis_wait',attempt_count=GREATEST(attempt_count-1,0),
                execution_owner=NULL,last_error='旧报告缺少成功分析，等待重新生成',updated_at=%s WHERE delivery_id=%s""",
                (now, row['delivery_id']))

    def mark_delivery_sent(self, delivery: dict, *, message_id: str, now: str) -> bool:
        with self.connect() as connection:
            results = dict(delivery.get("recipient_results", {}))
            results.update({recipient: {"status": "accepted"} for recipient in delivery["recipients"]})
            remaining = [recipient for recipient, result in results.items() if result["status"] == "refused"]
            status = "failed" if remaining else "sent"
            error = "部分收件人被 SMTP 永久拒收，其他收件人已由 SMTP 接收" if remaining else None
            updated = connection.execute(
                """
                UPDATE email_deliveries
                SET status = %s, message_id = %s, last_error = %s, recipients_json = %s,
                    sent_at = %s, updated_at = %s, recipient_results_json = %s
                WHERE delivery_id = %s AND status = 'sending'
                """,
                (status, message_id, error, json.dumps(remaining or delivery["recipients"]),
                 now if not remaining else None, now, json.dumps(results), delivery["delivery_id"]),
            )
            if updated.rowcount != 1:
                raise LookupError("邮件不在发送状态，拒绝写入迟到结果")
            update_run(connection, delivery["run_id"], status, now=now, error=error)
            return not remaining

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
            update_run(
                connection, delivery["run_id"], status, now=now, error=error[:500] if exhausted else None
            )

    def mark_delivery_refused(self, delivery: dict, refused: dict, *, message_id: str,
                             next_attempt_at: str, now: str) -> None:
        if not any(recipient in refused for recipient in delivery["recipients"]):
            raise ValueError("SMTP 拒收结果没有匹配的收件人")
        results = dict(delivery.get("recipient_results", {}))
        remaining = []
        for recipient in delivery["recipients"]:
            if recipient in refused:
                code = int(refused[recipient][0])
                if 400 <= code < 500:
                    remaining.append(recipient)
                results[recipient] = {"status": "refused", "code": code}
            else:
                results[recipient] = {"status": "accepted"}
        rejected = [recipient for recipient, result in results.items() if result["status"] == "refused"]
        with self.connect() as connection:
            connection.execute(
                """UPDATE email_deliveries SET recipients_json = %s, recipient_results_json = %s,
                   message_id = %s WHERE delivery_id = %s AND status = 'sending'""",
                (json.dumps(remaining or rejected), json.dumps(results), message_id, delivery["delivery_id"]),
            )
            attempt = delivery if remaining else {**delivery, 'attempt_count': delivery['max_attempts']}
            DeliveryRepository(self.database, connection=connection).mark_delivery_failed(
                attempt, error=("SMTP 部分或全部拒收，仅自动重试临时拒收地址" if remaining
                                else "SMTP 永久拒收收件人，已停止自动重试"),
                next_attempt_at=next_attempt_at, now=now,
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
            update_run(
                connection, delivery["run_id"], "unknown", now=now, error=error
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
            row = connection.execute("SELECT * FROM email_deliveries WHERE delivery_id=%s FOR UPDATE",
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
            remaining.extend(recipient for recipient, result in previous.items()
                             if result["status"] == "refused" and recipient not in decisions)
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
            update_run(
                connection, delivery["run_id"], status, now=now, error=error,
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
                """
                UPDATE email_deliveries
                SET status = 'pending', attempt_count = 0, next_attempt_at = %s,
                    last_error = NULL, updated_at = %s WHERE delivery_id = %s
                    AND status = 'failed' RETURNING run_id
                """,
                (now, now, delivery_id),
            ).fetchone()
            if row is None:
                return False
            connection.execute(
                """
                UPDATE scheduled_report_runs SET status = 'email_pending',
                    last_error = NULL, updated_at = %s WHERE run_id = %s
                """,
                (now, row["run_id"]),
            )
        return True

    def list_deliveries(self, *, page: int, page_size: int, search: str = "") -> tuple[list[dict], int]:
        source = """
            FROM email_deliveries AS d
            JOIN scheduled_report_runs AS r ON r.run_id = d.run_id
            JOIN reports AS p ON p.report_id = d.report_id
        """
        parameters: tuple = ()
        if search.strip():
            source += "WHERE strpos(lower(concat_ws(' ', r.schedule_name, d.subject, r.recipients_json)), lower(%s)) > 0"
            parameters = (search.strip(),)
        with self.connect() as connection:
            total = int(connection.execute(f"SELECT COUNT(*) AS count {source}", parameters).fetchone()['count'])
            rows = connection.execute(
                f"""
                SELECT d.*, r.schedule_name, r.report_type, r.report_date, r.trigger_type,
                       p.filename, p.relative_path, p.generated_at
                {source}
                ORDER BY d.created_at DESC
                LIMIT %s OFFSET %s
                """,
                (*parameters, page_size, (page - 1) * page_size),
            ).fetchall()
        return [self._decode_delivery(row) for row in rows], total
