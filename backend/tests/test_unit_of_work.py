from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.database.automation import AutomationRepository
from app.database.definitions import DefinitionRepository
from app.database.dispatch import DispatchRepository
from app.database.metadata import MetadataRepository
from app.database.sqlite import SQLiteDatabase
from app.database.unit_of_work import UnitOfWork
from app.database.workshop import WorkshopRepository


TZ = ZoneInfo("Asia/Shanghai")


def _repositories(tmp_path: Path):
    database = SQLiteDatabase(tmp_path / "metadata.db")
    metadata = MetadataRepository(database)
    workshop = WorkshopRepository(database)
    automation = AutomationRepository(database)
    definitions = DefinitionRepository(database)
    dispatches = DispatchRepository(database)
    metadata.initialize()
    workshop.initialize()
    automation.initialize()
    definitions.initialize()
    dispatches.initialize()
    return database, metadata, workshop, automation, definitions, dispatches


def test_every_repository_uses_the_same_connection_policy(tmp_path: Path) -> None:
    database, metadata, workshop, automation, definitions, dispatches = _repositories(tmp_path)

    for repository in (metadata, workshop, automation, definitions, dispatches):
        assert repository.database is database
        with repository.connect() as connection:
            assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 30_000
            assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_cross_repository_unit_of_work_rolls_back_all_changes(tmp_path: Path) -> None:
    database, metadata, workshop, automation, _definitions, _dispatches = _repositories(tmp_path)
    now = datetime(2026, 8, 25, 10, 0, tzinfo=TZ).isoformat(timespec="seconds")
    admin = workshop.create_user(
        username="admin",
        display_name="管理员",
        password="correct-horse-battery-staple",
        role="admin",
        now=now,
    )
    schedule = automation.create_schedule(
        {
            "name": "日报",
            "report_type": "equipment",
            "frequency": "daily",
            "run_time": "18:00",
            "weekday": None,
            "month_day": None,
            "report_period": "current_day",
            "recipients": ["manager@example.com"],
            "subject_template": "日报",
            "chart_type": "bar_line",
            "attach_report": True,
            "max_attempts": 2,
            "enabled": True,
            "next_run_at": now,
        },
        user=admin,
        now=now,
    )
    run = automation.reserve_run(
        schedule,
        scheduled_for=now,
        report_date="2026-08-25",
        trigger_type="manual",
        now=now,
    )
    assert run is not None

    with pytest.raises(RuntimeError, match="simulate failure"):
        with UnitOfWork(database) as unit:
            unit.metadata.create_job(
                report_id="rpt_atomic",
                report_date="2026-08-25",
                project_code=None,
                ship_no=None,
                request_id="test",
                created_at=now,
                report_type="equipment",
            )
            unit.automation.attach_report(run["run_id"], "rpt_atomic", now=now)
            raise RuntimeError("simulate failure")

    assert metadata.get_job("rpt_atomic") is None
    saved_run = automation.get_run(run["run_id"])
    assert saved_run is not None
    assert saved_run["report_id"] is None
    assert saved_run["status"] == "queued"


def test_failed_cross_repository_link_does_not_leave_orphan_job(
    tmp_path: Path,
) -> None:
    database, metadata, _workshop, _automation, _definitions, _dispatches = _repositories(tmp_path)

    with pytest.raises(LookupError):
        with UnitOfWork(database) as unit:
            unit.metadata.create_job(
                report_id="rpt_orphan",
                report_date="2026-08-25",
                project_code=None,
                ship_no=None,
                request_id="test",
                created_at="2026-08-25T10:00:00+08:00",
                report_type="equipment",
            )
            unit.automation.attach_report(
                "missing-run", "rpt_orphan", now="2026-08-25T10:00:00+08:00"
            )

    assert metadata.get_job("rpt_orphan") is None
