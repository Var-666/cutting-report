from __future__ import annotations

import hashlib
import json
from psycopg import Connection
import unicodedata
from datetime import datetime, timedelta

from typing import Any, Iterable
from uuid import uuid4

from app.database.postgres import PostgresDatabase, PostgresRepository, lock_key
from app.modules.alarms.leases import require_alarm_lease
from app.modules.alarms.report_repository import AlarmReportRepository


def scoped_fingerprint(source_name: str, value: str) -> str:
    return hashlib.sha256(f'{source_name}|{value}'.encode()).hexdigest()


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


class AlarmRepository(PostgresRepository):
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
        self.reports = AlarmReportRepository(database, connection=connection, machine_no=machine_no, source_name=source_name)

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO alarm_sync_state(source_name, last_alarm_id)
                VALUES (%s, 0) ON CONFLICT(source_name) DO NOTHING
                """, (self.source_name,)
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

    def get_cursor(self, source_name: str | None = None) -> int:
        source_name = source_name or self.source_name
        with self.connect() as connection:
            row = connection.execute(
                "SELECT last_alarm_id FROM alarm_sync_state WHERE source_name = %s",
                (source_name,),
            ).fetchone()
        return int(row['last_alarm_id']) if row else 0

    def get_sync_state(self, source_name: str | None = None) -> dict[str, Any]:
        source_name = source_name or self.source_name
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT source_name, last_alarm_id, last_success_at, monitor_start_at,
                       open_recheck_after_id, history_recheck_after_id
                FROM alarm_sync_state WHERE source_name = %s
                """,
                (source_name,),
            ).fetchone()
        return dict(row) if row else {
            "source_name": source_name,
            "last_alarm_id": 0,
            "last_success_at": None,
            "monitor_start_at": None,
            "open_recheck_after_id": 0,
            "history_recheck_after_id": 0,
        }

    def configure_monitoring_window(
        self,
        monitor_start_at: str,
        *,
        force: bool = False,
        source_name: str | None = None,
        lease_owner: str | None = None,
        lease_now: datetime | None = None,
    ) -> bool:
        source_name = source_name or self.source_name
        with self.connect() as connection:
            lock_key(connection, "alarm-source:" + source_name)
            if lease_owner is not None:
                if lease_now is None:
                    raise ValueError("租约保护的监控窗口修改必须提供当前时间")
                require_alarm_lease(connection, lease_owner, lease_now, source_name)
            row = connection.execute(
                "SELECT monitor_start_at FROM alarm_sync_state WHERE source_name = %s",
                (source_name,),
            ).fetchone()
            changed = force or row is None or row["monitor_start_at"] != monitor_start_at
            if not changed:
                return False
            connection.execute(
                """
                INSERT INTO alarm_sync_state(
                    source_name, last_alarm_id, last_success_at, monitor_start_at
                ) VALUES (%s, 0, NULL, %s)
                ON CONFLICT(source_name) DO UPDATE SET
                    last_alarm_id = 0,
                    open_recheck_after_id = 0,
                    history_recheck_after_id = 0,
                    last_success_at = NULL,
                    monitor_start_at = excluded.monitor_start_at
                """,
                (source_name, monitor_start_at),
            )
        return True

    def list_open_source_ids(
        self, limit: int = 2000, *, monitor_start_at: str | None = None,
        after_id: int = 0,
    ) -> list[int]:
        where = "WHERE is_open = 1 AND source_alarm_id > %s AND source_name = %s"
        if self.machine_no is not None:
            where += " AND incident_id IN (SELECT incident_id FROM alarm_incidents WHERE sqlite_nocase(machine_no) = sqlite_nocase(%s))"
        parameters: tuple[Any, ...]
        if monitor_start_at:
            where += " AND start_time >= %s"
            parameters = (after_id, self.source_name, *((self.machine_no,) if self.machine_no is not None else ()), monitor_start_at, limit)
        else:
            parameters = (after_id, self.source_name, *((self.machine_no,) if self.machine_no is not None else ()), limit)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT source_alarm_id FROM alarm_source_events
                {where} ORDER BY source_alarm_id LIMIT %s
                """,
                parameters,
            ).fetchall()
        return [int(row['source_alarm_id']) for row in rows]

    def try_acquire_lease(
        self,
        owner: str,
        now: datetime,
        *,
        ttl_seconds: int = 600,
        source_name: str | None = None,
    ) -> bool:
        source_name = source_name or self.source_name
        now_text = now.isoformat(timespec="seconds")
        until = (now + timedelta(seconds=ttl_seconds)).isoformat(timespec="seconds")
        with self.connect() as connection:
            updated = connection.execute(
                """
                UPDATE alarm_sync_state
                SET lease_owner = %s, lease_until = %s
                WHERE source_name = %s
                  AND (lease_until IS NULL OR lease_until <= %s)
                """,
                (owner, until, source_name, now_text),
            )
            return updated.rowcount == 1

    def renew_lease(self, owner: str, now: datetime, *, ttl_seconds: int = 600,
                    source_name: str | None = None) -> bool:
        source_name = source_name or self.source_name
        now_text = now.isoformat(timespec="seconds")
        until = (now + timedelta(seconds=ttl_seconds)).isoformat(timespec="seconds")
        with self.connect() as connection:
            updated = connection.execute(
                """UPDATE alarm_sync_state SET lease_until = %s
                   WHERE source_name = %s AND lease_owner = %s AND lease_until > %s""",
                (until, source_name, owner, now_text),
            )
            return updated.rowcount == 1

    def release_lease(self, owner: str, source_name: str | None = None) -> None:
        source_name = source_name or self.source_name
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE alarm_sync_state SET lease_owner = NULL, lease_until = NULL
                WHERE source_name = %s AND lease_owner = %s
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
        source_name: str | None = None,
        open_recheck_after_id: int | None = None,
        history_recheck_after_id: int | None = None,
        expired_before: datetime | None = None,
        lease_owner: str | None = None,
        lease_now: datetime | None = None,
    ) -> dict[str, int]:
        """Persist events and the caller's sequential scan boundary atomically.

        Supplemental rechecks may contain larger IDs without proving that the
        intervening source rows were scanned; they must never advance cursor.
        """
        source_name = source_name or self.source_name
        now_text = now.isoformat(timespec="seconds")
        debounce_cutoff = (now - timedelta(minutes=max(0, debounce_minutes))).isoformat(
            timespec="seconds"
        )
        normalized = sorted(
            (dict(item) for item in records if self.machine_no is None or _text(item.get("machine_no")).casefold() == self.machine_no.casefold()),
            key=lambda item: int(item["alarm_info_id"]),
        )
        affected_incidents: set[str] = set()
        created_incidents = 0

        with self.connect() as connection:
            lock_key(connection, "alarm-source:" + source_name)
            if lease_owner is not None:
                require_alarm_lease(connection, lease_owner, lease_now or now, source_name)
            for record in normalized:
                source_alarm_id = int(record["alarm_info_id"])
                remove_time = _text(record.get("remove_time")) or None
                start_time = _text(record.get("start_time")) or None
                is_open = 0 if remove_time else 1
                source_hash = _source_hash(record)
                existing = connection.execute(
                    """
                    SELECT e.incident_id, e.source_hash, i.first_start_time
                    FROM alarm_source_events e
                    JOIN alarm_incidents i ON i.incident_id = e.incident_id
                    WHERE e.source_alarm_id = %s AND e.source_name = %s
                    """,
                    (source_alarm_id, source_name),
                ).fetchone()
                # History rechecks must not recreate expired, resolved events.
                # Existing open events still need their removal update persisted.
                if existing is None and remove_time and start_time and expired_before:
                    try:
                        started_at = datetime.fromisoformat(start_time)
                        if started_at.tzinfo is None:
                            started_at = started_at.replace(tzinfo=expired_before.tzinfo)
                        if started_at < expired_before:
                            continue
                    except ValueError:
                        pass
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
                        "DELETE FROM alarm_source_events WHERE source_alarm_id = %s AND source_name = %s",
                        (source_alarm_id, source_name),
                    )
                    existing = None
                if existing:
                    incident_id = str(existing["incident_id"])
                    record_changed = existing["source_hash"] != source_hash
                    if record_changed:
                        connection.execute(
                            """
                            UPDATE alarm_source_events
                            SET source_hash = %s, start_time = %s, remove_time = %s,
                                is_open = %s, last_seen_at = %s
                            WHERE source_alarm_id = %s AND source_name = %s
                            """,
                            (
                                source_hash,
                                start_time,
                                remove_time,
                                is_open,
                                now_text,
                                source_alarm_id,
                                source_name,
                            ),
                        )
                else:
                    record_changed = True
                    base_fingerprint = scoped_fingerprint(source_name, _fingerprint(record))
                    fingerprint = _window_fingerprint(
                        _fingerprint(record), monitor_start_at
                    )
                    fingerprint = scoped_fingerprint(source_name, fingerprint)
                    fingerprints = (
                        (base_fingerprint, fingerprint)
                        if fingerprint != base_fingerprint
                        else (fingerprint,)
                    )
                    placeholders = ",".join("%s" for _ in fingerprints)
                    incident_where = (
                        f"fingerprint IN ({placeholders}) AND status = 'open'"
                    )
                    incident_parameters: tuple[Any, ...] = fingerprints
                    if monitor_start_at:
                        incident_where += " AND first_start_time >= %s"
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
                            "AND status = 'closed' AND closed_at >= %s"
                        )
                        closed_parameters: tuple[Any, ...] = (
                            *fingerprints,
                            debounce_cutoff,
                        )
                        if monitor_start_at:
                            closed_where += " AND first_start_time >= %s"
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
                                SET status = 'open', closed_at = NULL, last_seen_at = %s
                                WHERE incident_id = %s
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
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'open', %s, %s, %s, %s, %s, %s)
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
                            source_name, source_alarm_id, incident_id, source_hash, start_time,
                            remove_time, is_open, first_seen_at, last_seen_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            source_name,
                            source_alarm_id,
                            incident_id,
                            source_hash,
                            start_time,
                            remove_time,
                            is_open,
                            now_text,
                            now_text,
                        ),
                    )
                if record_changed:
                    affected_incidents.add(incident_id)
                    connection.execute(
                        """
                        UPDATE alarm_incidents
                        SET last_seen_at = %s, latest_alarm_id = GREATEST(latest_alarm_id, %s),
                            latest_remove_time = COALESCE(%s, latest_remove_time),
                            first_start_time = CASE
                                WHEN %s::text IS NULL THEN first_start_time
                                WHEN first_start_time IS NULL OR %s < first_start_time THEN %s
                                ELSE first_start_time
                            END
                        WHERE incident_id = %s
                        """,
                        (
                            now_text,
                            source_alarm_id,
                            remove_time,
                            start_time,
                            start_time,
                            start_time,
                            incident_id,
                        ),
                    )

            for incident_id in affected_incidents:
                open_count = connection.execute(
                    """
                    SELECT COUNT(*) AS count FROM alarm_source_events
                    WHERE incident_id = %s AND is_open = 1
                    """,
                    (incident_id,),
                ).fetchone()['count']
                if open_count:
                    connection.execute(
                        """
                        UPDATE alarm_incidents SET status = 'open', closed_at = NULL
                        WHERE incident_id = %s
                        """,
                        (incident_id,),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE alarm_incidents
                        SET status = 'closed', closed_at = COALESCE(closed_at, %s)
                        WHERE incident_id = %s
                        """,
                        (now_text, incident_id),
                    )

            connection.execute(
                """
                UPDATE alarm_sync_state
                SET last_alarm_id = GREATEST(last_alarm_id, %s), last_success_at = %s,
                    open_recheck_after_id = COALESCE(%s, open_recheck_after_id),
                    history_recheck_after_id = COALESCE(%s, history_recheck_after_id)
                WHERE source_name = %s
                """,
                (cursor, now_text, open_recheck_after_id, history_recheck_after_id, source_name),
            )
            committed_cursor = int(connection.execute(
                "SELECT last_alarm_id FROM alarm_sync_state WHERE source_name = %s",
                (source_name,),
            ).fetchone()['last_alarm_id'])

        return {
            "records": len(normalized),
            "created_incidents": created_incidents,
            "last_alarm_id": committed_cursor,
        }

    def list_incidents(
        self,
        status: str | None,
        limit: int = 100,
        *,
        monitor_start_at: str | None = None,
    ) -> list[dict[str, Any]]:
        conditions: list[str] = []
        values: list[Any] = []
        if self.machine_no is not None:
            conditions.append("sqlite_nocase(machine_no) = sqlite_nocase(%s)")
            values.append(self.machine_no)
        if status:
            conditions.append("status = %s")
            values.append(status)
        if monitor_start_at:
            conditions.append("first_start_time >= %s")
            values.append(monitor_start_at)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        values.append(limit)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM alarm_incidents {where}
                ORDER BY first_seen_at DESC, incident_id DESC LIMIT %s
                """,
                tuple(values),
            ).fetchall()
        return [dict(row) for row in rows]

    def closing_incidents(self, machines: list[str], start: datetime, end: datetime) -> list[dict]:
        # Stored start times may use either a space or T before the time of day.
        with self.connect() as connection:
            rows = connection.execute("""
                SELECT i.first_start_time, i.severity
                FROM alarm_incidents i
                WHERE i.machine_no = ANY(%s) AND i.first_start_time >= %s AND i.first_start_time < %s
                  AND EXISTS (SELECT 1 FROM alarm_source_events e
                              WHERE e.incident_id = i.incident_id AND e.source_name = %s)
                """, (machines, start.date().isoformat(), (end.date() + timedelta(days=1)).isoformat(),
                      self.source_name)).fetchall()
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
        base_condition = "first_start_time >= %s"
        base_values: list[Any] = [monitor_start_at]
        if self.machine_no is not None:
            base_condition += " AND sqlite_nocase(machine_no) = sqlite_nocase(%s)"
            base_values.append(self.machine_no)
        filtered_conditions = [base_condition]
        filtered_values: list[Any] = list(base_values)
        if status:
            filtered_conditions.append("status = %s")
            filtered_values.append(status)
        if severity:
            filtered_conditions.append("severity = %s")
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
                tuple(base_values),
            ).fetchone()
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) AS count FROM alarm_incidents WHERE {filtered_where}",
                    tuple(filtered_values),
                ).fetchone()['count']
            )
            page_count = max(1, (total + page_size - 1) // page_size)
            current_page = min(page, page_count)
            rows = connection.execute(
                f"""
                SELECT i.*, r.status AS report_status,
                       r.error_message AS report_error_message,
                       CASE WHEN COALESCE(r.html_relative_path, '') != ''
                            THEN 1 ELSE 0 END AS report_has_html
                FROM (
                    SELECT * FROM alarm_incidents WHERE {filtered_where}
                    ORDER BY first_seen_at DESC, incident_id DESC LIMIT %s OFFSET %s
                ) i
                LEFT JOIN alarm_reports r ON r.report_id = i.pdf_report_id
                ORDER BY i.first_seen_at DESC, i.incident_id DESC
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
                tuple(base_values),
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
