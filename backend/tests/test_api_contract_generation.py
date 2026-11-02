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
            if method not in {'get', 'post', 'put', 'delete'} or '/pdf' in url or url.endswith('/view'):
                continue
            success = next(value for code, value in operation['responses'].items() if code.startswith('2'))
            if 'application/json' not in success.get('content', {}):
                continue
            assert '$ref' in success['content']['application/json']['schema'], url
    assert 'AlarmIncidentPage' in schema['components']['schemas']
    assert 'WorkshopResult' in schema['components']['schemas']
    assert 'CuttingDispatch' in schema['components']['schemas']
    query = schema['paths']['/api/v1/welding/jobs']['get']['parameters']
    assert {item['name'] for item in query if item.get('required')} == {'start', 'end'}
    assert not schema['components']['schemas']['ConfigurationDiagnostics']['properties']['items'] == {}
    assert schema['components']['schemas']['LeadershipPeriodRecord']['properties']['cut_length_m']['type'] == 'number'
    download = schema['paths']['/api/v1/welding/export']['get']['responses']['200']['content']
    assert set(download) == {'text/csv', 'text/html', 'application/pdf'}


@pytest.fixture
def contract_http(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api import automation, configuration, database, leadership, equipment, energy, reports, analysis
    from app.api.auth import require_admin
    from app.config import DEVICE_CATALOG, Settings
    from app.services.application_runtime import build_runtime

    catalog = tmp_path / 'backend/app/configuration/devices.json'
    catalog.parent.mkdir(parents=True)
    catalog.write_text(DEVICE_CATALOG.model_dump_json())
    runtime = build_runtime(Settings(project_root=tmp_path, data_source_mode='demo', weld_enabled=False,
        worker_database_auth_enabled=False, file_database_enabled=False, energy_api_enabled=False, llm_enabled=False), role='web')
    admin = runtime.users.bootstrap_admin(username='contract-admin', display_name='管理员',
        password='test-password', now='2026-11-02T10:00:00+08:00')
    app = FastAPI()
    app.state.runtime = runtime
    app.dependency_overrides[require_admin] = lambda: admin
    for router in (database.router, automation.router, configuration.router, leadership.router, equipment.router, energy.router, reports.router, analysis.router):
        app.include_router(router)

    @app.middleware('http')
    async def context(request, call_next):
        request.state.request_id = 'configuration-contract'
        return await call_next(request)

    with TestClient(app) as client:
        yield client


def check_contract(response, model):
    from app.models.contracts import ApiEnvelope
    assert response.status_code in (200, 201, 202), response.text
    raw = response.json()
    contract = ApiEnvelope[model].model_validate(raw)
    assert contract.model_dump(mode='json', exclude_unset=True) == raw
    return raw['data']


def test_configuration_and_automation_documented_responses_match_http_payloads(contract_http):
    from app.models.contracts import Items, Page
    from app.models.configuration_contracts import DatabaseConfiguration, DatabaseStatus, ConfigurationSaved, ConfigurationDiagnostics
    from app.models.automation_contracts import AutomationConfiguration, AutomationStatus, AutomationSchedule, EmailDelivery, ScheduledRun
    client = contract_http
    check_contract(client.get('/api/v1/database/status'), DatabaseStatus)
    check_contract(client.get('/api/v1/database/configuration'), DatabaseConfiguration)
    check_contract(client.put('/api/v1/database/configuration', json={
        'data_source_mode': 'demo', 'production': {}, 'worker': {}, 'file': {},
    }), ConfigurationSaved)
    check_contract(client.get('/api/v1/automation/configuration'), AutomationConfiguration)
    check_contract(client.put('/api/v1/automation/configuration', json={'smtp_enabled': False}), ConfigurationSaved)
    check_contract(client.get('/api/v1/automation/status'), AutomationStatus)
    schedule = check_contract(client.post('/api/v1/automation/schedules', json={
        'device_id': 'Cutter01', 'name': '契约测试', 'report_type': 'workshop',
        'recipients': ['contract@example.com'],
    }), AutomationSchedule)
    check_contract(client.get('/api/v1/automation/schedules'), Items[AutomationSchedule])
    check_contract(client.post(f"/api/v1/automation/schedules/{schedule['schedule_id']}/run"), ScheduledRun)
    check_contract(client.get('/api/v1/automation/deliveries?page=1&page_size=5'), Page[EmailDelivery])
    check_contract(client.get('/api/v1/configuration/diagnostics'), ConfigurationDiagnostics)



def test_cutting_reports_and_state_dependent_job_responses_match_contracts(contract_http):
    from app.models.contracts import Page
    from app.models.report_contracts import LeadershipReport, ReportAccepted, ReportJob
    from app.models.energy_contracts import EquipmentReport, TaskEnergyReport, EnergyRangeReport, EnergyPeriodReport, EnergyDayReport, EnergyMonthReport
    client = contract_http
    params = {'device_id': 'Cutter01'}
    for path, query, model in [
        ('leadership-report', {'report_month': '2026-08'}, LeadershipReport),
        ('leadership-report', {'start_date': '2026-08-18', 'end_date': '2026-08-20'}, LeadershipReport),
        ('equipment-report', {'report_date': '2026-08-18'}, EquipmentReport),
        ('energy-report/tasks', {'report_date': '2026-08-18'}, TaskEnergyReport),
        ('energy-report/range', {'start_date': '2026-08-18', 'end_date': '2026-08-20'}, EnergyRangeReport),
        ('energy-report/day', {'report_date': '2026-08-18'}, EnergyDayReport),
        ('energy-report/month', {'report_month': '2026-08'}, EnergyMonthReport),
        ('energy-report/monthly-range', {'start_date': '2026-08-01', 'end_date': '2026-08-20'}, EnergyPeriodReport),
    ]:
        check_contract(client.get(f'/api/v1/{path}', params={**params, **query}), model)
    accepted = check_contract(client.post('/api/v1/reports', params=params, json={
        'report_type': 'workshop', 'report_date': '2026-08-18',
    }), ReportAccepted)
    assert set(accepted) == {'report_id', 'status', 'status_url'}
    url = f"/api/v1/reports/{accepted['report_id']}/status"
    pending = check_contract(client.get(url, params=params), ReportJob)
    assert 'download_url' not in pending and pending['error'] is None
    metadata = client.app.state.runtime.devices['Cutter01'].metadata
    metadata.update_status(accepted['report_id'], status='failed', stage_message='契约测试失败状态')
    failed = check_contract(client.get(url, params=params), ReportJob)
    assert failed['error']['message'] == '契约测试失败状态'
    check_contract(client.get('/api/v1/reports', params=params), Page[ReportJob])


def test_required_dispatch_fields_cannot_silently_disappear():
    with pytest.raises(ValidationError):
        CuttingDispatch.model_validate({'dispatch_id': 'missing-required-fields'})


@pytest.mark.parametrize('file_type', [0, 1, '0', 'GEN', None])
def test_file_list_and_search_preserve_pdm_file_type(tmp_path, file_type):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api import cutting
    from app.api.auth import require_user

    service, _, admin = _service(tmp_path)
    raw = service.pdm.files[0]
    raw['physical_file_type'] = file_type
    app = FastAPI()
    app.include_router(cutting.router)
    app.state.cutting_dispatch = service
    app.dependency_overrides[require_user] = lambda: admin

    @app.middleware('http')
    async def context(request, call_next):
        request.state.request_id = 'pdm-file-type-contract'
        return await call_next(request)

    with TestClient(app) as client:
        responses = [
            client.get('/api/v1/cutting/files', params={'project_id': raw['project_id']}),
            client.post('/api/v1/cutting/files/search', json={
                'filters': {'rules': [{'field': 'document_id', 'operator': 'eq', 'value': raw['document_id']}]},
            }),
        ]
        for response in responses:
            assert response.status_code == 200, response.text
            item = next(item for item in response.json()['data']['items'] if item['document_id'] == raw['document_id'])
            assert item['physical_file_type'] == file_type
            assert type(item['physical_file_type']) is type(file_type)


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


@pytest.mark.parametrize('priority', [1, 'high', 'medium', 'low'])
def test_alarm_http_contracts_cover_empty_and_populated_reports(tmp_path, priority):
    from datetime import datetime
    from types import SimpleNamespace
    from zoneinfo import ZoneInfo
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api import alarms
    from app.api.auth import require_admin
    from app.config import Settings
    from app.database.alarms import AlarmRepository
    from app.services.alarm_analysis_service import AlarmAnalysisService
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
        facts = AlarmAnalysisService._facts([incident])
        analysis = {
            'summary': 'model [E001]',
            'facts': facts,
            'findings': [{
                'title': '设备告警', 'analysis': '需要现场核查 [E001]',
                'possible_causes': ['可能存在设备异常'], 'confidence': 0.6,
                'evidence_ids': ['E001'],
            }],
            'actions': [{'priority': priority, 'action': '按规程核查 [E001]', 'evidence_ids': ['E001']}],
            'limitations': ['仅依据告警记录'],
        }
        AlarmAnalysisService._validate(analysis, facts)
        repository.reserve_report(proposed_report_id='report', emission_key='report', machine_no='Cutter01', incident_ids=[incident['incident_id']], now='2026-10-28T09:00:00+08:00')
        repository.complete_report('report', analysis=analysis, html_relative_path='alarms/a.html', pdf_relative_path='alarms/a.pdf', pdf_filename='a.pdf', file_size=10, sha256='sha', model_status='model', completed_at='2026-10-28T09:01:00+08:00')
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
            assert item['analysis'] == analysis
            assert type(item['analysis']['actions'][0]['priority']) is type(priority)
