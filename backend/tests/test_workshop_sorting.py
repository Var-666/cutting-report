from datetime import datetime

from app.api.workshop import sort_workshop_records
from app.models.domain import WorkshopRecord


def _record(task_id: str, production_hour: int, confirmed_hour: int | None = None):
    return WorkshopRecord(
        task_id=task_id,
        project_code=None,
        ship_no=None,
        segment_name=None,
        program_no=task_id,
        cut_length=None,
        groove_length=None,
        work_time=None,
        production_time=datetime(2026, 8, 18, production_hour),
        confirmed_by="工人" if confirmed_hour is not None else None,
        confirmed_at=(
            datetime(2026, 8, 19, confirmed_hour)
            if confirmed_hour is not None
            else None
        ),
    )


def test_manager_sort_puts_latest_confirmations_before_unconfirmed_tasks():
    records = [
        _record("unconfirmed-late", 12),
        _record("confirmed-old", 9, 14),
        _record("unconfirmed-early", 8),
        _record("confirmed-new", 10, 16),
    ]

    ordered = sort_workshop_records(records, "manager")

    assert [row.task_id for row in ordered] == [
        "confirmed-new",
        "confirmed-old",
        "unconfirmed-early",
        "unconfirmed-late",
    ]


def test_worker_sort_puts_unconfirmed_tasks_first_in_production_time_order():
    records = [
        _record("confirmed-old", 9, 14),
        _record("unconfirmed-late", 12),
        _record("confirmed-new", 10, 16),
        _record("unconfirmed-early", 8),
    ]

    ordered = sort_workshop_records(records, "worker")

    assert [row.task_id for row in ordered] == [
        "unconfirmed-early",
        "unconfirmed-late",
        "confirmed-new",
        "confirmed-old",
    ]
