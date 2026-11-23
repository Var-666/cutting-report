from tests.web_fixtures import embedded_app, request_for
import logging
import smtplib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.modules.automation import delivery_repository
from app.modules.automation.repository import AutomationRepository
from app.modules.automation.email import (
    EmailService, EmailDeliveryFailure, EmailDeliveryUnknown, EmailRecipientsRefused,
)
from app.modules.reports.generation.email import EmailReport
from tests.automation_fixtures import complete_report, NOW, STAMP


settings = Settings()


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

    monkeypatch.setattr("app.modules.automation.email.smtplib.SMTP", SMTP)
    monkeypatch.setattr("app.modules.reports.pdf.PdfService.render_email", lambda self, path, **kwargs: EmailReport("<h1>日报</h1>", {}))
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
    monkeypatch.setattr("app.modules.automation.email.monotonic", lambda: next(clock))
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

    monkeypatch.setattr("app.modules.automation.email.smtplib.SMTP", SMTP)
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


@pytest.mark.parametrize('enters_data', [False, True])
def test_disconnect_before_data_is_retryable_but_lost_data_reply_is_unknown(monkeypatch, enters_data):
    configured = replace(settings, smtp_enabled=True, smtp_host='smtp.invalid', smtp_from_email='report@example.com',
                         smtp_use_tls=False, smtp_use_ssl=False, smtp_username='')
    class SMTP:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def data(self, _body):
            raise smtplib.SMTPServerDisconnected('no DATA reply')

        def send_message(self, message):
            if enters_data:
                return self.data(message.as_bytes())
            raise smtplib.SMTPServerDisconnected('no RCPT reply')

    monkeypatch.setattr(smtplib, 'SMTP', SMTP)
    with pytest.raises(EmailDeliveryUnknown if enters_data else EmailDeliveryFailure):
        EmailService(configured).send(delivery(), None)


def ready(workflow, recipients=None):
    repository, metadata, service, run = workflow
    assert service._create_report_job(run, NOW)
    report_id = repository.get_run(run["run_id"])["report_id"]
    complete_report(metadata, report_id)
    service.report_completed(report_id, NOW)
    if recipients:
        import json
        with repository.connect() as connection:
            connection.execute("UPDATE email_deliveries SET recipients_json = %s WHERE report_id = %s",
                               (json.dumps(recipients), report_id))
    return repository, service, report_id


def test_partial_refusal_retries_only_rejected_recipients_with_same_message_id(workflow, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger='app.services.automation_service')
    repository, service, report_id = ready(workflow, ["a@example.com", "b@example.com"])
    email, configured, sent = smtp_service(monkeypatch, [{"b@example.com": (450, b"later")}, {}])
    service.delivery.email_service = email
    service.runtime.apply(smtp_enabled=configured.smtp_enabled, smtp_host=configured.smtp_host,
                          smtp_from_email=configured.smtp_from_email, smtp_username="",
                          smtp_use_tls=False, smtp_use_ssl=False)
    assert service.delivery.process_next_email(NOW)
    first = repository.deliveries.delivery_for_report(report_id)
    assert first["status"] == "retry_wait"
    assert first["recipients"] == ["b@example.com"]
    assert first["recipient_results"]["a@example.com"]["status"] == "accepted"
    assert not repository.deliveries.retry_delivery(first["delivery_id"], now=STAMP)
    assert service.delivery.process_next_email(NOW + timedelta(hours=2))
    assert sent[0]["To"] == "a@example.com, b@example.com"
    assert sent[1]["To"] == "b@example.com"
    assert sent[0]["Message-ID"] == sent[1]["Message-ID"] == first["message_id"]
    final = repository.deliveries.delivery_for_report(report_id)
    assert final["status"] == "sent"
    assert all(value["status"] == "accepted" for value in final["recipient_results"].values())
    logs = [record.operations_fields for record in caplog.records if hasattr(record, 'operations_fields')]
    assert [(item['error_code'], item['resource_id'], item['report_id'], item['line_id']) for item in logs] == [
        ('SMTP_ATTEMPT_FAILED', first['delivery_id'], report_id, 'Cutter01'),
        ('SMTP_ACCEPTED', first['delivery_id'], report_id, 'Cutter01'),
    ]


