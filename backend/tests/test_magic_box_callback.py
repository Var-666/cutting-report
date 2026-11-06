"""Completion callbacks use the existing dispatch row and preserve dispatch semantics."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.cutting import callback_router, router
from app.models.cutting_contracts import CuttingDispatch
from tests.dispatch_fixtures import dispatch_service as _service


@pytest.mark.parametrize(('body', 'content_type', 'field', 'error_type', 'input_type', 'invalid_value'), [
    ({'FinishBy':'(10.1.133.197)', 'finishTime':'2026-11-05T16:20:30'}, 'application/json',
     'finishTime', 'value_error', 'str', '2026-11-05T16:20:30'),
    ({'FinishBy':'unknown', 'finishTime':'2026-11-05 16:20:30'}, 'application/json',
     'FinishBy', 'value_error', 'str', 'unknown'),
    ({'finishBy':'unknown', 'finishTime':'2026-11-05 16:20:30'}, 'application/json',
     'finishBy', 'value_error', 'str', 'unknown'),
    ({'FinishBy':'(10.1.133.197)', 'finishTime':'2026-11-05 16:20:30', 'material':None}, 'application/json',
     'material', 'string_type', 'NoneType', None),
    ({'FinishBy':'(10.1.133.197)'}, 'application/json', 'finishTime', 'missing', 'dict', None),
    ({'finishTime':'2026-11-05 16:20:30', 'machineName':'actual-machine'}, 'application/json',
     'FinishBy', 'missing', 'dict', None),
    ('FinishBy=unknown&finishTime=bad-time', 'application/x-www-form-urlencoded',
     None, 'model_attributes_type', 'bytes', None),
])
def test_callback_validation_logs_field_errors_without_request_secrets(
        body, content_type, field, error_type, input_type, invalid_value, caplog):
    import json
    from fastapi.exceptions import RequestValidationError
    from app.main import safe_connection_validation
    from app.api.cutting import require_callback_source

    app = FastAPI()
    app.add_exception_handler(RequestValidationError, safe_connection_validation)
    app.dependency_overrides[require_callback_source] = lambda: None
    app.include_router(callback_router)
    fields = list(body) if isinstance(body, dict) else []
    if isinstance(body, dict):
        body = {**body, 'password':'private-body-secret', 'taskName':'实际完成任务',
                'taskIdentifier':'dsp_actual_task', 'thickness':12, 'material':body.get('material', 'A0')}
        body = json.dumps(body)
    with TestClient(app, client=('192.0.2.10', 50000)) as client:
        response = client.post('/api/v1/magic-box/callbacks/task-finished', content=body,
            headers={'Content-Type':content_type, 'Authorization':'private-header-secret'})
    assert response.status_code == 422
    detail = response.json()['detail'][0]
    assert detail['loc'] == (['body', field] if field else ['body'])
    assert detail['type'] == error_type
    records = [record for record in caplog.records if '魔盒完成通知校验失败' in record.message]
    assert len(records) == 1
    message = records[0].message
    assert '192.0.2.10' in message and content_type in message
    assert error_type in message and input_type in message
    assert 'body_fields=' in message
    for key in fields:
        assert key in message
    if field:
        assert field in message
    if invalid_value:
        assert invalid_value in message
    if fields:
        assert 'callback_body=' in message
        for value in ('实际完成任务', 'dsp_actual_task', "'thickness': 12"):
            assert value in message
    for secret in ('private-body-secret', 'private-header-secret'):
        assert secret not in message


def test_failed_callback_task_preview_is_bounded_and_does_not_dump_nested_values(caplog):
    from fastapi.exceptions import RequestValidationError
    from app.main import safe_connection_validation
    from app.api.cutting import require_callback_source

    app = FastAPI()
    app.add_exception_handler(RequestValidationError, safe_connection_validation)
    app.dependency_overrides[require_callback_source] = lambda: None
    app.include_router(callback_router)
    with TestClient(app) as client:
        response = client.post('/api/v1/magic-box/callbacks/task-finished', json={
            'finish_by':'机床(10.1.133.197)', 'finishTime':'2026-11-05 16:20:30',
            'taskName':'a' * 512 + 'hidden-overflow', 'material':{'password':'nested-secret'},
        })
    assert response.status_code == 422
    message = next(record.message for record in caplog.records if '魔盒完成通知校验失败' in record.message)
    assert "'finish_by': '机床(10.1.133.197)'" in message
    assert "'material': {'type': 'dict'}" in message
    assert 'hidden-overflow' not in message and 'nested-secret' not in message


def test_other_validation_failures_keep_the_existing_response_and_do_not_log_callbacks(caplog):
    from fastapi.exceptions import RequestValidationError
    from app.main import safe_connection_validation

    app = FastAPI()
    app.add_exception_handler(RequestValidationError, safe_connection_validation)
    @app.get('/api/v1/other')
    def other(count: int):
        return count
    with TestClient(app) as client:
        response = client.get('/api/v1/other', params={'count':'bad-number'})
    assert response.status_code == 422
    assert response.json()['detail'][0]['loc'] == ['query', 'count']
    assert not any('魔盒完成通知校验失败' in record.message for record in caplog.records)


@pytest.fixture
def completion(state_database, tmp_path):
    service, repository, admin = _service(state_database, tmp_path)
    draft = service.create_dispatch(document_id=service.pdm.files[0]['document_id'],
        machine_ip='10.1.133.197', material='A0', thickness=12, quantity=1, user=admin)
    with repository.connect() as connection:
        connection.execute("""UPDATE cutting_dispatches SET is_simulation=0, source_mode='http',
            status='sent', stage='complete', remote_task_id='box-task-uuid', sent_at='2026-11-05T01:00:00+00:00'
            WHERE dispatch_id=%s""", (draft['dispatch_id'],))
    context = SimpleNamespace(settings=replace(service.settings, magic_box_callback_enabled=True,
        magic_box_callback_allowed_ips=('192.0.2.10',)), cutting_dispatch=service)
    app = FastAPI()
    from app.database.workshop import WorkshopRepository
    app.state.workshop = WorkshopRepository(repository.database)
    @app.middleware('http')
    async def runtime(request, call_next):
        request.state.database_context = context
        return await call_next(request)
    app.include_router(callback_router)
    app.include_router(router)
    payload = {'taskIdentifier':draft['task_identifier'], 'FinishBy':'20kW切割机(10.1.133.197)',
               'finishTime':'2026-11-05 16:20:30', 'taskName':draft['document_no'], 'material':'A0', 'thickness':12}
    return service, repository, draft['dispatch_id'], app, context, payload


@pytest.mark.parametrize('machine_field', ['FinishBy', 'finishBy'])
def test_callback_matches_identifier_across_machines_and_repeated_notifications_do_not_overwrite(completion, machine_field):
    service, repository, identity, app, _, payload = completion
    payload.pop('FinishBy')
    machine = '实际完成机床(10.147.1.30)'
    payload[machine_field] = machine
    with repository.connect() as connection:
        connection.execute('UPDATE cutting_dispatches SET machine_ip=%s WHERE dispatch_id=%s',
                           ('10.147.1.40', identity))
    before = repository.get(identity)
    with TestClient(app, client=('192.0.2.10', 50000)) as client:
        response = client.post('/api/v1/magic-box/callbacks/task-finished', json=payload)
        assert response.status_code == 200 and response.json() == {'status':0, 'msg':'OK'}
        first = repository.get(identity)
        assert first['processing_finished_at'] == '2026-11-05T16:20:30+08:00'
        assert first['processing_finish_by'] == machine
        assert first['completion_received_at']
        assert client.post('/api/v1/magic-box/callbacks/task-finished', json={**payload,
            machine_field:'另一机床(10.147.1.31)', 'finishTime':'2026-11-05 16:30:00'}).status_code == 200
        assert repository.get(identity) == first
        assert client.get('/api/v1/cutting/dispatches').status_code == 401
    for key in ('status','stage','sent_at','remote_task_id','updated_at','machine_ip'):
        assert first[key] == before[key]
    item = service.list_dispatches(page=1, page_size=10)['items'][0]
    assert item['processing_status'] == 'completed' and item['processing_status_label'] == '已加工完成'
    assert CuttingDispatch.model_validate(item).model_dump(exclude_unset=True) == item
    from datetime import date
    trend = service.dispatch_trend(start_date=date(2026,11,5), end_date=date(2026,11,5))
    assert trend['items'] == [dict(date='2026-11-05', sent_count=1, completed_count=1)]


def test_captured_task_finished_payload_updates_status_and_daily_count(completion):
    from datetime import date
    service, repository, identity, app, _, _ = completion
    payload = {
        'eventType':'TASK_FINISHED', 'finishBy':'(10.147.1.30)',
        'finishTime':'2026-11-05 16:05:41', 'machineIp':'10.147.1.30', 'machineType':0,
        'material':'A0', 'taskIdentifier':'dsp_52244bbdb6884c1ab5',
        'taskName':'CMYZ0170_409P_S409P100A004.gen',
        'taskUuid':'fac16ddcda7a4b72bcae51e3accc1c65', 'thickness':10.0,
    }
    with repository.connect() as connection:
        connection.execute('UPDATE cutting_dispatches SET task_identifier=%s, remote_task_id=%s WHERE dispatch_id=%s',
                           (payload['taskIdentifier'], payload['taskUuid'], identity))
    with TestClient(app, client=('192.0.2.10', 50000)) as client:
        response = client.post('/api/v1/magic-box/callbacks/task-finished', json=payload)
    assert response.status_code == 200 and response.json() == {'status':0, 'msg':'OK'}
    item = repository.get(identity)
    assert item['processing_finished_at'] == '2026-11-05T16:05:41+08:00'
    assert item['processing_finish_by'] == '(10.147.1.30)'
    assert service.list_dispatches(page=1, page_size=10)['items'][0]['processing_status'] == 'completed'
    assert service.dispatch_trend(start_date=date(2026,11,5), end_date=date(2026,11,5))['items'] == [
        dict(date='2026-11-05', sent_count=1, completed_count=1)]


@pytest.mark.parametrize('machine', [None, '', 'unknown'])
def test_camel_case_machine_field_cannot_bypass_machine_validation(completion, machine):
    _, repository, identity, app, _, payload = completion
    payload.pop('FinishBy')
    payload['finishBy'] = machine
    with TestClient(app, client=('192.0.2.10', 50000)) as client:
        response = client.post('/api/v1/magic-box/callbacks/task-finished', json=payload)
    assert response.status_code == 422
    assert repository.get(identity)['processing_finished_at'] is None


@pytest.mark.parametrize('identifier',['','other-system-task'])
def test_callback_acknowledges_test_or_unrelated_events_without_guessing_by_filename(completion, identifier):
    _, repository, identity, app, _, payload = completion
    with TestClient(app, client=('192.0.2.10', 50000)) as client:
        response = client.post('/api/v1/magic-box/callbacks/task-finished', json={**payload,'taskIdentifier':identifier})
    assert response.json() == {'status':0,'msg':'OK'}
    assert repository.get(identity)['processing_finished_at'] is None


@pytest.mark.parametrize('changes', [{'finishTime':'bad-time'}, {'FinishBy':'unknown'}])
def test_invalid_completion_fields_never_complete_a_dispatch(completion, changes):
    _, repository, identity, app, _, payload = completion
    with TestClient(app, client=('192.0.2.10', 50000)) as client:
        response = client.post('/api/v1/magic-box/callbacks/task-finished', json={**payload,**changes})
    assert response.status_code == 422
    assert repository.get(identity)['processing_finished_at'] is None


def test_callbacks_default_closed_and_reject_unlisted_peer_even_with_forwarded_header(completion):
    _, repository, identity, app, context, payload = completion
    with TestClient(app, client=('192.0.2.11', 50000)) as client:
        assert client.post('/api/v1/magic-box/callbacks/task-finished', json=payload,
                           headers={'X-Forwarded-For':'192.0.2.10'}).status_code == 403
    context.settings = replace(context.settings, magic_box_callback_enabled=False)
    with TestClient(app, client=('192.0.2.10', 50000)) as client:
        assert client.post('/api/v1/magic-box/callbacks/task-finished', json=payload).status_code == 404
    assert repository.get(identity)['processing_finished_at'] is None


def test_simulation_or_unuploaded_dispatch_cannot_be_completed(completion):
    _, repository, identity, app, _, payload = completion
    for change in ("is_simulation=1", "is_simulation=0, remote_task_id=NULL"):
        with repository.connect() as connection:
            connection.execute(f'UPDATE cutting_dispatches SET {change} WHERE dispatch_id=%s',(identity,))
        with TestClient(app, client=('192.0.2.10', 50000)) as client:
            assert client.post('/api/v1/magic-box/callbacks/task-finished', json=payload).status_code == 200
        assert repository.get(identity)['processing_finished_at'] is None


def test_callback_database_failure_is_not_acknowledged_as_success(completion, monkeypatch):
    service, _, _, app, _, payload = completion
    def unavailable(**kwargs):
        raise RuntimeError('database unavailable')
    monkeypatch.setattr(service.dispatches,'record_completion',unavailable)
    with TestClient(app, client=('192.0.2.10',50000), raise_server_exceptions=False) as client:
        assert client.post('/api/v1/magic-box/callbacks/task-finished',json=payload).status_code == 500


def test_concurrent_notifications_commit_one_completion(completion):
    service, repository, identity, _, _, payload = completion
    before = repository.get(identity)
    def notify(_):
        return service.receive_completion(task_identifier=payload['taskIdentifier'], machine_ip='10.1.133.197',
            finished_at=datetime.fromisoformat('2026-11-05T16:20:30+08:00'), finish_by=payload['FinishBy'])
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert all(pool.map(notify,range(4)))
    after = repository.get(identity)
    assert after['processing_finished_at'] == '2026-11-05T16:20:30+08:00'
    assert after['status'] == before['status']


def test_callback_settings_require_specific_source_addresses():
    from app.config import Settings
    with pytest.raises(ValueError,match='ALLOWED_IPS'):
        Settings(magic_box_callback_enabled=True)
    with pytest.raises(ValueError,match='具体IP'):
        Settings(magic_box_callback_allowed_ips=('0.0.0.0/0',))


def test_completion_chart_counts_by_finish_day_and_drilldown_uses_the_same_day(completion):
    from datetime import date
    service, repository, identity, app, _, payload = completion
    with repository.connect() as connection:
        connection.execute('UPDATE cutting_dispatches SET quantity=10 WHERE dispatch_id=%s',(identity,))
    with TestClient(app,client=('192.0.2.10',50000)) as client:
        for _ in range(2):
            assert client.post('/api/v1/magic-box/callbacks/task-finished',
                json={**payload,'finishTime':'2026-11-06 00:30:00'}).status_code == 200
    trend = service.dispatch_trend(start_date=date(2026,11,5),end_date=date(2026,11,6))
    assert trend['items'] == [dict(date='2026-11-05',sent_count=1,completed_count=0),
                              dict(date='2026-11-06',sent_count=0,completed_count=1)]
    completed = service.list_dispatches(page=1,page_size=10,start_date=date(2026,11,6),
                                       end_date=date(2026,11,6),date_type='completed')
    assert completed['total']==1 and completed['items'][0]['dispatch_id']==identity
    assert service.list_dispatches(page=1,page_size=10,start_date=date(2026,11,6),end_date=date(2026,11,6))['total']==0
    assert service.dispatch_trend(start_date=date(2026,11,5),end_date=date(2026,11,6),machine_ip='10.1.133.198')['items']==[
        dict(date='2026-11-05',sent_count=0,completed_count=0),dict(date='2026-11-06',sent_count=0,completed_count=0)]
