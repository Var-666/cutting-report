from datetime import timedelta

from fastapi import HTTPException
from tests.web_fixtures import embedded_app
from fastapi.testclient import TestClient
import pytest

from app.api.auth import require_admin
from app.api.automation import router
from app.database.automation import AutomationRepository
from tests.automation_fixtures import complete_report, NOW, STAMP


def test_pause_and_resume_schedule_preserves_runs_and_skips_missed_occurrences(workflow, monkeypatch):
    repository, _, service, run = workflow
    schedule_id = run['schedule_id']
    original = repository.get_schedule(schedule_id)
    now = NOW + timedelta(days=3)
    monkeypatch.setattr('app.api.automation._now', lambda _request: now)
    app = embedded_app()
    app.include_router(router)
    app.state.automation_service = service

    @app.middleware('http')
    async def request_context(request, call_next):
        request.state.request_id = 'automation-pause-test'
        return await call_next(request)

    def forbidden():
        raise HTTPException(status_code=403)

    path = f'/api/v1/automation/schedules/{schedule_id}'
    app.dependency_overrides[require_admin] = forbidden
    with TestClient(app) as client:
        assert client.patch(path, json={'enabled': False}).status_code == 403
        app.dependency_overrides[require_admin] = lambda: {'role': 'admin'}
        assert client.patch(path, json={'enabled': 'false'}).status_code == 422
        assert client.patch('/api/v1/automation/schedules/missing', json={'enabled': False}).status_code == 404
        paused = client.patch(path, json={'enabled': False}).json()['data']
        assert paused == {**original, 'enabled': False, 'updated_at': now.isoformat()}
        assert service.process_due_schedules(now) == 0
        assert repository.reserve_run(original, scheduled_for=original['next_run_at'], report_date='2026-09-30',
                                      trigger_type='scheduled', now=now.isoformat(), next_run_at=now.isoformat()) is None
        assert repository.get_run(run['run_id']) == run
        manual = service.run_now(schedule_id, now)
        assert manual['trigger_type'] == 'manual'
        resumed = client.patch(path, json={'enabled': True}).json()['data']
        assert resumed['enabled'] is True
        assert resumed['next_run_at'] == (now + timedelta(days=1)).isoformat()
        assert repository.due_schedules(now.isoformat()) == []
        assert repository.due_schedules(resumed['next_run_at'])[0]['schedule_id'] == schedule_id
        now += timedelta(hours=1)
        assert client.patch(path, json={'enabled': True}).json()['data'] == resumed
        assert repository.get_run(manual['run_id']) == manual


def test_run_progress_tracks_its_report_and_delivery_without_changing_persisted_states(workflow):
    repository, metadata, service, run = workflow
    run_id = run['run_id']
    assert service.run_progress('missing') is None
    assert service.run_progress(run_id)['status'] == 'queued'
    assert service._create_report_job(run, NOW)
    report_id = repository.get_run(run_id)['report_id']
    assert service.run_progress(run_id)['status'] == 'generating'
    complete_report(metadata, report_id)
    service.report_completed(report_id, NOW)
    assert service.run_progress(run_id)['status'] == 'email_pending'
    delivery = repository.claim_delivery(STAMP)
    assert service.run_progress(run_id)['status'] == 'sending'
    assert repository.get_run(run_id)['status'] == 'email_pending'
    repository.mark_delivery_failed(delivery, error='retry later', next_attempt_at=STAMP, now=STAMP)
    progress = service.run_progress(run_id)
    assert progress['status'] == 'retry_wait'
    assert progress['last_error'] == 'retry later'
    delivery = repository.claim_delivery(STAMP)
    assert service.run_progress(run_id)['status'] == 'sending'
    repository.mark_delivery_unknown(delivery, now=STAMP)
    assert service.run_progress(run_id)['status'] == 'unknown'
    assert repository.claim_delivery(STAMP) is None


