from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.database.automation import AutomationRepository
from app.database.metadata import MetadataRepository
from app.database.workshop import WorkshopRepository
from app.services.automation_service import next_run_at, report_date_for


TZ = ZoneInfo("Asia/Shanghai")


def test_daily_weekly_and_monthly_next_run() -> None:
    after = datetime(2026, 8, 24, 18, 30, tzinfo=TZ)

    assert next_run_at(
        {"frequency": "daily", "run_time": "18:00"}, after
    ) == datetime(2026, 8, 25, 18, 0, tzinfo=TZ)
    assert next_run_at(
        {"frequency": "weekly", "run_time": "09:30", "weekday": 3}, after
    ) == datetime(2026, 8, 26, 9, 30, tzinfo=TZ)
    assert next_run_at(
        {"frequency": "monthly", "run_time": "08:00", "month_day": 5}, after
    ) == datetime(2026, 9, 5, 8, 0, tzinfo=TZ)


def test_report_period_resolves_without_locale_assumptions() -> None:
    scheduled_for = datetime(2026, 1, 1, 7, 0, tzinfo=TZ)

    assert report_date_for({"report_period": "current_day"}, scheduled_for) == date(2026, 1, 1)
    assert report_date_for({"report_period": "previous_day"}, scheduled_for) == date(2025, 12, 31)
    assert report_date_for({"report_period": "current_month"}, scheduled_for) == date(2026, 1, 1)
    assert report_date_for({"report_period": "previous_month"}, scheduled_for) == date(2025, 12, 1)


def test_schedule_and_email_retry_history_are_persisted(tmp_path) -> None:
    database_path = tmp_path / "metadata.db"
    metadata = MetadataRepository(database_path, device_id='Cutter01')
    metadata.initialize()
    workshop = WorkshopRepository(database_path, scope_machine_no='Cutter01')
    workshop.initialize()
    repository = AutomationRepository(database_path)
    repository.initialize()
    now = "2026-08-24T18:00:00+08:00"
    admin = workshop.create_user(
        username="admin",
        display_name="管理员",
        password="correct-horse-battery-staple",
        role="admin",
        now=now,
    )
    schedule = repository.create_schedule(
        {
            "device_id": "Cutter01",
            "name": "每日车间报表",
            "report_type": "workshop",
            "frequency": "daily",
            "run_time": "18:00",
            "weekday": None,
            "month_day": None,
            "report_period": "previous_day",
            "recipients": ["manager@example.com"],
            "subject_template": "{report_name} - {report_date}",
            "chart_type": "bar_line",
            "attach_report": True,
            "max_attempts": 2,
            "enabled": True,
            "next_run_at": now,
        },
        user=admin,
        now=now,
    )
    run = repository.reserve_run(
        schedule,
        scheduled_for=now,
        report_date="2026-08-23",
        trigger_type="scheduled",
        now=now,
        next_run_at="2026-08-25T18:00:00+08:00",
    )
    assert run is not None

    metadata.create_job(
        report_id="rpt_automation_test",
        report_date="2026-08-23",
        project_code=None,
        ship_no=None,
        request_id="automation-test",
        created_at=now,
        report_type="workshop",
    )
    metadata.save_report(
        report_id="rpt_automation_test",
        filename="report.html",
        relative_path="report.html",
        file_size=100,
        sha256="0" * 64,
        task_count=10,
        generated_at=now,
        system_version="test",
    )
    repository.attach_report(run["run_id"], "rpt_automation_test", now=now)
    delivery = repository.enqueue_delivery(
        "rpt_automation_test", subject="每日车间报表", now=now
    )
    assert delivery is not None

    first = repository.claim_delivery(now)
    assert first is not None and first["attempt_count"] == 1
    repository.mark_delivery_failed(
        first,
        error="SMTP timeout",
        next_attempt_at="2026-08-24T18:01:00+08:00",
        now=now,
    )
    second = repository.claim_delivery("2026-08-24T18:02:00+08:00")
    assert second is not None and second["attempt_count"] == 2
    repository.mark_delivery_failed(
        second,
        error="SMTP timeout again",
        next_attempt_at="2026-08-24T18:04:00+08:00",
        now="2026-08-24T18:02:00+08:00",
    )

    items, total = repository.list_deliveries(page=1, page_size=20)
    assert total == 1
    assert items[0]["status"] == "failed"
    assert items[0]["attempt_count"] == 2
    assert repository.summary()["delivery_failed"] == 1

    assert repository.retry_delivery(delivery["delivery_id"], now=now)
    retried = repository.claim_delivery(now)
    assert retried is not None and retried["attempt_count"] == 1
    repository.mark_delivery_sent(retried, message_id="<message@example.com>", now=now)
    assert repository.summary()["delivery_sent"] == 1