def test_mixed_refusal_automatically_retries_only_4xx_and_keeps_partial_failure(workflow, monkeypatch):
    repository, service, report_id = ready(workflow, ['a@example.com', 'b@example.com', 'c@example.com'])
    email, configured, sent = smtp_service(monkeypatch, [{
        'b@example.com': (450, b'temporary'), 'c@example.com': (550, b'permanent'),
    }, {}])
    service.delivery.email_service = email
    service.runtime.apply(smtp_enabled=True, smtp_host=configured.smtp_host, smtp_from_email=configured.smtp_from_email,
                          smtp_username='', smtp_use_tls=False, smtp_use_ssl=False)
    assert service.delivery.process_next_email(NOW)
    first = repository.deliveries.delivery_for_report(report_id)
    assert first['status'] == 'retry_wait' and first['recipients'] == ['b@example.com']
    assert service.delivery.process_next_email(NOW + timedelta(hours=2))
    final = repository.deliveries.delivery_for_report(report_id)
    assert final['status'] == 'failed' and final['recipients'] == ['c@example.com']
    assert final['recipient_results'] == {
        'a@example.com': {'status': 'accepted'}, 'b@example.com': {'status': 'accepted'},
        'c@example.com': {'status': 'refused', 'code': 550},
    }
    assert sent[1]['To'] == 'b@example.com'
    assert sent[0]['Message-ID'] == sent[1]['Message-ID']
    assert not service.delivery.process_next_email(NOW + timedelta(hours=4))


def test_corrupted_html_is_stopped_before_submitting_any_email(workflow):
    repository, service, report_id = ready(workflow)
    path = service.settings.reports_dir / 'r.html'
    data = path.read_bytes()
    path.write_bytes(data.replace(b'h1', b'h2'))
    def reject(*_args, **_kwargs):
        pytest.fail('SMTP must never receive an unverified archive')
    service.delivery.email_service = SimpleNamespace(send=reject)
    assert service.delivery.process_next_email(NOW)
    record = repository.deliveries.delivery_for_report(report_id)
    assert record['status'] == 'retry_wait' and '尚未提交 SMTP' in record['last_error']


def test_success_then_database_failure_is_not_automatically_resent(workflow, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger='app.services.automation_service')
    repository, service, report_id = ready(workflow)
    service.delivery.email_service = SimpleNamespace(send=lambda job, *args, **kwargs: job["message_id"])
    monkeypatch.setattr(repository.deliveries, "mark_delivery_sent", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("disk failure")))
    with pytest.raises(RuntimeError, match="disk failure"):
        service.delivery.process_next_email(NOW)
    assert not any(getattr(record, 'operations_fields', {}).get('error_code') == 'SMTP_ACCEPTED'
                   for record in caplog.records)
    assert repository.deliveries.delivery_for_report(report_id)["status"] == "sending"
    restarted = AutomationRepository(repository.database)
    assert restarted.deliveries.delivery_for_report(report_id)["status"] == "sending", "Web schema initialization must not disturb live SMTP"
    from app.database.coordination import CoordinationRepository
    from app.database.postgres import execution_scope
    owner = CoordinationRepository(repository.database).acquire('automation',ttl=30)
    with execution_scope(repository.database,'automation',owner):
        restarted.deliveries.recover_interrupted_deliveries()
    record = restarted.deliveries.delivery_for_report(report_id)
    assert record["status"] == "unknown"
    assert record["message_id"]
    assert restarted.deliveries.claim_delivery("2026-10-01T09:00:00+08:00") is None
    assert not restarted.deliveries.retry_delivery(record["delivery_id"], now=STAMP)


