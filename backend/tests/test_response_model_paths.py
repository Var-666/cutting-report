from tests.line_fixtures import as_line_services, runtime_line
"""Exercise the new response models through real services and PostgreSQL repositories."""
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import alarms, cutting, workshop
from app.api.auth import require_admin, require_user, require_worker
from app.database.alarms import AlarmRepository
from app.database.workshop import WorkshopRepository
from app.models.domain import WorkshopRecord
from app.services.alarm_analysis_service import AlarmAnalysisService
from app.services.llm_service import AnalysisOutputValidationError
from tests.test_alarm_monitor import alarm, incident
from tests.dispatch_fixtures import RecordingMagicClient, push_service as _push_service


@pytest.fixture
def api(tmp_path, state_database):
    remote = RecordingMagicClient()
    service, dispatches, admin = _push_service(state_database, tmp_path, remote)
    repository = WorkshopRepository(dispatches.database, scope_machine_no='Cutter01')
    worker = repository.create_user(username='worker', display_name='工人', password='worker-password',
                                    role='worker', now='2026-10-01T08:00:00')
    other = repository.create_user(username='other', display_name='另一工人', password='worker-password',
                                   role='worker', now='2026-10-01T08:00:00')
    records = [WorkshopRecord(
        task_id='task1', project_code='SHIP', ship_no='SHIP', segment_name='P1', program_no='p.gen',
        cut_length=Decimal('1.2'), groove_length=Decimal('0.1'), work_time=Decimal('120'),
        production_time=datetime(2026, 10, 1, 8), production_date=date(2026, 10, 1),
    ), WorkshopRecord(
        task_id='task2', project_code=None, ship_no=None, segment_name=None, program_no=None,
        cut_length=None, groove_length=None, work_time=None, production_date=date(2026, 10, 1),
    )]
    bundle = SimpleNamespace(workshop_records=records, data_updated_at=None)
    query = SimpleNamespace(enrich_ship_names=lambda records: records, latest_production_date=lambda _: date(2026, 10, 1),
                            prepare_workshop_data=lambda _: bundle)
    alarm_repository = AlarmRepository(dispatches.database, source_name='production')
    alarm_repository.initialize()
    runtime = SimpleNamespace(settings=service.settings, workshop=repository, query_service=query,
                              range_report_service=SimpleNamespace(workshop_records=lambda *_: (
                                  bundle, repository.get_daily_report_records('2026-10-01')[1])),
                              alarm_repository=alarm_repository,
                              alarm_worker=SimpleNamespace(enabled=True, thread=None))
    app = FastAPI()
    for router in (cutting.router, workshop.router, alarms.router):
        app.include_router(router)
    app.state.lines = as_line_services({'Cutter01': runtime})
    app.state.worker_status_reader = SimpleNamespace(status=lambda: {'state': 'running'})
    app.dependency_overrides[require_admin] = lambda: admin
    app.dependency_overrides[require_user] = lambda: admin
    app.dependency_overrides[require_worker] = lambda: worker

    @app.middleware('http')
    async def context(request, call_next):
        request.state.request_id = 'response-model-paths'
        request.state.database_context = SimpleNamespace(cutting_dispatch=service, lines=app.state.lines)
        return await call_next(request)

    with TestClient(app) as client:
        yield SimpleNamespace(client=client, service=service, repository=repository, runtime=runtime,
                              alarms=alarm_repository, admin=admin, worker=worker, other=other, remote=remote)


def data(response, status=200):
    assert response.status_code == status, response.text
    body = response.json()
    assert body['success'] is True
    assert body['request_id'] == 'response-model-paths'
    return body['data']


