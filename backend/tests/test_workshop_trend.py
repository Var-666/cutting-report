from datetime import datetime
from types import SimpleNamespace

from app.api.workshop import _workshop_trend


def _row(stamp: str, *, project: str, confirmed: bool):
    return SimpleNamespace(
        production_time=datetime.fromisoformat(stamp),
        project_code=project,
        confirmed_by="工人" if confirmed else None,
    )


def test_workshop_trend_uses_real_daily_records() -> None:
    trend = _workshop_trend(
        [
            _row("2026-09-16T08:00:00", project="A001", confirmed=True),
            _row("2026-09-17T08:00:00", project="A001", confirmed=False),
            _row("2026-09-17T09:00:00", project="A002", confirmed=True),
        ]
    )

    assert trend == [
        {
            "report_date": "2026-09-16",
            "task_count": 1,
            "pending_count": 0,
            "confirmed_count": 1,
            "project_count": 1,
            "confirmation_rate": 100.0,
        },
        {
            "report_date": "2026-09-17",
            "task_count": 2,
            "pending_count": 1,
            "confirmed_count": 1,
            "project_count": 2,
            "confirmation_rate": 50.0,
        },
    ]
