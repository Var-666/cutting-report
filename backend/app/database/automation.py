from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4


class AutomationRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS report_schedules (
                    schedule_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    report_type TEXT NOT NULL CHECK(report_type IN ('leadership', 'workshop', 'equipment')),
                    frequency TEXT NOT NULL CHECK(frequency IN ('daily', 'weekly', 'monthly')),
                    run_time TEXT NOT NULL,
                    weekday INTEGER,
                    month_day INTEGER,
                    report_period TEXT NOT NULL,
                    recipients_json TEXT NOT NULL,
                    subject_template TEXT NOT NULL,
                    chart_type TEXT NOT NULL DEFAULT 'bar_line',
                    attach_report INTEGER NOT NULL DEFAULT 1,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    next_run_at TEXT NOT NULL,
                    last_run_at TEXT,
                    last_status TEXT,
                    created_by_id TEXT NOT NULL,
                    created_by_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(created_by_id) REFERENCES users(user_id)
                );
                CREATE INDEX IF NOT EXISTS idx_report_schedules_due
                    ON report_schedules(enabled, next_run_at);

                CREATE TABLE IF NOT EXISTS scheduled_report_runs (
                    run_id TEXT PRIMARY KEY,
                    schedule_id TEXT,
                    schedule_name TEXT NOT NULL,
                    report_type TEXT NOT NULL,
                    report_date TEXT NOT NULL,
                    scheduled_for TEXT NOT NULL,
                    trigger_type TEXT NOT NULL CHECK(trigger_type IN ('scheduled', 'manual')),
                    report_id TEXT UNIQUE,
                    status TEXT NOT NULL,
                    recipients_json TEXT NOT NULL,
                    subject_template TEXT NOT NULL,
                    chart_type TEXT NOT NULL,
                    attach_report INTEGER NOT NULL,
                    max_attempts INTEGER NOT NULL,
                    created_by_id TEXT NOT NULL,
                    created_by_name TEXT NOT NULL,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(schedule_id, scheduled_for, trigger_type),
                    FOREIGN KEY(schedule_id) REFERENCES report_schedules(schedule_id) ON DELETE SET NULL
                );
                CREATE INDEX IF NOT EXISTS idx_scheduled_runs_status
                    ON scheduled_report_runs(status, created_at DESC);

                CREATE TABLE IF NOT EXISTS email_deliveries (
                    delivery_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL UNIQUE,
                    report_id TEXT NOT NULL UNIQUE,
                    recipients_json TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL,
                    next_attempt_at TEXT NOT NULL,
                    last_error TEXT,
                    message_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    sent_at TEXT,
                    FOREIGN KEY(run_id) REFERENCES scheduled_report_runs(run_id) ON DELETE CASCADE,
                    FOREIGN KEY(report_id) REFERENCES report_jobs(report_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_email_deliveries_due
                    ON email_deliveries(status, next_attempt_at);
                """
            )
            run_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(scheduled_report_runs)"
                ).fetchall()
            }
            if "chart_type" not in run_columns:
                connection.execute(
                    "ALTER TABLE scheduled_report_runs "
                    "ADD COLUMN chart_type TEXT NOT NULL DEFAULT 'bar_line'"
                )
            connection.execute(
                """
                UPDATE email_deliveries
                SET status = 'retry_wait', last_error = 'Service restarted during delivery'
                WHERE status = 'sending'
                """
            )
            connection.execute("PRAGMA optimize")

    @staticmethod
    def _decode_schedule(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        item["recipients"] = json.loads(item.pop("recipients_json") or "[]")
        item["enabled"] = bool(item["enabled"])
        item["attach_report"] = bool(item["attach_report"])
        return item

    @staticmethod
    def _decode_delivery(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        item["recipients"] = json.loads(item.pop("recipients_json") or "[]")
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
                    created_by_id, created_by_name, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    schedule_id,
                    data["name"], data["report_type"], data["frequency"],
                    data["run_time"], data.get("weekday"), data.get("month_day"),
                    data["report_period"], json.dumps(data["recipients"], ensure_ascii=False),
                    data["subject_template"], data["chart_type"],
                    int(data["attach_report"]), data["max_attempts"],
                    int(data["enabled"]), data["next_run_at"],
                    user["user_id"], user["display_name"], now, now,
                ),
            )
        return self.get_schedule(schedule_id)

    def update_schedule(self, schedule_id: str, data: dict[str, Any], *, now: str) -> dict | None:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE report_schedules
                SET name = ?, report_type = ?, frequency = ?, run_time = ?,
                    weekday = ?, month_day = ?, report_period = ?, recipients_json = ?,
                    subject_template = ?, chart_type = ?, attach_report = ?,
                    max_attempts = ?, enabled = ?, next_run_at = ?, updated_at = ?
                WHERE schedule_id = ?
                """,
                (
                    data["name"], data["report_type"], data["frequency"],
                    data["run_time"], data.get("weekday"), data.get("month_day"),
                    data["report_period"], json.dumps(data["recipients"], ensure_ascii=False),
                    data["subject_template"], data["chart_type"], int(data["attach_report"]),
                    data["max_attempts"], int(data["enabled"]), data["next_run_at"],
                    now, schedule_id,
                ),
            )
            if cursor.rowcount != 1:
                return None
        return self.get_schedule(schedule_id)

    def get_schedule(self, schedule_id: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM report_schedules WHERE schedule_id = ?", (schedule_id,)
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
                "DELETE FROM report_schedules WHERE schedule_id = ?", (schedule_id,)
            )
            return cursor.rowcount == 1

    def due_schedules(self, now: str, limit: int = 10) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM report_schedules
                WHERE enabled = 1 AND next_run_at <= ?
                ORDER BY next_run_at
                LIMIT ?
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
            with self.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                current = connection.execute(
                    "SELECT * FROM report_schedules WHERE schedule_id = ?",
                    (schedule["schedule_id"],),
                ).fetchone()
                if current is None or (trigger_type == "scheduled" and not current["enabled"]):
                    return None
                if trigger_type == "scheduled":
                    updated = connection.execute(
                        """
                        UPDATE report_schedules
                        SET next_run_at = ?, last_run_at = ?, last_status = 'queued', updated_at = ?
                        WHERE schedule_id = ? AND next_run_at = ? AND enabled = 1
                        """,
                        (next_run_at, now, now, schedule["schedule_id"], scheduled_for),
                    )
                    if updated.rowcount != 1:
                        return None
                else:
                    connection.execute(
                        """
                        UPDATE report_schedules
                        SET last_run_at = ?, last_status = 'queued', updated_at = ?
                        WHERE schedule_id = ?
                        """,
                        (now, now, schedule["schedule_id"]),
                    )
                connection.execute(
                    """
                    INSERT INTO scheduled_report_runs (
                        run_id, schedule_id, schedule_name, report_type, report_date,
                        scheduled_for, trigger_type, status, recipients_json,
                        subject_template, chart_type, attach_report, max_attempts, created_by_id,
                        created_by_name, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id, schedule["schedule_id"], schedule["name"],
                        schedule["report_type"], report_date, scheduled_for, trigger_type,
                        json.dumps(schedule["recipients"], ensure_ascii=False),
                        schedule["subject_template"], schedule["chart_type"],
                        int(schedule["attach_report"]),
                        schedule["max_attempts"], schedule["created_by_id"],
                        schedule["created_by_name"], now, now,
                    ),
                )
        except sqlite3.IntegrityError:
            return None
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM scheduled_report_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["recipients"] = json.loads(item.pop("recipients_json") or "[]")
        item["attach_report"] = bool(item["attach_report"])
        return item

    def attach_report(self, run_id: str, report_id: str, *, now: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE scheduled_report_runs
                SET report_id = ?, status = 'generating', updated_at = ?
                WHERE run_id = ?
                """,
                (report_id, now, run_id),
            )

    def mark_run(self, run_id: str, status: str, *, now: str, error: str | None = None) -> None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT schedule_id FROM scheduled_report_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            connection.execute(
                """
                UPDATE scheduled_report_runs
                SET status = ?, last_error = ?, updated_at = ? WHERE run_id = ?
                """,
                (status, error, now, run_id),
            )
            if row and row["schedule_id"]:
                connection.execute(
                    """
                    UPDATE report_schedules SET last_status = ?, updated_at = ?
                    WHERE schedule_id = ?
                    """,
                    (status, now, row["schedule_id"]),
                )

    def enqueue_delivery(self, report_id: str, *, subject: str, now: str) -> dict | None:
        with self.connect() as connection:
            run = connection.execute(
                "SELECT * FROM scheduled_report_runs WHERE report_id = ?", (report_id,)
            ).fetchone()
            if run is None:
                return None
            delivery_id = f"mail_{uuid4().hex[:18]}"
            connection.execute(
                """
                INSERT OR IGNORE INTO email_deliveries (
                    delivery_id, run_id, report_id, recipients_json, subject,
                    status, max_attempts, next_attempt_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
                """,
                (
                    delivery_id, run["run_id"], report_id, run["recipients_json"],
                    subject, run["max_attempts"], now, now, now,
                ),
            )
            connection.execute(
                """
                UPDATE scheduled_report_runs SET status = 'email_pending', updated_at = ?
                WHERE run_id = ?
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
                WHERE d.report_id = ?
                """,
                (report_id,),
            ).fetchone()
        return self._decode_delivery(row) if row else None

    def claim_delivery(self, now: str) -> dict | None:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT d.*, r.schedule_name, r.report_type, r.report_date, r.attach_report,
                       p.filename, p.relative_path, p.generated_at
                FROM email_deliveries AS d
                JOIN scheduled_report_runs AS r ON r.run_id = d.run_id
                JOIN reports AS p ON p.report_id = d.report_id
                WHERE d.status IN ('pending', 'retry_wait') AND d.next_attempt_at <= ?
                ORDER BY d.next_attempt_at, d.created_at
                LIMIT 1
                """,
                (now,),
            ).fetchone()
            if row is None:
                return None
            updated = connection.execute(
                """
                UPDATE email_deliveries
                SET status = 'sending', attempt_count = attempt_count + 1, updated_at = ?
                WHERE delivery_id = ? AND status IN ('pending', 'retry_wait')
                """,
                (now, row["delivery_id"]),
            )
            if updated.rowcount != 1:
                return None
            item = dict(row)
            item["attempt_count"] = int(item["attempt_count"]) + 1
            return self._decode_delivery(item)

    def mark_delivery_sent(self, delivery: dict, *, message_id: str, now: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE email_deliveries
                SET status = 'sent', message_id = ?, last_error = NULL,
                    sent_at = ?, updated_at = ? WHERE delivery_id = ?
                """,
                (message_id, now, now, delivery["delivery_id"]),
            )
        self.mark_run(delivery["run_id"], "sent", now=now)

    def mark_delivery_failed(
        self, delivery: dict, *, error: str, next_attempt_at: str, now: str
    ) -> None:
        exhausted = int(delivery["attempt_count"]) >= int(delivery["max_attempts"])
        status = "failed" if exhausted else "retry_wait"
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE email_deliveries
                SET status = ?, last_error = ?, next_attempt_at = ?, updated_at = ?
                WHERE delivery_id = ?
                """,
                (status, error[:500], next_attempt_at, now, delivery["delivery_id"]),
            )
        self.mark_run(
            delivery["run_id"], status, now=now, error=error[:500] if exhausted else None
        )

    def retry_delivery(self, delivery_id: str, *, now: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT run_id FROM email_deliveries WHERE delivery_id = ?", (delivery_id,)
            ).fetchone()
            if row is None:
                return False
            connection.execute(
                """
                UPDATE email_deliveries
                SET status = 'pending', attempt_count = 0, next_attempt_at = ?,
                    last_error = NULL, updated_at = ? WHERE delivery_id = ?
                """,
                (now, now, delivery_id),
            )
            connection.execute(
                """
                UPDATE scheduled_report_runs SET status = 'email_pending',
                    last_error = NULL, updated_at = ? WHERE run_id = ?
                """,
                (now, row["run_id"]),
            )
        return True

    def list_deliveries(self, *, page: int, page_size: int) -> tuple[list[dict], int]:
        with self.connect() as connection:
            total = int(connection.execute("SELECT COUNT(*) FROM email_deliveries").fetchone()[0])
            rows = connection.execute(
                """
                SELECT d.*, r.schedule_name, r.report_type, r.report_date, r.trigger_type,
                       p.filename, p.relative_path, p.generated_at
                FROM email_deliveries AS d
                JOIN scheduled_report_runs AS r ON r.run_id = d.run_id
                JOIN reports AS p ON p.report_id = d.report_id
                ORDER BY d.created_at DESC
                LIMIT ? OFFSET ?
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
                       SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed
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
        }