def test_cutting_response_models_cover_catalogs_machines_and_dispatch_lifecycle(api):
    client, service = api.client, api.service
    assert data(client.get('/api/v1/cutting/dispatches'))['items'] == []
    status = data(client.get('/api/v1/cutting/status'))
    assert status['file_source']['enabled'] and status['magic_box']['configured']
    assert data(client.get('/api/v1/cutting/status?scope=download'))['can_push'] is False
    project = data(client.get('/api/v1/cutting/projects'))['items'][0]
    service.pdm.catalogs[0]['sort_sn'] = Decimal('1.5')
    catalogs = data(client.get('/api/v1/cutting/catalogs', params={'project_id': project['project_id']}))
    assert catalogs['items'][0]['sort_sn'] == 1.5
    assert catalogs['tree'][0]['children']
    api.remote.list_machines = lambda: [{
        'machine_ip': '10.1.133.197', 'server_name': '切割机', 'alive_time': '2026-10-01 08:00:00',
        'applications': [{'name': 'CypCut', 'version': '6.3'}],
    }]
    machines = data(client.get('/api/v1/cutting/machines'))
    assert machines['items'][0]['applications'][0]['version'] == '6.3'
    assert machines['items'][0]['online'] is True
    api.remote.list_machines = lambda: []
    offline = data(client.get('/api/v1/cutting/machines'))['items'][0]
    assert offline['alive_time'] is None and offline['applications'] == []

    first, second = service.pdm.files[:2]
    parameters = {'machine_ip': '10.1.133.197', 'material': 'A0', 'thickness': 8, 'quantity': 1,
                  'name_check_confirmed': True}
    created = data(client.post('/api/v1/cutting/dispatches', json={
        **parameters, 'document_id': first['document_id'],
    }), 201)
    assert created['is_simulation'] is False
    batch = data(client.post('/api/v1/cutting/dispatches/batch', json={
        'machine_ip': parameters['machine_ip'],
        'items': [{**parameters, 'document_id': row['document_id'], 'version_id': row['version_id'],
                   'file_path': row['file_path']} for row in (first, second)]
                 + [{**parameters, 'document_id': 'missing', 'file_path': 'missing.GEN'}],
    }), 201)
    assert [item['status'] for item in batch['items']] == ['duplicate', 'created', 'failed']
    assert batch['created_count'] == batch['duplicate_count'] == batch['failed_count'] == 1
    assert batch['items'][2]['dispatch'] is None and batch['items'][2]['error']
    files = data(client.get('/api/v1/cutting/files', params={'project_id': first['project_id']}))
    assert files['items'][0]['dispatch_status']['status'] == 'ready'
    path = f"/api/v1/cutting/dispatches/{created['dispatch_id']}"
    assert data(client.get(path))['is_simulation'] is False
    sent = data(client.post(path + '/execute'))
    assert sent['status'] == 'sent' and sent['local_file_cleanup']['deleted'] is True
    assert sent['local_file_cleanup']['error'] is None
    assert data(client.post(path + '/cancel'))['status'] == 'cancelled'
    assert data(client.get('/api/v1/cutting/dispatches'))['total'] == 2
    service.pdm.mode = 'mock'
    simulated = data(client.post('/api/v1/cutting/dispatches', json={
        **parameters, 'document_id': first['document_id'],
    }), 201)
    assert simulated['is_simulation'] is True


def test_workshop_response_models_cover_legacy_confirmations_and_all_mutations(api):
    client, repository = api.client, api.repository
    query = '?device_id=Cutter01'
    report_date = {'report_date': '2026-10-01'}
    assert data(client.get('/api/v1/workshop/tasks' + query + '&report_date=2026-10-01'))['report'] is None
    assert data(client.get('/api/v1/workshop/confirmation-audits' + query))['items'] == []
    assert data(client.get('/api/v1/workshop/latest-date' + query + '&on_or_before=2026-10-01'))['report_date'] == '2026-10-01'
    api.runtime.query_service.latest_production_date = lambda _: None
    assert data(client.get('/api/v1/workshop/latest-date' + query + '&on_or_before=2026-10-01'))['report_date'] is None
    report = data(client.post('/api/v1/workshop/reports' + query, json=report_date))
    assert report['item_count'] == 2 and report['source_mode']
    repository.confirm_task(task_id='task1', user=api.worker, now='2026-10-01T09:00:00+08:00')
    confirmed = data(client.post('/api/v1/workshop/tasks/task1/confirm' + query, json=report_date))
    assert confirmed['report_id'] is None
    batch = data(client.post('/api/v1/workshop/tasks/confirm' + query, json={
        **report_date, 'task_ids': ['task1', 'task2'],
    }))
    assert batch['confirmed_count'] == 2
    assert [item['report_id'] for item in batch['items']] == [None, report['report_id']]
    tasks = data(client.get('/api/v1/workshop/tasks' + query + '&report_date=2026-10-01'))
    assert tasks['worker_summaries'][0]['confirmed_task_count'] == 2
    assert tasks['top_cut_length_tasks'][0]['cut_length_m'] == 1.2
    assert tasks['duration_distribution'] and tasks['status_distribution']
    assert tasks['current_user']['active'] is True
    assert 'password_hash' not in tasks['current_user']
    ranged = data(client.get('/api/v1/workshop/range' + query + '&start_date=2026-10-01&end_date=2026-10-01'))
    assert ranged['report']['generated_at'] is None
    assert ranged['trend'][0]['confirmed_count'] == 2
    assert data(client.post('/api/v1/workshop/tasks/task1/correct' + query, json={
        **report_date, 'action': 'reassign', 'worker_id': api.other['user_id'], 'reason': '现场核对',
    }))['new_worker_id'] == api.other['user_id']
    assert data(client.post('/api/v1/workshop/tasks/task2/revoke' + query, json={
        **report_date, 'reason': '现场核对',
    }))['action'] == 'worker_revoke'
    assert data(client.post('/api/v1/workshop/tasks/task1/correct' + query, json={
        **report_date, 'action': 'revoke', 'reason': '现场核对',
    }))['action'] == 'admin_revoke'
    audits = data(client.get('/api/v1/workshop/confirmation-audits' + query))['items']
    assert {item['action'] for item in audits} == {'confirm', 'admin_reassign', 'worker_revoke', 'admin_revoke'}
    assert all(item['action_label'] for item in audits)


