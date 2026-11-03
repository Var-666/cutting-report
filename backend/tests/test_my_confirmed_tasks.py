from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.auth import require_user
from app.api.workshop import router
from app.config import Settings
from app.database.confirmations import ConfirmationRepository
from app.database.workshop import WorkshopRepository
from app.database.workshop_reports import WorkshopReportRepository
from app.models.domain import WorkshopRecord
from app.services.workshop_service import WorkshopService


@pytest.fixture
def personal(tmp_path):
    repository = WorkshopRepository(tmp_path / 'personal.db', scope_machine_no='Cutter01')
    repository.initialize()
    now = '2026-11-03T12:00:00+08:00'
    def user(username, role='worker'):
        return repository.create_user(username=username, display_name='同名工人', password='offline-password', role=role, now=now)
    admin, worker, other = user('admin', 'admin'), user('worker'), user('other')
    def report(day, task_ids, scope='Cutter01'):
        reports = WorkshopReportRepository(repository.database, scope_machine_no=scope)
        rows = [WorkshopRecord(task_id=task_id, project_code='CMYZ0170', ship_no='18600DWT-02',
            segment_name='806C', program_no=f'{task_id}.gen', cut_length=Decimal('12.3'),
            groove_length=Decimal('0.1'), work_time=Decimal('60'), production_date=date.fromisoformat(day),
            production_time=datetime(2026, 11, 1, 23, 30)) for task_id in task_ids]
        return reports.create_daily_report(report_date=day, source_mode='demo', records=rows, user=admin, now=now)
    def confirm(task_id, report, owner=worker, scope='Cutter01'):
        return ConfirmationRepository(repository.database, scope_machine_no=scope).confirm_task(
            task_id=task_id, report_id=report['report_id'] if report else None, user=owner, now=now)
    first = report('2026-11-01', ['A', 'B', 'other', 'legacy'])
    second = report('2026-11-02', ['C'])
    confirm('A', first); confirm('B', first); confirm('other', first, other); confirm('C', second)
    confirm('legacy', None)
    duplicate = report('2026-10-31', ['legacy'])
    with repository.connect() as connection:
        connection.execute("UPDATE workshop_reports SET generated_at='2026-10-31T08:00:00+08:00' WHERE report_id=?", (duplicate['report_id'],))
        connection.execute("UPDATE workshop_report_items SET production_date=NULL WHERE task_id='B'")
    another_device = report('2026-11-01', ['A'], scope='Cutter02')
    confirm('A', another_device, scope='Cutter02')
    confirmations = ConfirmationRepository(repository.database, scope_machine_no='Cutter01')
    service = WorkshopService(WorkshopReportRepository(repository.database, scope_machine_no='Cutter01'),
                              confirmations, Settings())
    return SimpleNamespace(repository=repository, service=service, admin=admin, worker=worker, other=other,
                           confirm=confirm, reports=[first, second])


def test_personal_records_follow_production_day_and_deduplicate_snapshots(personal):
    p = personal
    def query(page=1, page_size=2, **dates):
        return p.service.my_confirmed_tasks(start_date=date(2026, 11, 1), end_date=date(2026, 11, 2),
                                           page=page, page_size=page_size, user=p.worker, **dates)
    first, second = query(), query(2)
    assert first['total'] == second['total'] == 4
    assert first['items'][0]['task_id'] == 'C'
    assert {row['task_id'] for page in (first, second) for row in page['items']} == {'A', 'B', 'C', 'legacy'}
    assert all(row['production_date'] in {'2026-11-01', '2026-11-02'} for page in (first, second) for row in page['items'])
    assert first['items'][0]['ship_no'] == '18600DWT-02'
    assert query(3)['items'] == []
    empty = p.service.my_confirmed_tasks(date(2026, 11, 3), date(2026, 11, 3), 1, 10, p.worker)
    assert empty['total'] == 0  # Confirmation date is November 3; production date is earlier.
    assert p.service.my_confirmed_tasks(date(2026, 10, 31), date(2026, 10, 31), 1, 10, p.worker)['total'] == 0
    p.confirm('A', p.reports[0])  # Idempotent confirmation does not increase count.
    assert query()['total'] == 4


def test_revoke_and_reassignment_remove_personal_tasks_without_changing_snapshots(personal):
    p = personal
    args = (date(2026, 11, 1), date(2026, 11, 2), 1, 10)
    p.repository.revoke_confirmation(task_id='A', report_id=None, user=p.worker, reason='误选任务', now='2026-11-03T13:00:00+08:00')
    assert p.service.my_confirmed_tasks(*args, p.worker)['total'] == 3
    p.repository.correct_confirmation(task_id='B', report_id=None, admin=p.admin, reason='核对后改派',
        new_worker_id=p.other['user_id'], now='2026-11-03T13:01:00+08:00')
    assert p.service.my_confirmed_tasks(*args, p.worker)['total'] == 2
    assert {row['task_id'] for row in p.service.my_confirmed_tasks(*args, p.other)['items']} == {'B', 'other'}
    with p.repository.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM workshop_report_items WHERE task_id='A'").fetchone()[0] == 2


def test_personal_http_auth_device_scope_validation_and_no_external_dependency(personal):
    p = personal
    app = FastAPI()
    app.include_router(router)
    app.state.workshop = p.repository
    app.state.devices = {}
    for device_id in ('Cutter01', 'Cutter02'):
        app.state.devices[device_id] = SimpleNamespace(settings=replace(Settings(), report_machine_no=device_id),
            workshop=WorkshopReportRepository(p.repository.database, scope_machine_no=device_id),
            confirmations=ConfirmationRepository(p.repository.database, scope_machine_no=device_id),
            query_service=SimpleNamespace(enrich_ship_names=lambda *_: pytest.fail('personal history must not query BM or production')))
    @app.middleware('http')
    async def context(request, call_next):
        request.state.request_id = 'personal-tasks'
        return await call_next(request)
    query = dict(start_date='2026-11-01', end_date='2026-11-02', device_id='Cutter01')
    with TestClient(app) as client:
        assert client.get('/api/v1/workshop/my-confirmed-tasks', params=query).status_code == 401
        app.dependency_overrides[require_user] = lambda: p.worker
        response = client.get('/api/v1/workshop/my-confirmed-tasks', params={**query, 'worker_id': p.other['user_id']})
        assert response.status_code == 200
        assert response.json()['data']['total'] == 4
        assert {row['task_id'] for row in response.json()['data']['items']} == {'A', 'B', 'C', 'legacy'}
        assert client.get('/api/v1/workshop/my-confirmed-tasks', params={**query, 'device_id': 'Cutter02'}).json()['data']['total'] == 1
        for updates in [dict(end_date='2026-10-31'), dict(end_date='2028-11-01'), dict(start_date='invalid'),
                        dict(device_id='missing'), dict(page=0), dict(page_size=51)]:
            assert client.get('/api/v1/workshop/my-confirmed-tasks', params={**query, **updates}).status_code == 422
        missing_device = {key: value for key, value in query.items() if key != 'device_id'}
        assert client.get('/api/v1/workshop/my-confirmed-tasks', params=missing_device).status_code == 422
        app.dependency_overrides[require_user] = lambda: p.other
        assert client.get('/api/v1/workshop/my-confirmed-tasks', params=query).json()['data']['total'] == 1
        app.dependency_overrides[require_user] = lambda: p.admin
        assert client.get('/api/v1/workshop/my-confirmed-tasks', params=query).status_code == 403
