import smtplib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.config import settings
from app.database.automation import AutomationRepository
from app.services.email_service import (
    EmailService, EmailDeliveryFailure, EmailDeliveryUnknown, EmailRecipientsRefused,
)
from test_automation_recovery import workflow, complete_report, NOW, STAMP


def smtp_service(monkeypatch, responses, *, quit_error=False):
    sent = []

    class SMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            if quit_error:
                raise smtplib.SMTPServerDisconnected("QUIT disconnected")

        def send_message(self, message):
            sent.append(message)
            result = responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

    monkeypatch.setattr("app.services.email_service.smtplib.SMTP", SMTP)
    configured = replace(settings, smtp_enabled=True, smtp_host="smtp.example.com",
                         smtp_from_email="report@example.com", smtp_username="", smtp_use_tls=False,
                         smtp_use_ssl=False)
    return EmailService(configured), configured, sent


def delivery(recipients=None):
    return {"message_id": "<stable@test.local>", "recipients": recipients or ["a@example.com"],
            "subject": "report", "schedule_name": "daily", "report_date": "2026-09-29"}


@pytest.mark.parametrize("result,expected", [
    ({"b@example.com": (450, b"try later")}, EmailRecipientsRefused),
    (smtplib.SMTPRecipientsRefused({"a@example.com": (550, b"refused")}), EmailRecipientsRefused),
    (smtplib.SMTPDataError(451, b"refused DATA"), EmailDeliveryFailure),
    (TimeoutError("reply lost after DATA"), EmailDeliveryUnknown),
])
def test_smtp_acceptance_and_refusal_are_distinguished(monkeypatch, result, expected):
    email, _, sent = smtp_service(monkeypatch, [result])
    with pytest.raises(expected):
        email.send(delivery(["a@example.com", "b@example.com"]), None)
    assert sent[0]["Message-ID"] == "<stable@test.local>"


@pytest.mark.parametrize("response", [{}, {"a@example.com": (450, b"later")}])
def test_quit_failure_does_not_erase_received_delivery_result(monkeypatch, response):
    email, _, _ = smtp_service(monkeypatch, [response], quit_error=True)
    if response:
        with pytest.raises(EmailRecipientsRefused):
            email.send(delivery(), None)
    else:
        assert email.send(delivery(), None) == "<stable@test.local>"


def test_smtp_stages_share_one_wall_clock_budget(monkeypatch):
    clock = iter([0.0, 1.0, 2.0, 25.0])
    monkeypatch.setattr("app.services.email_service.monotonic", lambda: next(clock))
    sent = []

    class SMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def starttls(self):
            pass

        def login(self, *_args):
            pass

        def send_message(self, message):
            sent.append(message)
            return {}

    monkeypatch.setattr("app.services.email_service.smtplib.SMTP", SMTP)
    configured = replace(
        settings,
        smtp_enabled=True,
        smtp_host="smtp.example.com",
        smtp_from_email="report@example.com",
        smtp_username="user",
        smtp_password="secret",
        smtp_use_tls=True,
        smtp_use_ssl=False,
        smtp_timeout=20,
    )

    with pytest.raises(EmailDeliveryFailure, match="SMTP"):
        EmailService(configured).send(delivery(), None)
    assert sent == []


def ready(workflow, recipients=None):
    repository, metadata, service, run = workflow
    assert service._create_report_job(run, NOW)
    report_id = repository.get_run(run["run_id"])["report_id"]
    complete_report(metadata, report_id)
    service.report_completed(report_id, NOW)
    if recipients:
        import json
        with repository.connect() as connection:
            connection.execute("UPDATE email_deliveries SET recipients_json = ? WHERE report_id = ?",
                               (json.dumps(recipients), report_id))
    return repository, service, report_id


def test_partial_refusal_retries_only_rejected_recipients_with_same_message_id(workflow, monkeypatch):
    repository, service, report_id = ready(workflow, ["a@example.com", "b@example.com"])
    email, configured, sent = smtp_service(monkeypatch, [{"b@example.com": (450, b"later")}, {}])
    service.email_service = email
    service.runtime.apply(smtp_enabled=configured.smtp_enabled, smtp_host=configured.smtp_host,
                          smtp_from_email=configured.smtp_from_email, smtp_username="",
                          smtp_use_tls=False, smtp_use_ssl=False)
    assert service.process_next_email(NOW)
    first = repository.delivery_for_report(report_id)
    assert first["status"] == "retry_wait"
    assert first["recipients"] == ["b@example.com"]
    assert first["recipient_results"]["a@example.com"]["status"] == "accepted"
    assert not repository.retry_delivery(first["delivery_id"], now=STAMP)
    assert service.process_next_email(NOW + timedelta(hours=2))
    assert sent[0]["To"] == "a@example.com, b@example.com"
    assert sent[1]["To"] == "b@example.com"
    assert sent[0]["Message-ID"] == sent[1]["Message-ID"] == first["message_id"]
    final = repository.delivery_for_report(report_id)
    assert final["status"] == "sent"
    assert all(value["status"] == "accepted" for value in final["recipient_results"].values())


