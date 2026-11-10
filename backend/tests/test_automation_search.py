from datetime import timedelta
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.auth import require_admin
from app.api.automation import router
from tests.automation_fixtures import NOW, STAMP


def test_delivery_search_covers_all_pages_and_matches_snapshot_names_subjects_and_recipients(workflow):
    repository, metadata, service, initial = workflow
    schedule = repository.get_schedule(initial['schedule_id'])
    for index in range(25):
        moment = NOW + timedelta(minutes=index + 1)
        values = {
            **schedule,
            'name': '车间%任务' if index == 24 else '普通任务',
            'subject_template': '特殊主题' if index == 23 else schedule['subject_template'],
            'recipients': ['accepted@example.com', 'retry@example.com'] if index == 24 else ['other@example.com'],
        }
        run = repository.reserve_run(values, scheduled_for=moment.isoformat(), report_date='2026-09-29',
                                     trigger_type='manual', now=STAMP)
        assert service._create_report_job(run, moment)
        report_id = repository.get_run(run['run_id'])['report_id']
        metadata.save_report(report_id=report_id, filename=f'{report_id}.html', relative_path=f'{report_id}.html',
                             file_size=1, sha256='a' * 64, task_count=1, generated_at=STAMP, system_version='test')
        metadata.update_status(report_id, 'completed', 'completed', finished_at=STAMP)
        service.report_completed(report_id, moment)
        if index == 24:
            with repository.connect() as connection:
                connection.execute('UPDATE email_deliveries SET recipients_json=%s WHERE report_id=%s',
                                   (json.dumps(['retry@example.com']), report_id))
    items, total = repository.list_deliveries(page=2, page_size=20, search='普通任务')
    assert total == 24 and len(items) == 4
    for term in ['车间', '%', ' ACCEPTED@EXAMPLE.COM ', '特殊主题']:
        items, total = repository.list_deliveries(page=1, page_size=20, search=term)
        assert total == 1 and len(items) == 1, term
    for term in ['不存在', "' OR 1=1 --", '_']:
        assert repository.list_deliveries(page=1, page_size=20, search=term) == ([], 0)

    app = FastAPI()
    app.include_router(router)
    app.state.automation = repository
    app.dependency_overrides[require_admin] = lambda: {'role': 'admin'}

    @app.middleware('http')
    async def request_id(request, call_next):
        request.state.request_id = 'automation-search-test'
        return await call_next(request)

    with TestClient(app) as client:
        response = client.get('/api/v1/automation/deliveries', params={'search': '特殊主题'})
        assert response.status_code == 200
        assert response.json()['data']['total'] == 1
        assert client.get('/api/v1/automation/deliveries', params={'search': 'x' * 201}).status_code == 422
