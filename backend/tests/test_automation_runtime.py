from dataclasses import replace
from datetime import datetime
from threading import Event, Lock
from zoneinfo import ZoneInfo

from app.config import settings
from app.services.automation_runtime import AutomationRuntime
from app.services.automation_service import AutomationService
from app.services.email_service import EmailService
from app.workers.report_worker import ReportWorker


def test_email_service_uses_latest_smtp_configuration_per_delivery(monkeypatch) -> None:
    initial = replace(
        settings,
        smtp_enabled=True,
        smtp_host="smtp.old.example",
        smtp_port=587,
        smtp_username="old-user",
        smtp_password="old-secret",
        smtp_from_email="old@example.com",
        smtp_use_tls=True,
        smtp_use_ssl=False,
    )
    runtime = AutomationRuntime(initial)
    service = EmailService(initial, runtime=runtime)
    calls = []

    class FakeSMTP:
        def __init__(self, host, port, *, timeout):
            self.call = {"host": host, "port": port, "timeout": timeout}
            calls.append(self.call)

        def __enter__(self):
            if len(calls) == 1:
                runtime.apply(
                    smtp_username="changed-during-send",
                    smtp_password="changed-during-send",
                    smtp_from_email="changed@example.com",
                )
            return self

        def __exit__(self, *_args):
            return False

        def starttls(self):
            self.call["tls"] = True

        def login(self, username, password):
            self.call["login"] = (username, password)

        def send_message(self, message):
            self.call["from"] = message["From"]

    monkeypatch.setattr("app.services.email_service.smtplib.SMTP", FakeSMTP)
    monkeypatch.setattr("app.services.email_service.smtplib.SMTP_SSL", FakeSMTP)
    delivery = {
        "recipients": ["manager@example.com"],
        "subject": "日报",
        "schedule_name": "车间日报",
        "report_date": "2026-09-23",
    }

    service.send(delivery, None)
    runtime.apply(
        smtp_host="smtp.new.example",
        smtp_port=465,
        smtp_username="new-user",
        smtp_password="new-secret",
        smtp_from_email="new@example.com",
        smtp_use_tls=False,
        smtp_use_ssl=True,
    )
    service.send(delivery, None)

    assert calls[0]["host"] == "smtp.old.example"
    assert calls[0]["login"] == ("old-user", "old-secret")
    assert calls[0]["tls"] is True
    assert "old@example.com" in calls[0]["from"]
    assert calls[1]["host"] == "smtp.new.example"
    assert calls[1]["port"] == 465
    assert calls[1]["login"] == ("new-user", "new-secret")
    assert "tls" not in calls[1]
    assert "new@example.com" in calls[1]["from"]


def test_automation_enable_and_retry_delay_use_live_configuration() -> None:
    initial = replace(
        settings, automation_enabled=False, email_retry_base_seconds=60
    )
    runtime = AutomationRuntime(initial)

    class Repository:
        scans = 0
        retry_at = None

        def due_schedules(self, _now):
            self.scans += 1
            return []

        def claim_delivery(self, _now):
            return {"delivery_id": "delivery-1", "attempt_count": 2, "attach_report": False}

        def mark_delivery_failed(self, _delivery, *, error, next_attempt_at, now):
            self.retry_at = next_attempt_at

    class FailingEmail:
        def send(self, _delivery, _attachment, *, settings_snapshot):
            raise ConnectionError("SMTP unavailable")

    repository = Repository()
    service = object.__new__(AutomationService)
    service.settings = initial
    service.runtime = runtime
    service.repository = repository
    service.email_service = FailingEmail()
    now = datetime(2026, 9, 23, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    assert service.process_due_schedules(now) == 0
    assert repository.scans == 0
    runtime.apply(automation_enabled=True, email_retry_base_seconds=10)
    assert service.process_due_schedules(now) == 0
    assert repository.scans == 1
    assert service.process_next_email(now) is True
    assert repository.retry_at == "2026-09-23T12:00:20+08:00"


def test_report_worker_rescans_after_live_interval_change() -> None:
    initial = replace(settings, automation_scan_seconds=3600, worker_poll_seconds=0.01)
    runtime = AutomationRuntime(initial)
    first_scan = Event()
    second_scan = Event()
    scan_lock = Lock()
    scans = 0

    class Metadata:
        def recover_interrupted_jobs(self):
            return None

        def claim_next_job(self, _now):
            return None

    class Automation:
        def process_due_schedules(self, _now):
            nonlocal scans
            with scan_lock:
                scans += 1
                if scans == 1:
                    first_scan.set()
                if scans == 2:
                    second_scan.set()

        def process_next_email(self, _now):
            return False

    worker = ReportWorker(
        initial, Metadata(), object(), automation_service=Automation(), runtime=runtime
    )
    try:
        worker.start()
        assert first_scan.wait(1)
        runtime.apply(automation_scan_seconds=1)
        assert second_scan.wait(1)
    finally:
        worker.stop()
    assert scans == 2
