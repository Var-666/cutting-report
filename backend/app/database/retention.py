from __future__ import annotations

import sqlite3
from pathlib import Path, PurePosixPath
from typing import Any

from app.database.sqlite import SQLiteDatabase, SQLiteRepository
from app.database.dispatch import UNRESOLVED_DISPATCH_SQL


REPORT_TERMINAL_STATUSES = ("completed", "failed")
AUTOMATION_TERMINAL_STATUSES = ("sent", "failed")
DISPATCH_TERMINAL_STATUSES = (
    "sent",
    "download_failed",
    "push_failed",
    "cancelled",
)

# Source START_TIME is local wall time; application timestamps use ISO format.
ALARM_OCCURRED_AT_SQL = """replace(substr(COALESCE(
    NULLIF(incident.first_start_time, ''), incident.first_seen_at
), 1, 19), 'T', ' ')"""
ALARM_REPORT_LINK_SQL = """(
    incident.pdf_report_id = report.report_id
    OR incident.incident_id IN (SELECT value FROM json_each(report.incident_ids_json))
)"""


class RetentionRepository(SQLiteRepository):
    """Persistence boundary for bounded operational-history retention."""

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
                CREATE TABLE IF NOT EXISTS retention_file_deletions (
                    relative_path TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    queued_at TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_retention_file_deletions_queued
                    ON retention_file_deletions(queued_at, relative_path);
                """
            )

    @staticmethod
    def _placeholders(values: list[str]) -> str:
        return ", ".join("?" for _ in values)

    @staticmethod
    def _queue_file(
        connection: sqlite3.Connection,
        relative_path: str | None,
        *,
        category: str,
        queued_at: str,
    ) -> None:
        cleaned = str(relative_path or "").strip()
        if not cleaned:
            return
        connection.execute(
            """
            INSERT INTO retention_file_deletions (
                relative_path, category, queued_at, updated_at
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(relative_path) DO UPDATE SET
                category = excluded.category,
                updated_at = excluded.updated_at
            """,
            (cleaned, category, queued_at, queued_at),
        )

    def cleanup_expired_records(
        self,
        *,
        report_cutoff: str | None,
        analysis_cutoff: str | None,
        alarm_cutoff: str | None,
        dispatch_cutoff: str | None,
        audit_cutoff: str | None,
        queued_at: str,
        batch_size: int,
    ) -> dict[str, int]:
        counts = {
            "reports": 0,
            "automation_runs": 0,
            "workshop_reports": 0,
            "workshop_items": 0,
            "workshop_confirmations": 0,
            "analyses": 0,
            "alarm_reports": 0,
            "alarm_incidents": 0,
            "alarm_events": 0,
            "dispatches": 0,
            "download_records": 0,
            "confirmation_audits": 0,
            "admin_audits": 0,
        }
        with self.connect() as connection:
            self.begin_immediate(connection)
            if report_cutoff is not None:
                report_rows = connection.execute(
                    f"""
                    SELECT j.report_id, r.relative_path
                    FROM report_jobs AS j
                    LEFT JOIN reports AS r ON r.report_id = j.report_id
                    WHERE j.status IN ({self._placeholders(list(REPORT_TERMINAL_STATUSES))})
                      AND COALESCE(j.finished_at, j.created_at) <= ?
                      AND NOT EXISTS (
                          SELECT 1 FROM scheduled_report_runs AS run
                          WHERE run.report_id = j.report_id
                            AND run.status NOT IN (
                                {self._placeholders(list(AUTOMATION_TERMINAL_STATUSES))}
                            )
                      )
                      AND NOT EXISTS (
                          SELECT 1 FROM email_deliveries AS delivery
                          WHERE delivery.report_id = j.report_id
                            AND delivery.status IN ('pending', 'sending', 'retry_wait', 'unknown')
                      )
                    ORDER BY COALESCE(j.finished_at, j.created_at), j.report_id
                    LIMIT ?
                    """,
                    (
                        *REPORT_TERMINAL_STATUSES,
                        report_cutoff,
                        *AUTOMATION_TERMINAL_STATUSES,
                        batch_size,
                    ),
                ).fetchall()
                report_ids = [str(row["report_id"]) for row in report_rows]
                for row in report_rows:
                    relative_path = str(row["relative_path"] or "")
                    self._queue_file(
                        connection,
                        relative_path,
                        category="report",
                        queued_at=queued_at,
                    )
                    if relative_path:
                        pdf_path = (
                            PurePosixPath(relative_path).with_suffix(".pdf").as_posix()
                        )
                        self._queue_file(
                            connection,
                            pdf_path,
                            category="report",
                            queued_at=queued_at,
                        )
                if report_ids:
                    placeholders = self._placeholders(report_ids)
                    connection.execute(
                        f"DELETE FROM email_deliveries WHERE report_id IN ({placeholders})",
                        report_ids,
                    )
                    connection.execute(
                        f"DELETE FROM scheduled_report_runs WHERE report_id IN ({placeholders})",
                        report_ids,
                    )
                    connection.execute(
                        f"DELETE FROM reports WHERE report_id IN ({placeholders})",
                        report_ids,
                    )
                    deleted = connection.execute(
                        f"DELETE FROM report_jobs WHERE report_id IN ({placeholders})",
                        report_ids,
                    )
                    counts["reports"] = deleted.rowcount
                orphan_runs = connection.execute(
                    f"""
                    DELETE FROM scheduled_report_runs
                    WHERE run_id IN (
                        SELECT run_id FROM scheduled_report_runs
                        WHERE report_id IS NULL
                          AND status IN ({self._placeholders(list(AUTOMATION_TERMINAL_STATUSES))})
                          AND updated_at <= ?
                        ORDER BY updated_at, run_id
                        LIMIT ?
                    )
                    """,
                    (*AUTOMATION_TERMINAL_STATUSES, report_cutoff, batch_size),
                )
                counts["automation_runs"] = orphan_runs.rowcount

                workshop_report_rows = connection.execute(
                    """
                    SELECT report_id FROM workshop_reports
                    WHERE generated_at <= ?
                    ORDER BY generated_at, report_id
                    LIMIT ?
                    """,
                    (report_cutoff, batch_size),
                ).fetchall()
                workshop_report_ids = [
                    str(row["report_id"]) for row in workshop_report_rows
                ]
                if workshop_report_ids:
                    placeholders = self._placeholders(workshop_report_ids)
                    counts["workshop_items"] = int(
                        connection.execute(
                            f"""
                            SELECT COUNT(*) FROM workshop_report_items
                            WHERE report_id IN ({placeholders})
                            """,
                            workshop_report_ids,
                        ).fetchone()[0]
                    )
                    counts["workshop_reports"] = connection.execute(
                        f"""
                        DELETE FROM workshop_reports
                        WHERE report_id IN ({placeholders})
                        """,
                        workshop_report_ids,
                    ).rowcount
                counts["workshop_confirmations"] = connection.execute(
                    """
                    DELETE FROM workshop_confirmations
                    WHERE task_id IN (
                        SELECT confirmation.task_id
                        FROM workshop_confirmations AS confirmation
                        WHERE confirmation.updated_at <= ?
                          AND NOT EXISTS (
                              SELECT 1 FROM workshop_report_items AS item
                              WHERE item.task_id = confirmation.task_id
                          )
                        ORDER BY confirmation.updated_at, confirmation.task_id
                        LIMIT ?
                    )
                    """,
                    (report_cutoff, batch_size),
                ).rowcount

            if analysis_cutoff is not None:
                counts["analyses"] = connection.execute(
                    """
                    DELETE FROM report_analyses
                    WHERE cache_key IN (
                        SELECT cache_key FROM report_analyses
                        WHERE generated_at <= ?
                        ORDER BY generated_at, cache_key
                        LIMIT ?
                    )
                    """,
                    (analysis_cutoff, batch_size),
                ).rowcount

            if alarm_cutoff is not None:
                alarm_time_cutoff = alarm_cutoff[:19].replace("T", " ")
                alarm_reports = connection.execute(
                    f"""
                    SELECT report_id, html_relative_path, pdf_relative_path
                    FROM alarm_reports AS report
                    WHERE report.status IN ('completed', 'failed')
                      AND (
                          COALESCE(report.completed_at, report.created_at) < ?
                          OR EXISTS (
                              SELECT 1 FROM alarm_incidents AS incident
                              WHERE {ALARM_REPORT_LINK_SQL}
                          )
                      )
                      AND NOT EXISTS (
                          SELECT 1 FROM alarm_incidents AS incident
                          WHERE {ALARM_REPORT_LINK_SQL}
                            AND (
                                incident.status <> 'closed'
                                OR {ALARM_OCCURRED_AT_SQL} >= ?
                            )
                      )
                    ORDER BY COALESCE(report.completed_at, report.created_at), report.report_id
                    LIMIT ?
                    """,
                    (alarm_cutoff, alarm_time_cutoff, batch_size),
                ).fetchall()
                alarm_report_ids = [str(row["report_id"]) for row in alarm_reports]
                for row in alarm_reports:
                    self._queue_file(
                        connection,
                        row["html_relative_path"],
                        category="alarm_report",
                        queued_at=queued_at,
                    )
                    self._queue_file(
                        connection,
                        row["pdf_relative_path"],
                        category="alarm_report",
                        queued_at=queued_at,
                    )
                old_incidents = connection.execute(
                    f"""
                    SELECT incident_id FROM alarm_incidents AS incident
                    WHERE status = 'closed'
                      AND {ALARM_OCCURRED_AT_SQL} < ?
                    ORDER BY {ALARM_OCCURRED_AT_SQL}, incident_id
                    LIMIT ?
                    """,
                    (alarm_time_cutoff, batch_size),
                ).fetchall()
                incident_ids = [str(row["incident_id"]) for row in old_incidents]
                if incident_ids:
                    placeholders = self._placeholders(incident_ids)
                    counts["alarm_events"] = connection.execute(
                        f"DELETE FROM alarm_source_events WHERE incident_id IN ({placeholders})",
                        incident_ids,
                    ).rowcount
                    counts["alarm_incidents"] = connection.execute(
                        f"DELETE FROM alarm_incidents WHERE incident_id IN ({placeholders})",
                        incident_ids,
                    ).rowcount
                if alarm_report_ids:
                    placeholders = self._placeholders(alarm_report_ids)
                    counts["alarm_reports"] = connection.execute(
                        f"DELETE FROM alarm_reports WHERE report_id IN ({placeholders})",
                        alarm_report_ids,
                    ).rowcount

            if dispatch_cutoff is not None:
                counts["dispatches"] = connection.execute(
                    f"""
                    DELETE FROM cutting_dispatches
                    WHERE dispatch_id IN (
                        SELECT dispatch_id FROM cutting_dispatches
                        WHERE status IN ({self._placeholders(list(DISPATCH_TERMINAL_STATUSES))})
                          AND NOT {UNRESOLVED_DISPATCH_SQL}
                          AND updated_at <= ?
                        ORDER BY updated_at, dispatch_id
                        LIMIT ?
                    )
                    """,
                    (*DISPATCH_TERMINAL_STATUSES, dispatch_cutoff, batch_size),
                ).rowcount
                counts["download_records"] = connection.execute(
                    """
                    DELETE FROM cutting_downloads
                    WHERE download_id IN (
                        SELECT download_id FROM cutting_downloads
                        WHERE updated_at <= ?
                          AND (
                              status = 'failed'
                              OR payload LIKE '%"file_deleted_at"%'
                          )
                        ORDER BY updated_at, download_id
                        LIMIT ?
                    )
                    """,
                    (dispatch_cutoff, batch_size),
                ).rowcount

            if audit_cutoff is not None:
                counts["confirmation_audits"] = connection.execute(
                    """
                    DELETE FROM workshop_confirmation_audits
                    WHERE audit_id IN (
                        SELECT audit_id FROM workshop_confirmation_audits
                        WHERE operated_at <= ?
                        ORDER BY operated_at, audit_id
                        LIMIT ?
                    )
                    """,
                    (audit_cutoff, batch_size),
                ).rowcount
                counts["admin_audits"] = connection.execute(
                    """
                    DELETE FROM admin_role_audits
                    WHERE audit_id IN (
                        SELECT audit_id FROM admin_role_audits
                        WHERE operated_at <= ?
                        ORDER BY operated_at, audit_id
                        LIMIT ?
                    )
                    """,
                    (audit_cutoff, batch_size),
                ).rowcount
        return counts

    def list_file_deletions(self, limit: int) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM retention_file_deletions
                ORDER BY attempt_count, queued_at, relative_path
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def has_pending_file_deletions(self) -> bool:
        with self.connect() as connection:
            return connection.execute(
                "SELECT 1 FROM retention_file_deletions LIMIT 1"
            ).fetchone() is not None

    def complete_file_deletion(self, relative_path: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM retention_file_deletions WHERE relative_path = ?",
                (relative_path,),
            )

    def fail_file_deletion(self, relative_path: str, error: str, now: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE retention_file_deletions
                SET attempt_count = attempt_count + 1,
                    last_error = ?, updated_at = ?
                WHERE relative_path = ?
                """,
                (error[:500], now, relative_path),
            )
