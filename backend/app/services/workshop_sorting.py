from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


def _timestamp(value: datetime, timezone: str) -> float:
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo(timezone))
    return value.timestamp()


def sort_workshop_by_recency(records: list, timezone: str) -> list:
    """Return newest production tasks first and keep missing dates last."""
    return sorted(
        records,
        key=lambda row: (
            row.production_time is None,
            -_timestamp(row.production_time, timezone)
            if row.production_time is not None
            else 0,
            str(row.task_id),
        ),
    )
