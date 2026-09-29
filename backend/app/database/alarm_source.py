from __future__ import annotations

from typing import Any, Iterable

from app.config import Settings
from app.database.sqlserver import SqlServerClient


ALARM_COLUMNS = """
    ALARM_INFO_ID AS alarm_info_id,
    MACHINE_NO AS machine_no,
    START_TIME AS start_time,
    REMOVE_TIME AS remove_time,
    ALARM_DESC AS alarm_desc,
    ALARM_CODE AS alarm_code,
    ALARM_CLASS AS alarm_class,
    ALARM_TYPE AS alarm_type
"""


class SqlServerAlarmSource:
    """Read Cutter alarms without modifying the equipment database."""

    def __init__(self, settings: Settings, client: SqlServerClient | None = None) -> None:
        self.settings = settings
        self.client = client or SqlServerClient(settings, source="production")

    @property
    def machine_nos(self) -> tuple[str, ...]:
        return self.settings.alarm_monitor_machine_nos

    def fetch_new(
        self, after_id: int, through_id: int, limit: int
    ) -> list[dict[str, Any]]:
        if not self.machine_nos:
            return []
        safe_limit = min(5000, max(1, int(limit)))
        placeholders = ",".join("?" for _ in self.machine_nos)
        return self.client.fetch_all(
            f"""
            SELECT TOP {safe_limit} {ALARM_COLUMNS}
            FROM dbo.alarm_info
            WHERE ALARM_INFO_ID > ?
              AND ALARM_INFO_ID <= ?
              AND TRY_CONVERT(datetime2, NULLIF(LTRIM(RTRIM(START_TIME)), '')) >= ?
              AND LTRIM(RTRIM(MACHINE_NO)) IN ({placeholders})
              AND UPPER(LTRIM(RTRIM(COALESCE(ALARM_TYPE, '')))) = 'CUTTER'
            ORDER BY ALARM_INFO_ID
            """,
            (
                after_id,
                through_id,
                self.settings.alarm_monitor_start_at,
                *self.machine_nos,
            ),
        )

    def latest_id(self) -> int:
        if not self.machine_nos:
            return 0
        placeholders = ",".join("?" for _ in self.machine_nos)
        rows = self.client.fetch_all(
            f"""
            SELECT COALESCE(MAX(ALARM_INFO_ID), 0) AS latest_id
            FROM dbo.alarm_info
            WHERE LTRIM(RTRIM(MACHINE_NO)) IN ({placeholders})
              AND UPPER(LTRIM(RTRIM(COALESCE(ALARM_TYPE, '')))) = 'CUTTER'
            """,
            tuple(self.machine_nos),
        )
        return int(rows[0]["latest_id"] or 0) if rows else 0

    def fetch_by_ids(self, source_ids: Iterable[int]) -> list[dict[str, Any]]:
        ids = [int(value) for value in source_ids]
        rows: list[dict[str, Any]] = []
        # Stay well below SQL Server's 2100-parameter limit.
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            placeholders = ",".join("?" for _ in chunk)
            rows.extend(
                self.client.fetch_all(
                    f"""
                    SELECT {ALARM_COLUMNS}
                    FROM dbo.alarm_info
                    WHERE ALARM_INFO_ID IN ({placeholders})
                    """,
                    tuple(chunk),
                )
            )
        return rows