def test_success_then_database_failure_is_not_automatically_resent(workflow, monkeypatch):
    repository, service, report_id = ready(workflow)
    service.email_service = SimpleNamespace(send=lambda job, *args, **kwargs: job["message_id"])
    monkeypatch.setattr(repository, "mark_delivery_sent", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("disk failure")))
    with pytest.raises(RuntimeError, match="disk failure"):
        service.process_next_email(NOW)
    assert repository.delivery_for_report(report_id)["status"] == "sending"
    restarted = AutomationRepository(repository.database_path)
    restarted.initialize()
    assert restarted.delivery_for_report(report_id)["status"] == "sending", "Web schema initialization must not disturb live SMTP"
    restarted.recover_interrupted_deliveries()
    record = restarted.delivery_for_report(report_id)
    assert record["status"] == "unknown"
    assert record["message_id"]
    assert restarted.claim_delivery("2026-10-01T09:00:00+08:00") is None
    assert not restarted.retry_delivery(record["delivery_id"], now=STAMP)


def test_only_new_worker_can_recover_unknown_smtp_and_old_owner_cannot_mark_sent(workflow):
    from app.database.coordination import CoordinationRepository
    from app.database.sqlite import ExecutionLeaseLost, execution_scope
    repository, _, report_id = ready(workflow)
    coordination = CoordinationRepository(repository.database)
    coordination.initialize()
    owner = coordination.acquire("worker", ttl=30)
    with execution_scope(repository.database_path, "worker", owner):
        delivery = repository.claim_delivery(STAMP)
    # Web initialization neither resets sending nor allows recovery without a Worker lease.
    repository.initialize()
    assert repository.delivery_for_report(report_id)["status"] == "sending"
    with pytest.raises(ExecutionLeaseLost):
        repository.recover_interrupted_deliveries()
    with repository.connect() as connection:
        connection.execute("UPDATE process_leases SET expires_at=0")
    replacement = coordination.acquire("worker", ttl=30)
    with execution_scope(repository.database_path, "worker", replacement):
        repository.recover_interrupted_deliveries()
    assert repository.delivery_for_report(report_id)["status"] == "unknown"
    assert repository.claim_delivery(STAMP) is None
    assert not repository.retry_delivery(delivery["delivery_id"], now=STAMP)
    with execution_scope(repository.database_path, "worker", owner), pytest.raises(ExecutionLeaseLost):
        repository.mark_delivery_sent(delivery, message_id=delivery["message_id"], now=STAMP)
    assert repository.delivery_for_report(report_id)["status"] == "unknown"


@pytest.mark.parametrize("status", ["pending", "sending", "sent", "retry_wait", "unknown"])
def test_manual_retry_cannot_reset_active_sent_or_unknown_delivery(workflow, status):
    repository, _, report_id = ready(workflow)
    record = repository.delivery_for_report(report_id)
    with repository.connect() as connection:
        connection.execute("UPDATE email_deliveries SET status = ? WHERE report_id = ?", (status, report_id))
    assert not repository.retry_delivery(record["delivery_id"], now=STAMP)
    assert repository.delivery_for_report(report_id)["status"] == status


def test_concurrent_manual_retries_claim_failed_delivery_once(workflow):
    repository, _, report_id = ready(workflow)
    record = repository.claim_delivery(STAMP)
    record["attempt_count"] = record["max_attempts"]
    repository.mark_delivery_failed(record, error="all refused", next_attempt_at=STAMP, now=STAMP)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: repository.retry_delivery(record["delivery_id"], now=STAMP), range(2)))
    assert sorted(results) == [False, True]


def test_delivery_and_run_status_commit_atomically(workflow, monkeypatch):
    repository, _, report_id = ready(workflow)
    record = repository.claim_delivery(STAMP)
    monkeypatch.setattr(AutomationRepository, "mark_run", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("run write failed")))
    with pytest.raises(RuntimeError, match="run write failed"):
        repository.mark_delivery_sent(record, message_id=record["message_id"], now=STAMP)
    assert repository.delivery_for_report(report_id)["status"] == "sending"


def test_unknown_outcome_is_held_and_stale_callback_cannot_change_it(workflow):
    repository, service, report_id = ready(workflow)
    service.email_service = SimpleNamespace(send=lambda *args, **kwargs: (_ for _ in ()).throw(EmailDeliveryUnknown("lost DATA reply")))
    assert service.process_next_email(NOW)
    record = repository.delivery_for_report(report_id)
    assert record["status"] == "unknown"
    assert repository.summary()["delivery_unknown"] == 1
    assert all(item["status"] == "unknown" for item in record["recipient_results"].values())
    assert repository.claim_delivery("2026-10-01T09:00:00+08:00") is None
    with pytest.raises(LookupError):
        repository.mark_delivery_sent(record, message_id=record["message_id"], now=STAMP)
    assert repository.delivery_for_report(report_id)["status"] == "unknown"


def test_retry_api_reports_conflict_for_unknown_or_active_mail(workflow):
    from app.api.automation import retry_delivery
    from fastapi import HTTPException

    repository, _, report_id = ready(workflow)
    record = repository.delivery_for_report(report_id)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(automation=repository)),
                              state=SimpleNamespace(request_id="test"))
    with pytest.raises(HTTPException) as captured:
        retry_delivery(record["delivery_id"], request)
    assert captured.value.status_code == 409
    with pytest.raises(HTTPException) as captured:
        retry_delivery("missing", request)
    assert captured.value.status_code == 404