def test_only_new_automation_can_recover_unknown_smtp_and_old_owner_cannot_mark_sent(workflow):
    from app.database.coordination import CoordinationRepository
    from app.database.postgres import ExecutionLeaseLost, execution_scope
    repository, _, report_id = ready(workflow)
    coordination = CoordinationRepository(repository.database)
    owner = coordination.acquire("automation", ttl=30)
    with execution_scope(repository.database, "automation", owner):
        delivery = repository.deliveries.claim_delivery(STAMP)
    # Web initialization neither resets sending nor allows recovery without a Worker lease.
    assert repository.deliveries.delivery_for_report(report_id)["status"] == "sending"
    with pytest.raises(ExecutionLeaseLost):
        repository.deliveries.recover_interrupted_deliveries()
    with repository.connect() as connection:
        connection.execute("UPDATE process_leases SET expires_at=0")
    replacement = coordination.acquire("automation", ttl=30)
    with execution_scope(repository.database, "automation", replacement):
        repository.deliveries.recover_interrupted_deliveries()
    assert repository.deliveries.delivery_for_report(report_id)["status"] == "unknown"
    assert repository.deliveries.claim_delivery(STAMP) is None
    assert not repository.deliveries.retry_delivery(delivery["delivery_id"], now=STAMP)
    with execution_scope(repository.database, "automation", owner), pytest.raises(ExecutionLeaseLost):
        repository.deliveries.mark_delivery_sent(delivery, message_id=delivery["message_id"], now=STAMP)
    assert repository.deliveries.delivery_for_report(report_id)["status"] == "unknown"


@pytest.mark.parametrize("status", ["pending", "sending", "sent", "retry_wait", "unknown"])
def test_manual_retry_cannot_reset_active_sent_or_unknown_delivery(workflow, status):
    repository, _, report_id = ready(workflow)
    record = repository.deliveries.delivery_for_report(report_id)
    with repository.connect() as connection:
        connection.execute("UPDATE email_deliveries SET status = %s WHERE report_id = %s", (status, report_id))
    assert not repository.deliveries.retry_delivery(record["delivery_id"], now=STAMP)
    assert repository.deliveries.delivery_for_report(report_id)["status"] == status


def test_concurrent_manual_retries_claim_failed_delivery_once(workflow):
    repository, _, report_id = ready(workflow)
    record = repository.deliveries.claim_delivery(STAMP)
    record["attempt_count"] = record["max_attempts"]
    repository.deliveries.mark_delivery_failed(record, error="all refused", next_attempt_at=STAMP, now=STAMP)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: repository.deliveries.retry_delivery(record["delivery_id"], now=STAMP), range(2)))
    assert sorted(results) == [False, True]


def test_delivery_and_run_status_commit_atomically(workflow, monkeypatch):
    repository, _, report_id = ready(workflow)
    record = repository.deliveries.claim_delivery(STAMP)
    monkeypatch.setattr(delivery_repository, "update_run", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("run write failed")))
    with pytest.raises(RuntimeError, match="run write failed"):
        repository.deliveries.mark_delivery_sent(record, message_id=record["message_id"], now=STAMP)
    assert repository.deliveries.delivery_for_report(report_id)["status"] == "sending"


def test_unknown_outcome_is_held_and_stale_callback_cannot_change_it(workflow):
    repository, service, report_id = ready(workflow)
    service.delivery.email_service = SimpleNamespace(send=lambda *args, **kwargs: (_ for _ in ()).throw(EmailDeliveryUnknown("lost DATA reply")))
    assert service.delivery.process_next_email(NOW)
    record = repository.deliveries.delivery_for_report(report_id)
    assert record["status"] == "unknown"
    assert repository.summary()["delivery_unknown"] == 1
    assert all(item["status"] == "unknown" for item in record["recipient_results"].values())
    assert repository.deliveries.claim_delivery("2026-10-01T09:00:00+08:00") is None
    with pytest.raises(LookupError):
        repository.deliveries.mark_delivery_sent(record, message_id=record["message_id"], now=STAMP)
    assert repository.deliveries.delivery_for_report(report_id)["status"] == "unknown"


def test_retry_api_reports_conflict_for_unknown_or_active_mail(workflow):
    from app.modules.automation.routes import retry_delivery
    from fastapi import HTTPException

    repository, _, report_id = ready(workflow)
    record = repository.deliveries.delivery_for_report(report_id)
    app = embedded_app(settings)
    app.state.runtime.automation = repository
    request = request_for(app)
    with pytest.raises(HTTPException) as captured:
        retry_delivery(record["delivery_id"], request)
    assert captured.value.status_code == 409
    with pytest.raises(HTTPException) as captured:
        retry_delivery("missing", request)
    assert captured.value.status_code == 404
