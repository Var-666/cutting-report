from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.database.automation import AutomationRepository
from app.database.definitions import DefinitionRepository
from app.database.dispatch import DispatchRepository
from app.database.metadata import MetadataRepository
from app.database.unit_of_work import UnitOfWork
from app.database.workshop import WorkshopRepository


TZ = ZoneInfo("Asia/Shanghai")


def _repositories(database):
    metadata = MetadataRepository(database, device_id='Cutter01')
    workshop = WorkshopRepository(database, scope_machine_no='Cutter01')
    automation = AutomationRepository(database)
    definitions = DefinitionRepository(database)
    dispatches = DispatchRepository(database)
    definitions.initialize()
    dispatches.initialize()
    return database, metadata, workshop, automation, definitions, dispatches


def test_every_repository_uses_the_same_connection_policy(state_database) -> None:
    database, metadata, workshop, automation, definitions, dispatches = _repositories(state_database)

    for repository in (metadata, workshop, automation, definitions, dispatches):
        assert repository.database is database
        with repository.connect() as connection:
            assert connection.execute('SELECT current_user AS name').fetchone()['name'] == 'cutting_app'
            assert connection.info.dbname == database.identity[2]



def test_cross_repository_unit_of_work_rolls_back_all_changes(state_database) -> None:
    database, metadata, workshop, automation, _definitions, _dispatches = _repositories(state_database)
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
            "device_id": "Cutter01",
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
        with UnitOfWork(database, device_id="Cutter01", scope_machine_no="Cutter01") as unit:
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
    state_database,
) -> None:
    database, metadata, _workshop, _automation, _definitions, _dispatches = _repositories(state_database)

    with pytest.raises(LookupError):
        with UnitOfWork(database, device_id="Cutter01", scope_machine_no="Cutter01") as unit:
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


def test_expected_constraint_failure_does_not_end_outer_transaction(state_database):
    now = '2026-11-04T08:00:00+08:00'
    with UnitOfWork(state_database) as unit:
        unit.users.create_user(username='same', display_name='工人', password='test-password', role='worker', now=now)
        with pytest.raises(ValueError, match='已存在'):
            unit.users.create_user(username='same', display_name='重复', password='test-password', role='worker', now=now)
        unit.users.create_user(username='kept', display_name='保留', password='test-password', role='worker', now=now)
    with state_database.connect() as connection:
        assert connection.execute('SELECT username FROM users ORDER BY username').fetchall() == [{'username':'kept'}, {'username':'same'}]