def test_run_progress_http_requires_admin_and_returns_only_requested_run(workflow):
    repository, _, service, run = workflow
    app = embedded_app()
    app.include_router(router)
    app.state.automation_service = service

    @app.middleware('http')
    async def request_context(request, call_next):
        request.state.request_id = 'automation-progress-test'
        return await call_next(request)

    def forbidden():
        raise HTTPException(status_code=403, detail='仅管理员可访问')

    app.dependency_overrides[require_admin] = forbidden
    with TestClient(app) as client:
        assert client.get(f"/api/v1/automation/runs/{run['run_id']}").status_code == 403
        assert client.post(f"/api/v1/automation/runs/{run['run_id']}/cancel").status_code == 403
        app.dependency_overrides[require_admin] = lambda: {'role': 'admin'}
        assert client.get('/api/v1/automation/runs/missing').status_code == 404
        assert client.post('/api/v1/automation/runs/missing/cancel').status_code == 404
        assert client.post(f"/api/v1/automation/runs/{run['run_id']}/cancel").status_code == 409
        manual = service.run_now(run['schedule_id'], NOW)
        cancelled = client.post(f"/api/v1/automation/runs/{manual['run_id']}/cancel")
        assert cancelled.status_code == 200 and cancelled.json()['data']['status'] == 'cancelled'
        response = client.get(f"/api/v1/automation/runs/{run['run_id']}")
        assert response.status_code == 200
        assert response.json()['data'] == run
        repository.mark_run(run['run_id'], 'failed', error='report failed', now=STAMP)
        response = client.get(f"/api/v1/automation/runs/{run['run_id']}")
        assert response.json()['data']['status'] == 'failed'
        assert response.json()['data']['last_error'] == 'report failed'


def test_run_progress_reports_analysis_wait_without_sending_or_failing(workflow):
    repository, metadata, service, run = workflow
    service._create_report_job(run, NOW)
    report_id = repository.get_run(run['run_id'])['report_id']
    metadata.update_status(report_id, 'pending', '等待智能分析完成', ai_status='waiting')
    assert service.run_progress(run['run_id'])['status'] == 'analysis_wait'
    assert repository.get_run(run['run_id'])['status'] == 'generating'
    assert repository.delivery_for_report(report_id) is None


@pytest.mark.parametrize('phase', ['queued', 'generating', 'email_pending', 'retry_wait'])
def test_cancel_manual_run_survives_late_report_callbacks(workflow, phase):
    repository, metadata, service, automatic = workflow
    run = service.run_now(automatic['schedule_id'], NOW)
    run_id = run['run_id']
    report_id = None
    if phase != 'queued':
        assert service._create_report_job(run, NOW)
        report_id = repository.get_run(run_id)['report_id']
    if phase in ('email_pending', 'retry_wait'):
        complete_report(metadata, report_id)
        service.report_completed(report_id, NOW)
    if phase == 'retry_wait':
        delivery = repository.claim_delivery(STAMP)
        repository.mark_delivery_failed(delivery, error='retry', next_attempt_at=STAMP, now=STAMP)
    [schedule] = repository.list_schedules()
    assert schedule['active_manual_run_id'] == run_id
    assert schedule['active_manual_run_status'] == phase
    assert service.cancel_run(run_id, NOW)['status'] == 'cancelled'
    assert service.cancel_run(run_id, NOW)['status'] == 'cancelled'
    if report_id:
        if phase == 'generating':
            complete_report(metadata, report_id)
        service.report_completed(report_id, NOW)
        service.report_failed(report_id, 'late callback', NOW)
        delivery = repository.delivery_for_report(report_id)
        if phase == 'generating':
            assert delivery is None
        else:
            assert delivery['status'] == 'cancelled'
            assert not repository.retry_delivery(delivery['delivery_id'], now=STAMP)
    else:
        assert not service._create_report_job(run, NOW)
    assert all(item['run_id'] != run_id for item in repository.incomplete_runs())
    assert repository.claim_delivery(STAMP) is None
    assert service.run_progress(run_id)['status'] == 'cancelled'
    assert repository.get_run(automatic['run_id'])['status'] == 'queued'
    assert repository.list_schedules()[0]['active_manual_run_id'] is None


def test_cancel_refuses_concurrent_smtp_claim_and_keeps_unknown_outcome(workflow):
    repository, metadata, service, automatic = workflow
    run = service.run_now(automatic['schedule_id'], NOW)
    service._create_report_job(run, NOW)
    report_id = repository.get_run(run['run_id'])['report_id']
    complete_report(metadata, report_id)
    service.report_completed(report_id, NOW)
    with repository.connect() as connection:
        claimed = AutomationRepository(repository.database, connection=connection).claim_delivery(STAMP)
        with pytest.raises(ValueError, match='状态正在更新'):
            service.cancel_run(run['run_id'], NOW)
    with pytest.raises(ValueError, match='进入发送'):
        service.cancel_run(run['run_id'], NOW)
    assert service.run_progress(run['run_id'])['status'] == 'sending'
    repository.mark_delivery_unknown(claimed, now=STAMP)
    with pytest.raises(ValueError, match='无法取消'):
        service.cancel_run(run['run_id'], NOW)
    assert service.run_progress(run['run_id'])['status'] == 'unknown'
