"""Shared recovery workflow, registered through the test conftest plugin list."""
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from tests.line_fixtures import cutting_settings, as_line_services
from app.database.automation import AutomationRepository
from app.database.metadata import MetadataRepository
from app.database.workshop import WorkshopRepository
from app.services.automation_service import AutomationService

NOW = datetime(2026, 9, 30, 9, tzinfo=ZoneInfo("Asia/Shanghai"))
STAMP = NOW.isoformat(timespec="seconds")


@pytest.fixture
def workflow(tmp_path, state_database):
    path = state_database
    metadata = MetadataRepository(path, device_id='Cutter01')
    workshop = WorkshopRepository(path, scope_machine_no='Cutter01')
    repository = AutomationRepository(path)
    user = workshop.create_user(username="admin", display_name="管理员", password="test-password",
                                role="admin", now=STAMP)
    schedule = repository.create_schedule({
            "device_id": "Cutter01",
        "name": "领导报表", "report_type": "leadership", "frequency": "daily",
        "run_time": "09:00", "weekday": None, "month_day": None,
        "report_period": "previous_day", "recipients": ["manager@example.com"],
        "subject_template": "{report_name} {report_date}", "chart_type": "bar_line",
        "attach_report": False, "max_attempts": 2, "enabled": True, "next_run_at": STAMP,
    }, user=user, now=STAMP)
    run = repository.reserve_run(schedule, scheduled_for=STAMP, report_date="2026-09-29",
                                 trigger_type="scheduled", now=STAMP,
                                 next_run_at="2026-10-01T09:00:00+08:00")
    cfg = cutting_settings(project_root=tmp_path)
    service = AutomationService(cfg, repository, metadata, object(),
        line_services=as_line_services({"Cutter01": SimpleNamespace(settings=cfg, pipeline=object())}))
    return repository, metadata, service, run


def complete_report(metadata, report_id):
    metadata.save_report(report_id=report_id, filename="r.html", relative_path="r.html",
                         file_size=1, sha256="a" * 64, task_count=1, generated_at=STAMP,
                         system_version="test")
    metadata.update_status(report_id, "completed", "completed", finished_at=STAMP)
