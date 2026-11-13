from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.auth import require_admin
from app.api.automation import router
from tests.automation_fixtures import complete_report, NOW, STAMP


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
    app = FastAPI()
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
        app.dependency_overrides[require_admin] = lambda: {'role': 'admin'}
        assert client.get('/api/v1/automation/runs/missing').status_code == 404
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
