from __future__ import annotations

import json
from uuid import uuid4

from app.database.postgres import PostgresRepository, execution_owner, require_role_recovery, lock_key, recovery_owners

_UNCONDITIONAL = object()


class EnergyReportRepository(PostgresRepository):
    """Persist hourly/day results, isolated by meter set and calculation settings."""


    def enqueue_months(self, source_key, months, now):
        with self.connect() as connection:
            count = 0
            for month in months:
                cursor = connection.execute("""
                    INSERT INTO energy_history_queue
                        (source_key, report_month, next_attempt_at, updated_at)
                    VALUES (%s, %s, %s, %s) ON CONFLICT(source_key, report_month) DO NOTHING
                """, (source_key, month, now, now))
                count += cursor.rowcount
            return count

    def recover_history_queue(self, source_key, now):
        # An expired lease cannot renew, so the predicate and reset are one atomic decision.
        with self.connect() as connection:
            require_role_recovery(connection, 'worker')
            candidates = connection.execute("""SELECT report_month,execution_owner FROM energy_history_queue
                WHERE source_key=%s AND status='running' AND NOT EXISTS (SELECT 1 FROM process_leases lease
                    WHERE lease.owner=energy_history_queue.execution_owner
                    AND lease.expires_at>EXTRACT(EPOCH FROM clock_timestamp())) FOR UPDATE SKIP LOCKED""", (source_key,)).fetchall()
            if not candidates:
                return
            allowed = recovery_owners(connection, [row['execution_owner'] for row in candidates])
            candidates = [row for row in candidates if row['execution_owner'] in allowed]
            connection.execute("""
                UPDATE energy_history_queue SET status='pending', claim_token=NULL,
                    next_attempt_at=%s, updated_at=%s, last_error_code='INTERRUPTED'
                WHERE source_key=%s AND report_month=ANY(%s) AND status='running' AND NOT EXISTS (
                    SELECT 1 FROM process_leases lease WHERE lease.owner=energy_history_queue.execution_owner
                    AND lease.expires_at>EXTRACT(EPOCH FROM clock_timestamp()))
            """, (now, now, source_key, [r['report_month'] for r in candidates]))

    def claim_history_month(self, source_key, now):
        with self.connect() as connection:
            row = connection.execute("""
                SELECT * FROM energy_history_queue
                WHERE source_key=%s AND status='pending' AND next_attempt_at<=%s
                ORDER BY next_attempt_at, report_month DESC LIMIT 1 FOR UPDATE SKIP LOCKED
            """, (source_key, now)).fetchone()
            if row is None:
                return None
            token = uuid4().hex
            connection.execute("""
                UPDATE energy_history_queue SET status='running', claim_token=%s, execution_owner=%s,
                    attempt_count=attempt_count+1, updated_at=%s
                WHERE source_key=%s AND report_month=%s
            """, (token, execution_owner(), now, source_key, row["report_month"]))
            return {**dict(row), "claim_token": token, "attempt_count": row["attempt_count"] + 1}

    def history_ready(self, source_key, now):
        with self.connect() as connection:
            return connection.execute("""SELECT 1 FROM energy_history_queue
                WHERE source_key=%s AND status='pending' AND next_attempt_at<=%s LIMIT 1""",
                (source_key, now)).fetchone() is not None

    def reschedule_history_month(self, claim, next_attempt_at, now, error_code=None):
        with self.connect() as connection:
            cursor = connection.execute("""
                UPDATE energy_history_queue SET status='pending', claim_token=NULL,
                    next_attempt_at=%s, updated_at=%s, last_error_code=%s,
                    attempt_count=CASE WHEN %s::text IS NULL THEN 0 ELSE attempt_count END
                WHERE source_key=%s AND report_month=%s AND status='running' AND claim_token=%s
                  AND execution_owner IS NOT DISTINCT FROM %s
            """, (next_attempt_at, now, error_code, error_code,
                  claim["source_key"], claim["report_month"], claim["claim_token"], execution_owner()))
            return cursor.rowcount == 1

    def history_range_active(self, source_key, first, last, now):
        with self.connect() as connection:
            return connection.execute("""
                SELECT 1 FROM energy_history_queue WHERE source_key=%s
                    AND report_month BETWEEN %s AND %s
                    AND (status='running' OR next_attempt_at<=%s) LIMIT 1
            """, (source_key, first, last, now)).fetchone() is not None

    def get_day(self, source_key, report_date):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT report_json FROM energy_daily_snapshots WHERE source_key = %s AND report_date = %s",
                (source_key, report_date),
            ).fetchone()
        return json.loads(row["report_json"]) if row else None

    def list_month(self, source_key, report_month):
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT report_json FROM energy_daily_snapshots WHERE source_key = %s AND report_date LIKE %s ORDER BY report_date",
                (source_key, report_month + "-%"),
            ).fetchall()
        return [json.loads(row["report_json"]) for row in rows]

    def save_day(self, source_key, report, *, previous=_UNCONDITIONAL):
        with self.connect() as connection:
            lock_key(connection, "energy-day:" + json.dumps([source_key, report["report_date"]]))
            if previous is not _UNCONDITIONAL:
                row = connection.execute("SELECT report_json FROM energy_daily_snapshots WHERE source_key=%s AND report_date=%s",
                                         (source_key, report["report_date"])).fetchone()
                current = json.loads(row['report_json']) if row else None
                if current != previous:
                    return False
            connection.execute("""
                INSERT INTO energy_daily_snapshots (source_key, report_date, updated_at, report_json)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (source_key, report_date) DO UPDATE SET
                    updated_at = excluded.updated_at, report_json = excluded.report_json
            """, (source_key, report["report_date"], report["updated_at"],
                  json.dumps(report, ensure_ascii=False, allow_nan=False)))
            return True
