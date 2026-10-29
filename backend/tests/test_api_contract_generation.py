"""Contracts must cover actual service output, not just a second handwritten TS model."""
from pathlib import Path
import importlib.util

from pydantic import ValidationError
import pytest

from app.models.cutting_contracts import CuttingFile, CuttingDispatch
from tests.test_cutting_dispatch import _service


def test_dispatch_contracts_match_service_and_preserve_all_returned_fields(tmp_path):
    service, repository, admin = _service(tmp_path)
    # Use the fixture's real project identity instead of assuming a SQL Server key.
    project = service.list_projects('', 100)[0]
    files = service.list_files(project['project_id'], catalog_id=None, status='all', keyword='', page=1, page_size=20)
    assert files['items']
    for item in files['items']:
        model = CuttingFile.model_validate(item)
        assert not item.keys() - type(model).model_fields.keys()
    raw = service.pdm.files[0]
    item = service.create_dispatch(document_id=raw['document_id'], machine_ip='10.1.133.197', material='A0', thickness=12, quantity=1, user=admin)
    model = CuttingDispatch.model_validate(item)
    assert not item.keys() - type(model).model_fields.keys()
    assert model.model_dump(exclude_unset=True) == item


def test_alarm_contract_export_has_typed_json_responses_and_no_database_side_effects(tmp_path, monkeypatch):
    from app.database.sqlite import SQLiteDatabase
    def forbidden(*args, **kwargs):
        raise AssertionError("Schema export must not access databases")
    monkeypatch.setattr(SQLiteDatabase, "connect", forbidden)
    path = Path(__file__).resolve().parents[2] / 'scripts/export_api_contracts.py'
    spec = importlib.util.spec_from_file_location('export_api_contracts', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    schema = module.api_schema()
    for url, methods in schema['paths'].items():
        for method, operation in methods.items():
            if not method in {'get', 'post'} or '/pdf' in url or url.endswith('/view'):
                continue
            success = next(value for code, value in operation['responses'].items() if code.startswith('2'))
            assert '$ref' in success['content']['application/json']['schema'], url
    assert 'AlarmIncidentPage' in schema['components']['schemas']
    assert 'WorkshopResult' in schema['components']['schemas']
    assert 'CuttingDispatch' in schema['components']['schemas']


def test_required_dispatch_fields_cannot_silently_disappear():
    with pytest.raises(ValidationError):
        CuttingDispatch.model_validate({'dispatch_id': 'missing-required-fields'})


def test_http_response_validation_matches_workshop_and_dispatch_records(tmp_path):
    from datetime import datetime
    from decimal import Decimal
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api import cutting, workshop
    from app.api.auth import require_admin, require_user, require_worker
    from app.models.domain import WorkshopRecord

    service, dispatches, admin = _service(tmp_path)
    from app.database.workshop import WorkshopRepository
    repository = WorkshopRepository(dispatches.database, scope_machine_no='Cutter01')
    worker = repository.create_user(username='worker', display_name='工人', password='worker-password', role='worker', now='2026-10-01T08:00:00')
    records = [WorkshopRecord(task_id='task1', project_code='SHIP', ship_no='SHIP', segment_name='P1', program_no='p.gen', cut_length=Decimal('1.2'), groove_length=Decimal('0.1'), work_time=Decimal('120'), production_time=datetime(2026, 10, 1, 8))]
    repository.sync_daily_report(report_date='2026-10-01', source_mode='demo', records=records, user=admin, now='2026-10-01T09:00:00', scope_machine_no='Cutter01')
    app = FastAPI()
    app.include_router(workshop.router)
    app.include_router(cutting.router)
    app.state.devices = {'Cutter01': SimpleNamespace(settings=service.settings, workshop=repository, query_service=None)}
    app.dependency_overrides[require_user] = lambda: admin
    app.dependency_overrides[require_admin] = lambda: admin
    app.dependency_overrides[require_worker] = lambda: worker

    @app.middleware('http')
    async def attach_context(request, call_next):
        request.state.request_id = 'contract-test'
        request.state.database_context = SimpleNamespace(cutting_dispatch=service, devices=app.state.devices)
        return await call_next(request)

    with TestClient(app) as client:
        for url in ['/api/v1/workshop/tasks?device_id=Cutter01&report_date=2026-10-01', '/api/v1/cutting/status', '/api/v1/cutting/projects', '/api/v1/cutting/dispatches']:
            response = client.get(url)
            assert response.status_code == 200, response.text
        response = client.post('/api/v1/workshop/tasks/task1/confirm?device_id=Cutter01', json={'report_date': '2026-10-01', 'snapshot_revision': 1})
        assert response.status_code == 200, response.text
        assert response.json()['data']['worker_id'] == worker['user_id']
        response = client.post('/api/v1/cutting/dispatches', json={'document_id': service.pdm.files[0]['document_id'], 'machine_ip': '10.1.133.197', 'material': 'A0', 'thickness': 12, 'quantity': 1, 'name_check_confirmed': True})
        assert response.status_code == 201, response.text
        result = response.json()['data']
        response = client.get(f"/api/v1/cutting/dispatches/{result['dispatch_id']}")
        assert response.status_code == 200, response.text
        assert response.json()['data']['ship_no'] == 'CMYZ0177'


def test_alarm_http_contracts_cover_empty_and_populated_reports(tmp_path):
    from datetime import datetime
    from types import SimpleNamespace
    from zoneinfo import ZoneInfo
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api import alarms
    from app.api.auth import require_admin
    from app.config import Settings
    from app.database.alarms import AlarmRepository
    from tests.test_alarm_monitor import alarm

    repository = AlarmRepository(tmp_path / 'alarms.db', source_name='production')
    repository.initialize()
    cfg = Settings(project_root=tmp_path, device_id='Cutter01')
    runtime = SimpleNamespace(settings=cfg, alarm_repository=repository, alarm_worker=SimpleNamespace(enabled=False, thread=None))
    app = FastAPI()
    app.include_router(alarms.router)
    app.state.devices = {'Cutter01': runtime}
    app.dependency_overrides[require_admin] = lambda: {'role': 'admin'}

    @app.middleware('http')
    async def context(request, call_next):
        request.state.request_id = 'alarm-contract'
        return await call_next(request)

    with TestClient(app) as client:
        for suffix in ['status', 'incidents', 'incidents/page', 'reports', 'reports/page']:
            response = client.get(f'/api/v1/alarms/{suffix}?device_id=Cutter01')
            assert response.status_code == 200, response.text
        repository.ingest([alarm(1, start_time='2026-10-28 08:00:00')], now=datetime(2026, 10, 28, 9, tzinfo=ZoneInfo('Asia/Shanghai')), cursor=1, debounce_minutes=10)
        incident = repository.list_incidents(None)[0]
        repository.reserve_report(proposed_report_id='report', emission_key='report', machine_no='Cutter01', incident_ids=[incident['incident_id']], now='2026-10-28T09:00:00+08:00')
        repository.complete_report('report', analysis={'summary': 'model', 'overall_risk': 'warning', 'facts': [], 'findings': [], 'actions': [], 'limitations': []}, html_relative_path='alarms/a.html', pdf_relative_path='alarms/a.pdf', pdf_filename='a.pdf', file_size=10, sha256='sha', model_status='model', completed_at='2026-10-28T09:01:00+08:00')
        response = client.get('/api/v1/alarms/incidents/page?device_id=Cutter01')
        assert response.status_code == 200, response.text
        assert response.json()['data']['total'] == 1
        assert response.json()['data']['items'][0]['report_status'] == 'completed'
        for suffix in ['reports', 'reports/page']:
            response = client.get(f'/api/v1/alarms/{suffix}?device_id=Cutter01')
            assert response.status_code == 200, response.text
            data = response.json()['data']
            item = data[0] if isinstance(data, list) else data['items'][0]
            original = repository.get_report('report')
            assert original.keys() <= item.keys()
            assert item['analysis']['summary'] == 'model'
