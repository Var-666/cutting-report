from __future__ import annotations

import hashlib
import json
import sqlite3
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from app.database.sqlite import SQLiteDatabase, SQLiteRepository


def _text(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def _fingerprint(record: dict[str, Any]) -> str:
    # Time fields and source ids deliberately stay out of the fingerprint: an
    # incident that remains active for hours must still emit only one report.
    alarm_code = _text(record.get("alarm_code")).casefold()
    description = " ".join(_text(record.get("alarm_desc")).split()).casefold()
    identity = {
        "machine_no": _text(record.get("machine_no")).casefold(),
        "alarm_type": _text(record.get("alarm_type")).casefold(),
        "alarm_code": alarm_code,
        "alarm_class": _text(record.get("alarm_class")).casefold(),
        # Alarm codes are the stable PLC identity. Descriptions sometimes carry
        # a live value or timestamp, so use text only when the source has no code.
        "alarm_desc_fallback": "" if alarm_code else description,
    }
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _window_fingerprint(fingerprint: str, monitor_start_at: str | None) -> str:
    if not monitor_start_at:
        return fingerprint
    return hashlib.sha256(
        f"{fingerprint}|{monitor_start_at}".encode("utf-8")
    ).hexdigest()


def _source_hash(record: dict[str, Any]) -> str:
    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


class AlarmRepository(SQLiteRepository):
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
                CREATE TABLE IF NOT EXISTS alarm_sync_state (
                    source_name TEXT PRIMARY KEY,
                    last_alarm_id INTEGER NOT NULL DEFAULT 0,
                    last_success_at TEXT,
                    monitor_start_at TEXT,
                    lease_owner TEXT,
                    lease_until TEXT
                );

                CREATE TABLE IF NOT EXISTS alarm_incidents (
                    incident_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    machine_no TEXT NOT NULL,
                    alarm_type TEXT NOT NULL,
                    alarm_code TEXT NOT NULL,
                    alarm_class TEXT NOT NULL,
                    alarm_desc TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('open', 'closed')),
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    closed_at TEXT,
                    first_start_time TEXT,
                    latest_remove_time TEXT,
                    first_alarm_id INTEGER NOT NULL,
                    latest_alarm_id INTEGER NOT NULL,
                    pdf_report_id TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_alarm_incidents_one_open
                    ON alarm_incidents(fingerprint) WHERE status = 'open';
                CREATE INDEX IF NOT EXISTS idx_alarm_incidents_recent
                    ON alarm_incidents(first_seen_at DESC, incident_id DESC);
                CREATE INDEX IF NOT EXISTS idx_alarm_incidents_start
                    ON alarm_incidents(first_start_time DESC, first_seen_at DESC);
                CREATE INDEX IF NOT EXISTS idx_alarm_incidents_unreported
                    ON alarm_incidents(pdf_report_id, first_seen_at);
                CREATE INDEX IF NOT EXISTS idx_alarm_incidents_retention
                    ON alarm_incidents(status, closed_at, incident_id);

                CREATE TABLE IF NOT EXISTS alarm_source_events (
                    source_alarm_id INTEGER PRIMARY KEY,
                    incident_id TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    start_time TEXT,
                    remove_time TEXT,
                    is_open INTEGER NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    FOREIGN KEY(incident_id) REFERENCES alarm_incidents(incident_id)
                );
                CREATE INDEX IF NOT EXISTS idx_alarm_source_events_open
                    ON alarm_source_events(is_open, source_alarm_id);
                CREATE INDEX IF NOT EXISTS idx_alarm_source_events_incident
                    ON alarm_source_events(incident_id, is_open);

                CREATE TABLE IF NOT EXISTS alarm_reports (
                    report_id TEXT PRIMARY KEY,
                    emission_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL CHECK(status IN ('generating', 'completed', 'failed')),
                    machine_no TEXT NOT NULL,
                    incident_ids_json TEXT NOT NULL,
                    analysis_json TEXT,
                    html_relative_path TEXT,
                    pdf_relative_path TEXT,
                    pdf_filename TEXT,
                    file_size INTEGER,
                    sha256 TEXT,
                    model_status TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_alarm_reports_created
                    ON alarm_reports(created_at DESC, report_id DESC);
                CREATE INDEX IF NOT EXISTS idx_alarm_reports_retention
                    ON alarm_reports(status, completed_at, report_id);
                """
            )
            sync_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(alarm_sync_state)"
                ).fetchall()
            }
            if "monitor_start_at" not in sync_columns:
                connection.execute(
                    "ALTER TABLE alarm_sync_state ADD COLUMN monitor_start_at TEXT"
                )
            connection.execute(
                """
                INSERT OR IGNORE INTO alarm_sync_state(source_name, last_alarm_id)
                VALUES ('production', 0)
                """
            )

    @staticmethod
    def severity_for(record: dict[str, Any]) -> str:
        content = " ".join(
            (_text(record.get("alarm_class")), _text(record.get("alarm_desc")))
        ).casefold()
        if any(word in content for word in ("急停", "致命", "严重", "火灾", "critical", "emergency")):
            return "critical"
        if any(word in content for word in ("故障", "错误", "停机", "fault", "error")):
            return "high"
        return "warning"

    def get_cursor(self, source_name: str = "production") -> int:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT last_alarm_id FROM alarm_sync_state WHERE source_name = ?",
                (source_name,),
            ).fetchone()
        return int(row[0]) if row else 0

    def get_sync_state(self, source_name: str = "production") -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT source_name, last_alarm_id, last_success_at, monitor_start_at
                FROM alarm_sync_state WHERE source_name = ?
                """,
                (source_name,),
            ).fetchone()
        return dict(row) if row else {
            "source_name": source_name,
            "last_alarm_id": 0,
            "last_success_at": None,
            "monitor_start_at": None,
        }

    def configure_monitoring_window(
        self,
        monitor_start_at: str,
        *,
        force: bool = False,
        source_name: str = "production",
    ) -> bool:
        with self.connect() as connection:
            self.begin_immediate(connection)
            row = connection.execute(
                "SELECT monitor_start_at FROM alarm_sync_state WHERE source_name = ?",
                (source_name,),
            ).fetchone()
            changed = force or row is None or row["monitor_start_at"] != monitor_start_at
            if not changed:
                return False
            connection.execute(
                """
                INSERT INTO alarm_sync_state(
                    source_name, last_alarm_id, last_success_at, monitor_start_at
                ) VALUES (?, 0, NULL, ?)
                ON CONFLICT(source_name) DO UPDATE SET
                    last_alarm_id = 0,
                    last_success_at = NULL,
                    monitor_start_at = excluded.monitor_start_at
                """,
                (source_name, monitor_start_at),
            )
        return True

    def list_open_source_ids(
        self, limit: int = 2000, *, monitor_start_at: str | None = None
    ) -> list[int]:
        where = "WHERE is_open = 1"
        parameters: tuple[Any, ...]
        if monitor_start_at:
            where += " AND start_time >= ?"
            parameters = (monitor_start_at, limit)
        else:
            parameters = (limit,)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT source_alarm_id FROM alarm_source_events
                {where} ORDER BY source_alarm_id LIMIT ?
                """,
                parameters,
            ).fetchall()
        return [int(row[0]) for row in rows]

    def try_acquire_lease(
        self,
        owner: str,
        now: datetime,
        *,
        ttl_seconds: int = 600,
        source_name: str = "production",
    ) -> bool:
        now_text = now.isoformat(timespec="seconds")
        until = (now + timedelta(seconds=ttl_seconds)).isoformat(timespec="seconds")
        with self.connect() as connection:
            self.begin_immediate(connection)
            updated = connection.execute(
                """
                UPDATE alarm_sync_state
                SET lease_owner = ?, lease_until = ?
                WHERE source_name = ?
                  AND (lease_until IS NULL OR lease_until < ? OR lease_owner = ?)
                """,
                (owner, until, source_name, now_text, owner),
            )
            return updated.rowcount == 1

    def release_lease(self, owner: str, source_name: str = "production") -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE alarm_sync_state SET lease_owner = NULL, lease_until = NULL
                WHERE source_name = ? AND lease_owner = ?
                """,
                (source_name, owner),
            )

    def ingest(
        self,
        records: Iterable[dict[str, Any]],
        *,
        now: datetime,
        cursor: int,
        debounce_minutes: int,
        monitor_start_at: str | None = None,
        source_name: str = "production",
    ) -> dict[str, int]:
        """Persist events and the caller's sequential scan boundary atomically.

        Supplemental rechecks may contain larger IDs without proving that the
        intervening source rows were scanned; they must never advance cursor.
        """
        now_text = now.isoformat(timespec="seconds")
        debounce_cutoff = (now - timedelta(minutes=max(0, debounce_minutes))).isoformat(
            timespec="seconds"
        )
        normalized = sorted(
            (dict(item) for item in records),
            key=lambda item: int(item["alarm_info_id"]),
        )
        affected_incidents: set[str] = set()
        created_incidents = 0

        with self.connect() as connection:
            self.begin_immediate(connection)
            for record in normalized:
                source_alarm_id = int(record["alarm_info_id"])
                remove_time = _text(record.get("remove_time")) or None
                start_time = _text(record.get("start_time")) or None
                is_open = 0 if remove_time else 1
                existing = connection.execute(
                    """
                    SELECT e.incident_id, i.first_start_time
                    FROM alarm_source_events e
                    JOIN alarm_incidents i ON i.incident_id = e.incident_id
                    WHERE e.source_alarm_id = ?
                    """,
                    (source_alarm_id,),
                ).fetchone()
                if (
                    existing
                    and monitor_start_at
                    and start_time
                    and start_time >= monitor_start_at
                    and (
                        not existing["first_start_time"]
                        or existing["first_start_time"] < monitor_start_at
                    )
                ):
                    affected_incidents.add(str(existing["incident_id"]))
                    connection.execute(
                        "DELETE FROM alarm_source_events WHERE source_alarm_id = ?",
                        (source_alarm_id,),
                    )
                    existing = None
                if existing:
                    incident_id = str(existing["incident_id"])
                    connection.execute(
                        """
                        UPDATE alarm_source_events
                        SET source_hash = ?, start_time = ?, remove_time = ?,
                            is_open = ?, last_seen_at = ?
                        WHERE source_alarm_id = ?
                        """,
                        (
                            _source_hash(record),
                            start_time,
                            remove_time,
                            is_open,
                            now_text,
                            source_alarm_id,
                        ),
                    )
                else:
                    base_fingerprint = _fingerprint(record)
                    fingerprint = _window_fingerprint(
                        base_fingerprint, monitor_start_at
                    )
                    fingerprints = (
                        (base_fingerprint, fingerprint)
                        if fingerprint != base_fingerprint
                        else (fingerprint,)
                    )
                    placeholders = ",".join("?" for _ in fingerprints)
                    incident_where = (
                        f"fingerprint IN ({placeholders}) AND status = 'open'"
                    )
                    incident_parameters: tuple[Any, ...] = fingerprints
                    if monitor_start_at:
                        incident_where += " AND first_start_time >= ?"
                        incident_parameters = (*fingerprints, monitor_start_at)
                    incident = connection.execute(
                        f"""
                        SELECT incident_id FROM alarm_incidents
                        WHERE {incident_where}
                        """,
                        incident_parameters,
                    ).fetchone()
                    if incident is None:
                        closed_where = (
                            f"fingerprint IN ({placeholders}) "
                            "AND status = 'closed' AND closed_at >= ?"
                        )
                        closed_parameters: tuple[Any, ...] = (
                            *fingerprints,
                            debounce_cutoff,
                        )
                        if monitor_start_at:
                            closed_where += " AND first_start_time >= ?"
                            closed_parameters = (
                                *fingerprints,
                                debounce_cutoff,
                                monitor_start_at,
                            )
                        incident = connection.execute(
                            f"""
                            SELECT incident_id FROM alarm_incidents
                            WHERE {closed_where}
                            ORDER BY closed_at DESC LIMIT 1
                            """,
                            closed_parameters,
                        ).fetchone()
                        if incident is not None:
                            connection.execute(
                                """
                                UPDATE alarm_incidents
                                SET status = 'open', closed_at = NULL, last_seen_at = ?
                                WHERE incident_id = ?
                                """,
                                (now_text, incident["incident_id"]),
                            )
                    if incident is None:
                        incident_id = f"alm_{uuid4().hex[:16]}"
                        connection.execute(
                            """
                            INSERT INTO alarm_incidents (
                                incident_id, fingerprint, machine_no, alarm_type,
                                alarm_code, alarm_class, alarm_desc, severity, status,
                                first_seen_at, last_seen_at, first_start_time,
                                latest_remove_time, first_alarm_id, latest_alarm_id
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                incident_id,
                                fingerprint,
                                _text(record.get("machine_no")),
                                _text(record.get("alarm_type")),
                                _text(record.get("alarm_code")),
                                _text(record.get("alarm_class")),
                                _text(record.get("alarm_desc")),
                                self.severity_for(record),
                                now_text,
                                now_text,
                                start_time,
                                remove_time,
                                source_alarm_id,
                                source_alarm_id,
                            ),
                        )
                        created_incidents += 1
                    else:
                        incident_id = str(incident["incident_id"])
                    connection.execute(
                        """
                        INSERT INTO alarm_source_events (
                            source_alarm_id, incident_id, source_hash, start_time,
                            remove_time, is_open, first_seen_at, last_seen_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            source_alarm_id,
                            incident_id,
                            _source_hash(record),
                            start_time,
                            remove_time,
                            is_open,
                            now_text,
                            now_text,
                        ),
                    )
                affected_incidents.add(incident_id)
                connection.execute(
                    """
                    UPDATE alarm_incidents
                    SET last_seen_at = ?, latest_alarm_id = MAX(latest_alarm_id, ?),
                        latest_remove_time = COALESCE(?, latest_remove_time)
                    WHERE incident_id = ?
                    """,
                    (now_text, source_alarm_id, remove_time, incident_id),
                )

            for incident_id in affected_incidents:
                open_count = connection.execute(
                    """
                    SELECT COUNT(*) FROM alarm_source_events
                    WHERE incident_id = ? AND is_open = 1
                    """,
                    (incident_id,),
                ).fetchone()[0]
                if open_count:
                    connection.execute(
                        """
                        UPDATE alarm_incidents SET status = 'open', closed_at = NULL
                        WHERE incident_id = ?
                        """,
                        (incident_id,),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE alarm_incidents
                        SET status = 'closed', closed_at = COALESCE(closed_at, ?)
                        WHERE incident_id = ?
                        """,
                        (now_text, incident_id),
                    )

            connection.execute(
                """
                UPDATE alarm_sync_state
                SET last_alarm_id = MAX(last_alarm_id, ?), last_success_at = ?
                WHERE source_name = ?
                """,
                (cursor, now_text, source_name),
            )
            committed_cursor = int(connection.execute(
                "SELECT last_alarm_id FROM alarm_sync_state WHERE source_name = ?",
                (source_name,),
            ).fetchone()[0])

        return {
            "records": len(normalized),
            "created_incidents": created_incidents,
            "last_alarm_id": committed_cursor,
        }

    def list_unreported_incidents(
        self, limit: int = 100, *, monitor_start_at: str | None = None
    ) -> list[dict[str, Any]]:
        where = "WHERE pdf_report_id IS NULL"
        parameters: tuple[Any, ...]
        if monitor_start_at:
            where += " AND first_start_time >= ?"
            parameters = (monitor_start_at, limit)
        else:
            parameters = (limit,)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM alarm_incidents
                {where}
                ORDER BY first_seen_at, incident_id LIMIT ?
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
    ) -> str:
        payload = json.dumps(incident_ids, ensure_ascii=False)
        with self.connect() as connection:
            self.begin_immediate(connection)
            existing = connection.execute(
                "SELECT report_id FROM alarm_reports WHERE emission_key = ?",
                (emission_key,),
            ).fetchone()
            report_id = str(existing["report_id"]) if existing else proposed_report_id
            if existing:
                connection.execute(
                    """
                    UPDATE alarm_reports
                    SET status = 'generating', error_message = NULL
                    WHERE report_id = ? AND status != 'completed'
                    """,
                    (report_id,),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO alarm_reports (
                        report_id, emission_key, status, machine_no,
                        incident_ids_json, created_at
                    ) VALUES (?, ?, 'generating', ?, ?, ?)
                    """,
                    (report_id, emission_key, machine_no, payload, now),
                )
            placeholders = ",".join("?" for _ in incident_ids)
            connection.execute(
                f"""
                UPDATE alarm_incidents SET pdf_report_id = ?
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
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE alarm_reports
                SET status = 'completed', analysis_json = ?, html_relative_path = ?,
                    pdf_relative_path = ?, pdf_filename = ?, file_size = ?, sha256 = ?,
                    model_status = ?, error_message = NULL, completed_at = ?
                WHERE report_id = ?
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
                ),
            )

    def fail_report(self, report_id: str, error: str) -> None:
        with self.connect() as connection:
            self.begin_immediate(connection)
            connection.execute(
                """
                UPDATE alarm_reports SET status = 'failed', error_message = ?
                WHERE report_id = ?
                """,
                (error[:500], report_id),
            )
            connection.execute(
                """
                UPDATE alarm_incidents SET pdf_report_id = NULL
                WHERE pdf_report_id = ?
                """,
                (report_id,),
            )

    def get_report(self, report_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM alarm_reports WHERE report_id = ?", (report_id,)
            ).fetchone()
        return self._decode_report(row)

    def list_reports(
        self, limit: int = 50, *, monitor_start_at: str | None = None
    ) -> list[dict[str, Any]]:
        join = ""
        where = ""
        parameters: tuple[Any, ...]
        if monitor_start_at:
            join = "JOIN alarm_incidents i ON i.pdf_report_id = r.report_id"
            where = "WHERE i.first_start_time >= ?"
            parameters = (monitor_start_at, limit)
        else:
            parameters = (limit,)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT DISTINCT r.* FROM alarm_reports r
                {join} {where}
                ORDER BY r.created_at DESC, r.report_id DESC LIMIT ?
                """,
                parameters,
            ).fetchall()
        return [self._decode_report(row) for row in rows]

    def list_incidents(
        self,
        status: str | None,
        limit: int = 100,
        *,
        monitor_start_at: str | None = None,
    ) -> list[dict[str, Any]]:
        conditions: list[str] = []
        values: list[Any] = []
        if status:
            conditions.append("status = ?")
            values.append(status)
        if monitor_start_at:
            conditions.append("first_start_time >= ?")
            values.append(monitor_start_at)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        values.append(limit)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM alarm_incidents {where}
                ORDER BY first_seen_at DESC, incident_id DESC LIMIT ?
                """,
                tuple(values),
            ).fetchall()
        return [dict(row) for row in rows]

    def page_incidents(
        self,
        *,
        monitor_start_at: str,
        status: str | None,
        severity: str | None,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        base_condition = "first_start_time >= ?"
        filtered_conditions = [base_condition]
        filtered_values: list[Any] = [monitor_start_at]
        if status:
            filtered_conditions.append("status = ?")
            filtered_values.append(status)
        if severity:
            filtered_conditions.append("severity = ?")
            filtered_values.append(severity)
        filtered_where = " AND ".join(filtered_conditions)

        with self.connect() as connection:
            summary = connection.execute(
                f"""
                SELECT
                    COUNT(*) AS all_total,
                    SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS open_count,
                    SUM(CASE WHEN severity = 'critical' THEN 1 ELSE 0 END) AS critical_count,
                    SUM(CASE WHEN severity = 'high' THEN 1 ELSE 0 END) AS high_count,
                    SUM(CASE WHEN severity = 'warning' THEN 1 ELSE 0 END) AS warning_count
                FROM alarm_incidents WHERE {base_condition}
                """,
                (monitor_start_at,),
            ).fetchone()
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM alarm_incidents WHERE {filtered_where}",
                    tuple(filtered_values),
                ).fetchone()[0]
            )
            page_count = max(1, (total + page_size - 1) // page_size)
            current_page = min(page, page_count)
            rows = connection.execute(
                f"""
                SELECT * FROM alarm_incidents WHERE {filtered_where}
                ORDER BY first_seen_at DESC, incident_id DESC LIMIT ? OFFSET ?
                """,
                (*filtered_values, page_size, (current_page - 1) * page_size),
            ).fetchall()
            dominant_status = (
                "AND status = 'open'" if int(summary["open_count"] or 0) else ""
            )
            dominant = connection.execute(
                f"""
                SELECT COALESCE(NULLIF(alarm_class, ''), NULLIF(alarm_desc, ''), '其他异常') AS label,
                       COUNT(*) AS count
                FROM alarm_incidents
                WHERE {base_condition}
                  {dominant_status}
                GROUP BY label ORDER BY count DESC, label LIMIT 1
                """,
                (monitor_start_at,),
            ).fetchone()

        return {
            "items": [dict(row) for row in rows],
            "page": current_page,
            "page_size": page_size,
            "page_count": page_count,
            "total": total,
            "all_total": int(summary["all_total"] or 0),
            "open_count": int(summary["open_count"] or 0),
            "severity_counts": {
                "critical": int(summary["critical_count"] or 0),
                "high": int(summary["high_count"] or 0),
                "warning": int(summary["warning_count"] or 0),
            },
            "dominant_alarm": dict(dominant) if dominant else None,
        }

    @staticmethod
    def _decode_report(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        item["incident_ids"] = json.loads(item.pop("incident_ids_json"))
        item["analysis"] = (
            json.loads(item.pop("analysis_json")) if item.get("analysis_json") else None
        )
        item.pop("analysis_json", None)
        return item
