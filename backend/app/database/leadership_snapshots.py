from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from app.database.sqlite import SQLiteDatabase, SQLiteRepository
from app.database.device_migrations import rebuild_table
from app.models.domain import LeadershipPeriodRecord, ProjectDistributionRecord


@dataclass(slots=True)
class LeadershipMonthSnapshot:
    report_month: date
    daily_records: list[LeadershipPeriodRecord]
    project_distribution: list[ProjectDistributionRecord]
    total: LeadershipPeriodRecord
    data_as_of: datetime
    refreshed_at: datetime
    expires_at: datetime
    query_duration_ms: int
    source_fingerprint: str
    snapshot_version: int


def _period_to_dict(record: LeadershipPeriodRecord) -> dict:
    return {
        "period_start": record.period_start.isoformat(),
        "task_count": record.task_count,
        "cut_length_m": str(record.cut_length_m),
        "groove_length_m": str(record.groove_length_m),
        "work_time_seconds": record.work_time_seconds,
        "remark": record.remark,
    }


def _period_from_dict(value: dict) -> LeadershipPeriodRecord:
    return LeadershipPeriodRecord(
        period_start=date.fromisoformat(value["period_start"]),
        task_count=int(value.get("task_count") or 0),
        cut_length_m=Decimal(str(value.get("cut_length_m") or 0)),
        groove_length_m=Decimal(str(value.get("groove_length_m") or 0)),
        work_time_seconds=int(value.get("work_time_seconds") or 0),
        remark=value.get("remark"),
    )


def _project_to_dict(record: ProjectDistributionRecord) -> dict:
    return {
        "project_name": record.project_name,
        "task_count": record.task_count,
        "cut_length_m": str(record.cut_length_m),
        "work_time_seconds": record.work_time_seconds,
    }


def _project_from_dict(value: dict) -> ProjectDistributionRecord:
    return ProjectDistributionRecord(
        project_name=str(value.get("project_name") or "未标注工程"),
        task_count=int(value.get("task_count") or 0),
        cut_length_m=Decimal(str(value.get("cut_length_m") or 0)),
        work_time_seconds=int(value.get("work_time_seconds") or 0),
    )


class LeadershipSnapshotRepository(SQLiteRepository):
    def __init__(self, database: SQLiteDatabase) -> None:
        super().__init__(database)

    def initialize(self) -> None:
        from app.database.migrations import apply_migrations
        apply_migrations(self.database, names=("005_leadership_snapshots",))

    def get(
        self,
        report_month: date,
        *,
        source_fingerprint: str,
        snapshot_version: int,
    ) -> LeadershipMonthSnapshot | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM leadership_month_snapshots
                WHERE report_month = ?
                  AND source_fingerprint = ?
                  AND snapshot_version = ?
                """,
                (report_month.isoformat(), source_fingerprint, snapshot_version),
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def get_many(
        self,
        report_months: list[date],
        *,
        source_fingerprint: str,
        snapshot_version: int,
    ) -> dict[date, LeadershipMonthSnapshot]:
        if not report_months:
            return {}
        placeholders = ",".join("?" for _ in report_months)
        parameters = [month.isoformat() for month in report_months]
        parameters.extend((source_fingerprint, snapshot_version))
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM leadership_month_snapshots
                WHERE report_month IN ({placeholders})
                  AND source_fingerprint = ?
                  AND snapshot_version = ?
                """,
                tuple(parameters),
            ).fetchall()
        snapshots = [self._from_row(row) for row in rows]
        return {snapshot.report_month: snapshot for snapshot in snapshots}

    def upsert_many(self, snapshots: list[LeadershipMonthSnapshot]) -> None:
        if not snapshots:
            return
        rows = [
            (
                snapshot.report_month.isoformat(),
                json.dumps(
                    [_period_to_dict(item) for item in snapshot.daily_records],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                json.dumps(
                    [_project_to_dict(item) for item in snapshot.project_distribution],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                json.dumps(
                    _period_to_dict(snapshot.total),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                snapshot.data_as_of.isoformat(),
                snapshot.refreshed_at.isoformat(),
                snapshot.expires_at.isoformat(),
                snapshot.query_duration_ms,
                snapshot.source_fingerprint,
                snapshot.snapshot_version,
            )
            for snapshot in snapshots
        ]
        with self.connect() as connection:
            connection.executemany(
                """
                INSERT INTO leadership_month_snapshots (
                    report_month, daily_json, project_json, total_json,
                    data_as_of, refreshed_at, expires_at, query_duration_ms,
                    source_fingerprint, snapshot_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(report_month, source_fingerprint) DO UPDATE SET
                    daily_json = excluded.daily_json,
                    project_json = excluded.project_json,
                    total_json = excluded.total_json,
                    data_as_of = excluded.data_as_of,
                    refreshed_at = excluded.refreshed_at,
                    expires_at = excluded.expires_at,
                    query_duration_ms = excluded.query_duration_ms,
                    source_fingerprint = excluded.source_fingerprint,
                    snapshot_version = excluded.snapshot_version
                """,
                rows,
            )

    @staticmethod
    def _from_row(row) -> LeadershipMonthSnapshot:
        return LeadershipMonthSnapshot(
            report_month=date.fromisoformat(row["report_month"]),
            daily_records=[
                _period_from_dict(item) for item in json.loads(row["daily_json"])
            ],
            project_distribution=[
                _project_from_dict(item) for item in json.loads(row["project_json"])
            ],
            total=_period_from_dict(json.loads(row["total_json"])),
            data_as_of=datetime.fromisoformat(row["data_as_of"]),
            refreshed_at=datetime.fromisoformat(row["refreshed_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]),
            query_duration_ms=int(row["query_duration_ms"]),
            source_fingerprint=row["source_fingerprint"],
            snapshot_version=int(row["snapshot_version"]),
        )
