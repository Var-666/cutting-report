from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.auth import require_admin
from app.api.automation import router
from test_automation_recovery import workflow, complete_report, NOW, STAMP


ACTOR = {"user_id": "admin-reviewer", "display_name": "管理员", "role": "admin"}


def unknown_delivery(workflow):
    repository, metadata, service, run = workflow
    service._create_report_job(run, NOW)
    report_id = repository.get_run(run["run_id"])["report_id"]
    complete_report(metadata, report_id)
    service.report_completed(report_id, NOW)
    with repository.connect() as connection:
        connection.execute("UPDATE email_deliveries SET recipients_json=? WHERE report_id=?",
                           ('["a@example.com","b@example.com"]', report_id))
    delivery = repository.claim_delivery(STAMP)
    repository.mark_delivery_unknown(delivery, now=STAMP)
    return delivery


def resolve(repository, delivery, outcomes=None, **changes):
    payload = dict(message_id=delivery["message_id"], evidence_note="已按 Message-ID 核对 SMTP 日志",
                   outcomes=outcomes or [{"recipient": recipient, "outcome": "accepted"}
                                        for recipient in delivery["recipients"]],
                   user=ACTOR, now=STAMP)
    return repository.resolve_unknown_delivery(delivery["delivery_id"], **{**payload, **changes})


def test_partial_resolution_requires_manual_retry_and_excludes_accepted_recipient(workflow):
    repository, _, _, _ = workflow
    delivery = unknown_delivery(workflow)
    assert resolve(repository, delivery, [
        {"recipient": "a@example.com", "outcome": "accepted"},
        {"recipient": "b@example.com", "outcome": "not_accepted"},
    ])
    assert repository.delivery_state(delivery["delivery_id"]) == "failed"
    assert repository.claim_delivery(STAMP) is None
    audit = repository.delivery_resolutions(delivery["delivery_id"])
    assert audit[0]["actor_id"] == ACTOR["user_id"]
    assert audit[0]["before_results"]["a@example.com"]["status"] == "unknown"
    assert audit[0]["after_results"]["a@example.com"]["status"] == "accepted"
    assert repository.retry_delivery(delivery["delivery_id"], now=STAMP)
    retry = repository.claim_delivery(STAMP)
    assert retry["recipients"] == ["b@example.com"]
    assert retry["message_id"] == delivery["message_id"]


def test_all_accepted_resolution_closes_run_and_never_requeues(workflow):
    repository, _, _, _ = workflow
    delivery = unknown_delivery(workflow)
    assert resolve(repository, delivery)
    assert repository.get_run(delivery["run_id"])["status"] == "sent"
    assert not repository.retry_delivery(delivery["delivery_id"], now=STAMP)
    assert repository.claim_delivery(STAMP) is None
    assert not resolve(repository, delivery)
    assert len(repository.delivery_resolutions(delivery["delivery_id"])) == 1


@pytest.mark.parametrize("changes", [
    {"message_id": "<wrong@server>"},
    {"evidence_note": "  "},
    {"outcomes": [{"recipient": "a@example.com", "outcome": "accepted"}]},
    {"outcomes": [{"recipient": "x@example.com", "outcome": "accepted"}]},
    {"outcomes": [{"recipient": "a@example.com", "outcome": "accepted"}] * 2},
])
def test_invalid_or_incomplete_server_verification_cannot_change_unknown_delivery(workflow, changes):
    repository, _, _, _ = workflow
    delivery = unknown_delivery(workflow)
    with pytest.raises(ValueError):
        resolve(repository, delivery, **changes)
    assert repository.delivery_state(delivery["delivery_id"]) == "unknown"
    assert repository.delivery_resolutions(delivery["delivery_id"]) == []


def test_concurrent_verifiers_only_one_can_commit(workflow):
    repository, _, _, _ = workflow
    delivery = unknown_delivery(workflow)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: resolve(repository, delivery), range(2)))
    assert sorted(results) == [False, True]
    assert len(repository.delivery_resolutions(delivery["delivery_id"])) == 1


def test_audit_and_run_update_failure_roll_back_delivery_resolution(workflow, monkeypatch):
    from app.database.automation import AutomationRepository

    repository, _, _, _ = workflow
    delivery = unknown_delivery(workflow)

    def fail(*args, **kwargs):
        raise RuntimeError("run update unavailable")

    monkeypatch.setattr(AutomationRepository, "mark_run", fail)
    with pytest.raises(RuntimeError):
        resolve(repository, delivery)
    assert repository.delivery_state(delivery["delivery_id"]) == "unknown"
    assert repository.get_run(delivery["run_id"])["status"] == "unknown"
    assert repository.delivery_resolutions(delivery["delivery_id"]) == []


def test_api_requires_admin_and_rejects_stale_verification(workflow):
    repository, _, _, _ = workflow
    delivery = unknown_delivery(workflow)
    app = FastAPI()
    app.include_router(router)
    app.state.automation = repository

    @app.middleware("http")
    async def request_id(request, call_next):
        request.state.request_id = "test"
        return await call_next(request)

    def unauthorized():
        raise HTTPException(status_code=403, detail="管理员权限")

    app.dependency_overrides[require_admin] = unauthorized
    client = TestClient(app)
    path = f'/api/v1/automation/deliveries/{delivery["delivery_id"]}/resolve'
    payload = {"message_id": delivery["message_id"], "evidence_note": "核对服务器日志",
               "outcomes": [{"recipient": recipient, "outcome": "accepted"}
                            for recipient in delivery["recipients"]]}
    assert client.post(path, json=payload).status_code == 403
    app.dependency_overrides[require_admin] = lambda: ACTOR
    assert client.post(path, json={**payload, "evidence_note": "  "}).status_code == 422
    assert client.post(path, json=payload).status_code == 200
    assert client.post(path, json=payload).status_code == 409
    assert client.get(path.replace('/resolve', '/resolutions')).json()["data"]["items"][0]["actor_id"] == ACTOR["user_id"]


def test_prior_accepted_recipient_is_immutable_during_later_unknown_resolution(workflow):
    repository, _, _, _ = workflow
    delivery = unknown_delivery(workflow)
    with repository.connect() as connection:
        connection.execute("""
            UPDATE email_deliveries SET recipients_json='["b@example.com"]',
                recipient_results_json='{"a@example.com":{"status":"accepted"},"b@example.com":{"status":"unknown"}}'
            WHERE delivery_id=?
        """, (delivery["delivery_id"],))
    with pytest.raises(ValueError):
        resolve(repository, delivery, [
            {"recipient": "a@example.com", "outcome": "not_accepted"},
            {"recipient": "b@example.com", "outcome": "accepted"},
        ])
    assert resolve(repository, delivery, [{"recipient": "b@example.com", "outcome": "accepted"}])
    result = repository.delivery_resolutions(delivery["delivery_id"])[0]
    assert result["before_results"]["a@example.com"]["status"] == "accepted"
    assert result["after_results"]["a@example.com"]["status"] == "accepted"