def test_file_response_models_cover_null_physical_join_and_database_metadata(api):
    raw = api.service.pdm.files[0]
    raw.update({key: None for key in raw if key.startswith('physical_')})
    raw.update(file_path=None, version_id=None, version=None, operate_time=datetime(2026, 10, 1, 8),
               catalog_type=1, classification_type=0, document_state=0)
    responses = [
        api.client.get('/api/v1/cutting/files', params={'project_id': raw['project_id']}),
        api.client.post('/api/v1/cutting/files/search', json={
            'filters': {'rules': [{'field': 'document_id', 'operator': 'eq', 'value': raw['document_id']}]},
        }),
    ]
    for response in responses:
        item = next(item for item in data(response)['items'] if item['document_id'] == raw['document_id'])
        assert item['physical_file_type'] is None and item['physical_version_no'] is None
        assert item['path_available'] is False and item['path_error']
        assert item['operate_time'] == '2026-10-01T08:00:00'
        assert item['catalog_type'] == 1 and item['classification_type'] == item['document_state'] == 0


def test_alarm_response_models_cover_closed_incidents_and_incomplete_reports(api):
    client, repository = api.client, api.alarms
    now = datetime(2026, 10, 1, 10, tzinfo=ZoneInfo('Asia/Shanghai'))
    repository.ingest([
        alarm(1, start_time='2026-10-01 08:00:00'),
        alarm(2, alarm_code=4101, start_time='2026-10-01 08:30:00', remove_time='2026-10-01 09:00:00'),
    ], now=now, cursor=2, debounce_minutes=10)
    query = '?device_id=Cutter01'
    status = data(client.get('/api/v1/alarms/status' + query))
    assert status['worker_running'] is True and status['last_alarm_id'] == 2
    incidents = data(client.get('/api/v1/alarms/incidents' + query))
    assert {item['status'] for item in incidents} == {'open', 'closed'}
    page = data(client.get('/api/v1/alarms/incidents/page' + query))
    assert page['dominant_alarm']['count'] >= 1 and page['severity_counts']
    for index, report_id in enumerate(('generating', 'failed')):
        repository.reserve_report(proposed_report_id=report_id, emission_key=report_id,
                                  machine_no='Cutter01', incident_ids=[incidents[index]['incident_id']],
                                  now=now.isoformat())
    repository.fail_report('failed', '模型暂不可用')
    for suffix in ('reports', 'reports/page'):
        result = data(client.get('/api/v1/alarms/' + suffix + query))
        items = result if isinstance(result, list) else result['items']
        assert {item['status'] for item in items} == {'generating', 'failed'}
        assert all(item['analysis'] is None for item in items)
        assert next(item for item in items if item['status'] == 'failed')['error_message'] == '模型暂不可用'


@pytest.mark.parametrize('section,field,value', [
    ('findings', 'title', 123),
    ('findings', 'analysis', None),
    ('findings', 'possible_causes', '可能存在设备异常'),
    ('findings', 'confidence', 'NaN'),
    ('actions', 'priority', []),
    ('actions', 'priority', None),
    ('actions', 'action', {'text': '现场核查'}),
    ('actions', 'action', None),
])
def test_alarm_generation_rejects_fields_that_would_break_response_models(section, field, value):
    facts = AlarmAnalysisService._facts([incident()])
    analysis = {
        'summary': '需要核查 [E001]', 'limitations': [],
        'findings': [{'title': '设备告警', 'analysis': '需要核查 [E001]',
                      'possible_causes': ['可能存在设备异常'], 'confidence': 0.6, 'evidence_ids': ['E001']}],
        'actions': [{'priority': 'high', 'action': '按规程核查 [E001]', 'evidence_ids': ['E001']}],
    }
    analysis[section][0][field] = value
    with pytest.raises(AnalysisOutputValidationError, match='告警分析字段格式错误'):
        AlarmAnalysisService._validate(analysis, facts)


@pytest.mark.parametrize('field', ['priority', 'action'])
def test_alarm_generation_rejects_missing_action_fields(field):
    facts = AlarmAnalysisService._facts([incident()])
    analysis = {'summary': '需要核查 [E001]', 'limitations': [], 'findings': [],
                'actions': [{'priority': 1, 'action': '按规程核查 [E001]', 'evidence_ids': ['E001']}]}
    del analysis['actions'][0][field]
    with pytest.raises(AnalysisOutputValidationError, match='告警分析字段格式错误'):
        AlarmAnalysisService._validate(analysis, facts)
