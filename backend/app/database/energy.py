from __future__ import annotations

import json
from uuid import uuid4

from app.database.sqlite import SQLiteRepository


class EnergyReportRepository(SQLiteRepository):
    """Persist hourly/day results, isolated by meter set and calculation settings."""

    def initialize(self):
        self.database.initialize()
        with self.connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS energy_daily_snapshots (
                    source_key TEXT NOT NULL,
                    report_date TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    PRIMARY KEY (source_key, report_date)
                )
            """)

            connection.execute("""
                CREATE TABLE IF NOT EXISTS energy_history_queue (
                    source_key TEXT NOT NULL,
                    report_month TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    claim_token TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_error_code TEXT,
                    PRIMARY KEY (source_key, report_month)
                )
            """)

    def enqueue_months(self, source_key, months, now):
        with self.connect() as connection:
            count = 0
            for month in months:
                cursor = connection.execute("""
                    INSERT INTO energy_history_queue
                        (source_key, report_month, next_attempt_at, updated_at)
                    VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING
                """, (source_key, month, now, now))
                count += cursor.rowcount
            return count

    def recover_history_queue(self, source_key, now):
        # Called only at startup of the single application process.
        with self.connect() as connection:
            connection.execute("""
                UPDATE energy_history_queue SET status='pending', claim_token=NULL,
                    next_attempt_at=?, updated_at=?, last_error_code='INTERRUPTED'
                WHERE source_key=? AND status='running'
            """, (now, now, source_key))

    def claim_history_month(self, source_key, now):
        with self.connect() as connection:
            self.begin_immediate(connection)
            row = connection.execute("""
                SELECT * FROM energy_history_queue
                WHERE source_key=? AND status='pending' AND next_attempt_at<=?
                ORDER BY next_attempt_at, report_month DESC LIMIT 1
            """, (source_key, now)).fetchone()
            if row is None:
                return None
            token = uuid4().hex
            connection.execute("""
                UPDATE energy_history_queue SET status='running', claim_token=?,
                    attempt_count=attempt_count+1, updated_at=?
                WHERE source_key=? AND report_month=?
            """, (token, now, source_key, row["report_month"]))
            return {**dict(row), "claim_token": token, "attempt_count": row["attempt_count"] + 1}

    def reschedule_history_month(self, claim, next_attempt_at, now, error_code=None):
        with self.connect() as connection:
            cursor = connection.execute("""
                UPDATE energy_history_queue SET status='pending', claim_token=NULL,
                    next_attempt_at=?, updated_at=?, last_error_code=?,
                    attempt_count=CASE WHEN ? IS NULL THEN 0 ELSE attempt_count END
                WHERE source_key=? AND report_month=? AND status='running' AND claim_token=?
            """, (next_attempt_at, now, error_code, error_code,
                  claim["source_key"], claim["report_month"], claim["claim_token"]))
            return cursor.rowcount == 1

    def history_range_active(self, source_key, first, last, now):
        with self.connect() as connection:
            return connection.execute("""
                SELECT 1 FROM energy_history_queue WHERE source_key=?
                    AND report_month BETWEEN ? AND ?
                    AND (status='running' OR next_attempt_at<=?) LIMIT 1
            """, (source_key, first, last, now)).fetchone() is not None

    def get_day(self, source_key, report_date):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT report_json FROM energy_daily_snapshots WHERE source_key = ? AND report_date = ?",
                (source_key, report_date),
            ).fetchone()
        return json.loads(row["report_json"]) if row else None

    def list_month(self, source_key, report_month):
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT report_json FROM energy_daily_snapshots WHERE source_key = ? AND report_date LIKE ? ORDER BY report_date",
                (source_key, report_month + "-%"),
            ).fetchall()
        return [json.loads(row["report_json"]) for row in rows]

    def save_day(self, source_key, report):
        with self.connect() as connection:
            connection.execute("""
                INSERT INTO energy_daily_snapshots (source_key, report_date, updated_at, report_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (source_key, report_date) DO UPDATE SET
                    updated_at = excluded.updated_at, report_json = excluded.report_json
            """, (source_key, report["report_date"], report["updated_at"],
                  json.dumps(report, ensure_ascii=False, allow_nan=False)))
