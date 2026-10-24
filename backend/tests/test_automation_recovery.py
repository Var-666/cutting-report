from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.config import settings
from app.database.automation import AutomationRepository
from app.database.metadata import MetadataRepository
from app.database.workshop import WorkshopRepository
from app.services.automation_service import AutomationService


NOW = datetime(2026, 9, 30, 9, tzinfo=ZoneInfo("Asia/Shanghai"))
STAMP = NOW.isoformat(timespec="seconds")


@pytest.fixture
def workflow(tmp_path):
    path = tmp_path / "metadata.db"
    metadata = MetadataRepository(path, device_id='Cutter01')
    metadata.initialize()
    workshop = WorkshopRepository(path, scope_machine_no='Cutter01')
    workshop.initialize()
    repository = AutomationRepository(path)
    repository.initialize()
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
    service = AutomationService(replace(settings, project_root=tmp_path), repository, metadata, object())
    return repository, metadata, service, run


def complete_report(metadata, report_id):
    metadata.save_report(report_id=report_id, filename="r.html", relative_path="r.html",
                         file_size=1, sha256="a" * 64, task_count=1, generated_at=STAMP,
                         system_version="test")
    metadata.update_status(report_id, "completed", "completed", finished_at=STAMP)


def test_restart_recovers_reserved_run_and_completed_outbox_once(workflow):
    repository, metadata, service, run = workflow
    # Reservation committed and advanced the schedule, but the process died
    # before create_job. Recovery must use the durable run, not today's schedule.
    assert service.reconcile_runs(NOW) == 1
    report_id = repository.get_run(run["run_id"])["report_id"]
    assert metadata.get_job(report_id)["report_date"] == "2026-09-29"
    assert service.reconcile_runs(NOW) == 0
    complete_report(metadata, report_id)
    # The artifact committed, but the completion callback never ran.
    assert service.reconcile_runs(NOW) == 1
    assert service.reconcile_runs(NOW) == 0
    delivery = repository.claim_delivery(STAMP)
    assert delivery is not None
    repository.mark_delivery_sent(delivery, message_id="test-id", now=STAMP)
    repository.enqueue_delivery(report_id, subject="repeat", now=STAMP)
    assert repository.get_run(run["run_id"])["status"] == "sent"
    assert repository.delivery_for_report(report_id)["status"] == "sent"
    assert repository.claim_delivery(STAMP) is None


def test_concurrent_recovery_and_manual_enqueue_create_only_one_job(workflow):
    repository, metadata, service, run = workflow
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service._create_report_job(run, NOW), range(2)))
    assert sorted(results) == [False, True]
    with metadata.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM report_jobs").fetchone()[0] == 1
    assert repository.get_run(run["run_id"])["status"] == "generating"


def test_recovery_isolates_callback_failure_and_repairs_failed_run(workflow, monkeypatch):
    repository, metadata, service, run = workflow
    assert service._create_report_job(run, NOW)
    report_id = repository.get_run(run["run_id"])["report_id"]
    metadata.update_status(report_id, "failed", "failed", error_message="query failed")
    assert service.reconcile_runs(NOW) == 1
    assert repository.get_run(run["run_id"])["status"] == "failed"
    complete_report(metadata, report_id)
    original = service.report_completed
    monkeypatch.setattr(service, "report_completed", lambda *args: (_ for _ in ()).throw(RuntimeError("db outage")))
    assert service.reconcile_runs(NOW) == 0
    assert repository.delivery_for_report(report_id) is None
    monkeypatch.setattr(service, "report_completed", original)
    assert service.reconcile_runs(NOW) == 1


def test_unknown_device_fails_reserved_run_without_creating_a_job(workflow):
    repository, metadata, service, run = workflow
    with repository.connect() as connection:
        connection.execute("UPDATE scheduled_report_runs SET device_id='missing' WHERE run_id=?", (run['run_id'],))
    run = repository.get_run(run['run_id'])
    assert not service._create_report_job(run, NOW)
    assert repository.get_run(run['run_id'])['status'] == 'failed'
    assert metadata.list_reports(page=1, page_size=10)[1] == 0
