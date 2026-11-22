from tests.runtime_factories import make_database_services
from app.services.production_lines import ProductionLineRuntime
from tests.runtime_factories import make_device_runtime
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import json
import logging
import os
from threading import Lock
from traceback import format_exception
from types import SimpleNamespace

from fastapi import Request
from tests.web_fixtures import embedded_app
from fastapi.responses import Response
from fastapi.testclient import TestClient
from psycopg_pool import PoolTimeout
import pytest

from app.api.auth import require_user
from app.api.operations import router
from app.core import operations_logging
from app.core.operations_logging import configured_logging, event, operation_context, prune_logs
from app.database.operations import OperationsRepository
from app.database.database_monitors import DatabaseMonitorRepository
from app.models.database_monitor import DatabaseMonitorInput
from app.models.operations import IncidentFilters, LogFilters
from app.services.application_runtime import build_runtime
from app.services.magic_box_service import MagicBoxError
from app.services.operations_log_query import OperationsLogQuery
from app.services.operations_service import BUSINESS_ERROR_CODES
from tests.dispatch_fixtures import RecordingMagicClient, push_service


def test_structured_logs_merge_paginate_rotate_and_expire(tmp_path, monkeypatch):
    monkeypatch.setattr(operations_logging, 'MAX_FILE_BYTES', 1800)
    logger = logging.getLogger('app.test_operations')
    for role, instance in [('web', 'web'), ('scheduler', 'scheduler'), ('worker', 'consumer-a'),
                           ('worker', 'consumer-b'), ('automation', 'automation')]:
        with configured_logging(tmp_path, role, instance):
            with operation_context(request_id='web_fixture', line_id='cut-a', report_id='r_fixture', task_id='t_fixture'):
                try:
                    raise RuntimeError('PASSWORD=private-password; Token=private-token')
                except RuntimeError:
                    logger.exception('https://example.invalid/sso/callback?code=private-code %s', 'private-password')
                    event(logger, logging.ERROR, '报表运行失败', error_code='TEST_FAILURE',
                          analysis_id='test_analysis', exc_info=True)
            for _ in range(8):
                event(logger, logging.INFO, '工作完成')
    query = OperationsLogQuery(tmp_path, 14)
    start = datetime.now(UTC) - timedelta(minutes=1)
    end = datetime.now(UTC)
    page = query.query(LogFilters(start=start, end=end, error_code='TEST_FAILURE', limit=2))
    assert len(page['items']) == 2 and page['next_cursor']
    next_page = query.query(LogFilters(start=start, end=end, error_code='TEST_FAILURE', limit=2, cursor=page['next_cursor']))
    assert {item['id'] for item in page['items']}.isdisjoint(item['id'] for item in next_page['items'])
    consumer = query.query(LogFilters(role='worker', request_id='web_fixture', task_id='t_fixture', error_code='TEST_FAILURE'))
    assert {item['instance_id'] for item in consumer['items']} == {'consumer-a', 'consumer-b'}
    assert all(item['exception']['type'] == 'RuntimeError' and item['analysis_id'] == 'test_analysis' for item in consumer['items'])
    files = list(operations_logging.log_paths(tmp_path / 'operations'))
    assert any(path.suffix == '.1' for path in files)
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in files)
    payload = ''.join(path.read_text() for path in files)
    assert all(secret not in payload for secret in ('private-password', 'private-code', 'private-token'))
    assert {json.loads(line)['role'] for path in files for line in path.read_text().splitlines()} == set(operations_logging.ROLES)
    stale = datetime.now(UTC).timestamp() - 20 * 86400
    for path in files:
        os.utime(path, (stale, stale))
    prune_logs(tmp_path, 14)
    assert not list(operations_logging.log_paths(tmp_path / 'operations'))


def test_log_instance_filter_applies_before_scan_budget(tmp_path, monkeypatch):
    from app.services import operations_log_query

    logger = logging.getLogger('app.test_operations')
    with configured_logging(tmp_path, 'worker', 'consumer-a'):
        event(logger, logging.WARNING, '第一实例异常', error_code='FIXTURE')
    segment, = operations_logging.log_paths(tmp_path / 'operations')
    with configured_logging(tmp_path, 'worker', 'consumer-b'):
        for _ in range(10):
            event(logger, logging.INFO, '另一个实例执行任务')
    monkeypatch.setattr(operations_log_query, 'MAX_SCAN_BYTES', segment.stat().st_size)

    result = OperationsLogQuery(tmp_path, 14).query(LogFilters(role='worker', instance_id='consumer-a', error_code='FIXTURE'))
    assert 'scan_limit' not in result['coverage_warnings']
    assert [item['message'] for item in result['items']] == ['第一实例异常']


def test_data_quality_tracks_one_incident_across_roles_and_requires_matching_recovery(postgres_state, tmp_path):
    database, _, _ = postgres_state
    repo = OperationsRepository(database)
    logger = logging.getLogger('app.test_operations')
    code = 'ENERGY_DAY_DATA_QUALITY'

    def observation(role, day, level, state):
        with configured_logging(tmp_path, role, 'instance-' + role) as logs:
            operations_logging.data_quality(logger, code, '测试数据状态', scope=(day,), state=state,
                                            level=level, line_id='cut-a', source_id='energy:test', stage='energy_day',
                                            period_start=day, period_end=day, missing_count=10 if level >= logging.WARNING else 0)
            return logs.incidents.get_nowait()

    day = '2026-08-18'
    first = observation('web', day, logging.WARNING, 'source_failed')
    repo.record([first])
    original, = repo.list(IncidentFilters(error_code=code))['items']
    assert original['level'] == 'WARNING' and original['context']['missing_count'] == '10'
    repo.record([observation('worker', day, logging.ERROR, 'missing_readings'),
                 observation('worker', day, logging.ERROR, 'missing_readings')])
    repo.record([observation('worker', '2026-08-19', logging.INFO, 'verified')])
    incident, = repo.list(IncidentFilters(error_code=code))['items']
    assert incident['incident_id'] == original['incident_id'] and incident['occurrences'] == 1
    assert (incident['role'], incident['instance_id'], incident['level'], incident['status']) == ('worker', 'instance-worker', 'ERROR', 'open')
    assert incident['last_seen_at'] > original['last_seen_at']

    success = observation('worker', day, logging.INFO, 'verified')
    repo.record([success])
    repo.record([first])
    assert repo.get(original['incident_id'])['status'] == 'recovered'
    repo.record([observation('web', day, logging.ERROR, 'counter_reset')])
    again, = repo.list(IncidentFilters(error_code=code, status='open'))['items']
    assert again['incident_id'] != original['incident_id']
    logs = OperationsLogQuery(tmp_path, 14)
    selection = LogFilters(start=datetime.fromisoformat(first['timestamp']), error_code=code, resource_id=again['resource_id'])
    rows = logs.query(selection)['items']
    assert {row['data_quality']['state'] for row in rows} == {'source_failed', 'missing_readings', 'verified', 'counter_reset'}
    exported, warnings = logs.export(selection)
    assert not warnings
    assert [json.loads(line)['data_quality'] for line in exported.splitlines()] == [row['data_quality'] for row in rows]


def test_historical_log_window_prioritizes_nearby_segments_at_scan_limit(tmp_path, monkeypatch):
    from app.services import operations_log_query

    logger = logging.getLogger('app.test_operations')
    with configured_logging(tmp_path, 'worker', 'previous'):
        event(logger, logging.WARNING, '上一时段的事件')
    old, = operations_logging.log_paths(tmp_path / 'operations')
    when = datetime.now(UTC) - timedelta(days=3)
    rows = [json.loads(line) for line in old.read_text().splitlines()]
    old.write_text(''.join(json.dumps({**row, 'timestamp': when.isoformat()}) + '\n' for row in rows))
    os.utime(old, (when.timestamp(), when.timestamp()))
    with configured_logging(tmp_path, 'worker', 'current'):
        event(logger, logging.WARNING, '当前时段的事件')
    segments = operations_logging.log_paths(tmp_path / 'operations')
    monkeypatch.setattr(operations_log_query, 'MAX_SCAN_BYTES', max(path.stat().st_size for path in segments))

    query = OperationsLogQuery(tmp_path, 14)
    filters = LogFilters(start=when - timedelta(hours=1), end=when + timedelta(hours=1), level='WARNING')
    historical = query.query(filters)
    assert [row['message'] for row in historical['items']] == ['上一时段的事件']
    assert 'scan_limit' in historical['coverage_warnings']

    recent = query.query(LogFilters(level='WARNING'))
    assert [row['message'] for row in recent['items']] == ['当前时段的事件']


def test_log_scan_keeps_readable_segments_when_another_rotates(tmp_path, monkeypatch):
    from app.services import operations_log_query

    with configured_logging(tmp_path, 'worker', 'fixture'):
        event(logging.getLogger('app.test_operations'), logging.WARNING, '可读取的日志')
    readable, = operations_logging.log_paths(tmp_path / 'operations')
    removed = readable.with_suffix('.jsonl.1')
    monkeypatch.setattr(operations_log_query, 'log_paths', lambda _: iter((readable, removed)))

    result = OperationsLogQuery(tmp_path, 14).query(LogFilters(role='worker', instance_id='fixture'))
    assert [item['message'] for item in result['items'] if item['level'] == 'WARNING'] == ['可读取的日志']
    assert 'rotation' in result['coverage_warnings']
    assert 'unavailable' not in result['coverage_warnings']


def test_writer_expires_quiet_active_segment_before_appending(tmp_path):
    logger = logging.getLogger('app.test_operations')
    with configured_logging(tmp_path, 'scheduler', 'scheduler'):
        event(logger, logging.WARNING, '过期的当前日志段')
    active, = operations_logging.log_paths(tmp_path / 'operations')
    stale = (datetime.now(UTC) - timedelta(days=15)).timestamp()
    os.utime(active, (stale, stale))

    with configured_logging(tmp_path, 'scheduler', 'scheduler'):
        event(logger, logging.WARNING, '重启后的日志')

    messages = [json.loads(line)['message'] for line in active.read_text().splitlines()]
    assert '过期的当前日志段' not in messages
    assert '重启后的日志' in messages


def test_new_log_instance_reclaims_only_expired_inactive_history(tmp_path, monkeypatch):
    monkeypatch.setattr(operations_logging, 'MAX_INSTANCES', 1)
    with configured_logging(tmp_path, 'worker', 'previous'):
        event(logging.getLogger('app.test_operations'), logging.WARNING, '上一个实例')
    root = tmp_path / 'operations'
    old, = operations_logging.log_paths(root)
    with configured_logging(tmp_path, 'worker', 'current'):
        assert tuple(operations_logging.log_paths(root)) == (old,)

    stale = (datetime.now(UTC) - timedelta(days=15)).timestamp()
    os.utime(old, (stale, stale))
    with operations_logging.FileLock(old.with_suffix('.lock')):
        with configured_logging(tmp_path, 'worker', 'current'):
            assert tuple(operations_logging.log_paths(root)) == (old,)

    with configured_logging(tmp_path, 'worker', 'current'):
        event(logging.getLogger('app.test_operations'), logging.WARNING, '新实例已写入')
    assert not old.exists()
    rows = OperationsLogQuery(tmp_path, 14).query(LogFilters(role='worker', instance_id='current'))['items']
    assert any(item['message'] == '新实例已写入' for item in rows)


def test_process_exit_keeps_safe_stack_without_reprinting_exception(tmp_path, capsys):
    secret = 'private-startup-password'
    with pytest.raises(RuntimeError, match='PROCESS_EXIT') as caught:
        with configured_logging(tmp_path, 'web', 'web'):
            raise ValueError(secret)

    captured = capsys.readouterr()
    assert secret not in captured.out + captured.err + ''.join(format_exception(caught.value))
    event, = OperationsLogQuery(tmp_path, 14).query(LogFilters(error_code='PROCESS_EXIT'))['items']
    assert event['exception']['type'] == 'ValueError'
    assert event['exception']['frames']


def test_log_query_keeps_failure_and_recovery_for_the_same_resource(tmp_path):
    logger = logging.getLogger('app.test_operations')
    with configured_logging(tmp_path, 'worker', 'fixture'):
        for source, resource, code in [
            ('target-a', 'fault-a', 'DATABASE_TCP_DOWN'),
            ('target-a', 'fault-a', 'DATABASE_TCP_RECOVERED'),
            ('target-a', 'fault-previous', 'DATABASE_TCP_DOWN'),
            ('target-b', 'fault-b', 'DATABASE_TCP_DOWN'),
        ]:
            event(logger, logging.WARNING, 'TCP 检测状态变化', error_code=code,
                  source_id=source, resource_id=resource, related_kind='database-monitor')
    logs = OperationsLogQuery(tmp_path, 14)
    web = embedded_app()
    web.state.runtime.operations = SimpleNamespace(logs=logs)
    web.dependency_overrides[require_user] = lambda: {'role': 'admin', 'user_id': 'admin-1'}
    web.include_router(router)

    @web.middleware('http')
    async def context(request: Request, call_next):
        request.state.request_id = 'test'
        return await call_next(request)

    with TestClient(web) as client:
        response = client.get('/api/v1/operations/logs', params={'resource_id': 'fault-a', 'source_id': 'target-a'})
        assert response.status_code == 200, response.text
        assert [item['error_code'] for item in response.json()['data']['items']] == [
            'DATABASE_TCP_RECOVERED', 'DATABASE_TCP_DOWN',
        ]
        response = client.get('/api/v1/operations/logs', params={'resource_id': 'fault-a', 'source_id': 'target-b'})
        assert response.status_code == 200 and not response.json()['data']['items']


def test_incident_pagination_preserves_three_thousand_record_range_at_ten_rows():
    requests = []

    def list_incidents(filters):
        requests.append((filters.page, filters.page_size))
        return dict(items=[], page=filters.page, page_size=filters.page_size, total=2993)

    web = embedded_app()
    web.state.runtime.operations = SimpleNamespace(repository=SimpleNamespace(list=list_incidents))
    web.dependency_overrides[require_user] = lambda: {'role': 'admin', 'user_id': 'admin'}
    web.include_router(router)

    @web.middleware('http')
    async def context(request: Request, call_next):
        request.state.request_id = 'test'
        return await call_next(request)

    with TestClient(web) as client:
        endpoint = '/api/v1/operations/incidents'
        assert client.get(endpoint, params={'page': 300, 'page_size': 10}).json()['data']['page'] == 300
        assert client.get(endpoint, params={'page': 301, 'page_size': 10}).status_code == 422
        assert client.get(endpoint, params={'page': 101, 'page_size': 30}).status_code == 422
        assert requests == [(300, 10)]


def test_admin_exports_filtered_logs_across_pages(tmp_path, monkeypatch):
    from app.services import operations_log_query

    logger = logging.getLogger('app.test_operations')
    with configured_logging(tmp_path, 'worker', 'consumer-a'):
        for index in range(3):
            try:
                raise RuntimeError('private-connection-password')
            except RuntimeError:
                event(logger, logging.ERROR, f'测试报表 {index}', report_id='report-a',
                      error_code='REPORT_TEST', exc_info=True)
        event(logger, logging.ERROR, '其他报表', report_id='report-b', error_code='REPORT_TEST')
    segment, = operations_logging.log_paths(tmp_path / 'operations')
    rows = [json.loads(raw) for raw in segment.read_text().splitlines()]
    for row in rows:
        row['unsupported_field'] = 'private-extra-field'
    segment.write_text(''.join(json.dumps(row) + '\n' for row in rows))

    web = embedded_app()
    web.state.runtime.operations = SimpleNamespace(logs=OperationsLogQuery(tmp_path, 14))
    web.include_router(router)

    @web.middleware('http')
    async def context(request: Request, call_next):
        request.state.request_id = 'test'
        return await call_next(request)

    query = dict(role='worker', instance_id='consumer-a', report_id='report-a', keyword='测试')
    endpoint = '/api/v1/operations/logs/export'
    with TestClient(web) as client:
        web.dependency_overrides[require_user] = lambda: {'role': 'worker', 'user_id': 'worker'}
        assert client.get(endpoint, params=query).status_code == 403
        web.dependency_overrides[require_user] = lambda: {'role': 'admin', 'user_id': 'admin'}
        page = client.get('/api/v1/operations/logs', params={**query, 'limit': 1}).json()['data']
        assert page['next_cursor']

        response = client.get(endpoint, params=query)
        assert response.status_code == 200
        assert response.headers['Content-Type'] == 'application/x-ndjson'
        assert response.headers['Content-Disposition'].endswith('.jsonl"')
        assert response.headers['Cache-Control'] == 'no-store'
        assert 'private-' not in response.text
        exported = [json.loads(raw) for raw in response.text.splitlines()]
        assert [row['message'] for row in exported] == ['测试报表 2', '测试报表 1', '测试报表 0']
        assert exported[0]['id'] == page['items'][0]['id']
        assert all(row['exception']['type'] == 'RuntimeError' and row['exception']['frames'] for row in exported)

        monkeypatch.setattr(operations_log_query, 'MAX_EXPORT_RECORDS', 2)
        limited = client.get(endpoint, params=query)
        assert limited.text.splitlines() == response.text.splitlines()[:2]
        assert 'record_limit' in limited.headers['X-Log-Coverage-Warnings'].split(',')
        assert client.get(endpoint, params={**query, 'report_id': 'missing'}).content == b''
        assert client.get(endpoint, params={**query, 'keyword': '测'}).status_code == 422


def test_snapshot_task_logs_identify_the_report_and_device(tmp_path):
    from app.worker import WorkerProcess

    completed = []
    worker = SimpleNamespace(_handle=lambda task: None, _lock=Lock(), _completed=completed)
    task = dict(kind='report_snapshot_refresh', device_id='cut-a', task_id='snapshot-a',
                payload={'report_type': 'energy:tasks', 'query': {}})
    with configured_logging(tmp_path, 'worker', 'consumer-a'):
        WorkerProcess._execute(worker, task)
    rows = OperationsLogQuery(tmp_path, 14).query(LogFilters(task_id='snapshot-a'))['items']
    assert {row['message'] for row in rows} == {'后台任务开始', '后台任务完成'}
    assert {(row['line_id'], row['stage']) for row in rows} == {('cut-a', 'energy:tasks')}
    assert completed == [(task, None)]


@pytest.mark.parametrize(('model_enabled', 'expected'), [(False, 'AnalysisDisabled'), (True, 'LlmRequestTimeout')])
def test_analysis_logs_keep_a_safe_cause_after_http_mapping(tmp_path, model_enabled, expected):
    from fastapi import HTTPException
    from app.config import Settings
    from app.services.analysis_execution import analysis_operation
    from app.services.llm_service import LlmRequestTimeout
    from app.services.report_analysis_service import AnalysisUnavailableError

    settings = Settings(project_root=tmp_path, device_id='cut-a', llm_enabled=model_enabled)
    cause = AnalysisUnavailableError('private upstream detail')
    if model_enabled:
        cause.__cause__ = LlmRequestTimeout('private network detail')
    with configured_logging(tmp_path, 'web', 'web') as logs:
        with pytest.raises(HTTPException), analysis_operation(settings, 'workshop', '2026-11-20'):
            raise HTTPException(status_code=503) from cause
        incident = logs.incidents.get_nowait()
    assert (incident['line_id'], incident['stage'], incident['error_type']) == ('cut-a', 'workshop', expected)
    assert 'private' not in json.dumps(incident)


def test_web_repeated_transient_failures_become_incidents(tmp_path, monkeypatch):
    from app.main import request_id_middleware
    from app.services import operations_service

    web = embedded_app()
    operations = operations_service.OperationsService(
        SimpleNamespace(logs_dir=tmp_path, operations_log_retention_days=14), None, None, None, None, None, 'web',
    )
    web.state.runtime.operations = operations
    web.middleware('http')(request_id_middleware)
    status = 503
    now = 100.0
    monkeypatch.setattr(operations_service, 'monotonic', lambda: now)

    @web.get('/api/v1/probe')
    def probe():
        return Response(status_code=status)

    @web.get('/api/v1/other')
    def other():
        return Response(status_code=503)

    with configured_logging(tmp_path, 'web', 'web') as logs, TestClient(web) as client:
        assert client.get('/api/v1/probe').status_code == 503
        assert client.get('/api/v1/probe').status_code == 503
        assert client.get('/api/v1/other').status_code == 503
        assert logs.incidents.empty()
        status = 200
        assert client.get('/api/v1/probe').status_code == 200
        status = 504
        assert client.get('/api/v1/probe').status_code == 504
        assert client.get('/api/v1/probe').status_code == 504
        now += 61
        assert client.get('/api/v1/probe').status_code == 504
        assert client.get('/api/v1/probe').status_code == 504
        assert logs.incidents.empty()
        response = client.get('/api/v1/probe')
        assert response.status_code == 504
        incident = logs.incidents.get_nowait()
        assert incident['error_code'] == 'HTTP_TIMEOUT'
        assert incident['resource_id'] == 'probe'
        assert incident['stage'] == 'probe'
        assert incident['request_id'] == response.headers['X-Request-ID']


def test_workshop_conflicts_stay_expected_and_server_failures_are_correlated(tmp_path, monkeypatch):
    from psycopg import OperationalError
    from app import main
    from app.modules.cutting import workshop_routes as workshop
    from app.modules.cutting.workshop_errors import ConfirmationConflictError

    web = embedded_app()
    web.middleware('http')(main.request_id_middleware)
    web.add_exception_handler(Exception, main.unexpected_error_handler)
    web.include_router(workshop.router)
    web.dependency_overrides[require_user] = lambda: {'role': 'worker', 'user_id': 'worker'}
    web.dependency_overrides[workshop.device_state] = lambda: None
    fault = ConfirmationConflictError('任务已由其他工人确认')
    secret = 'private-database-password'
    config = SimpleNamespace(id='cut-a', source_key='source-a')

    def confirm(**_):
        raise fault

    def state(request):
        if request.headers.get('X-Production-Line'):
            raise OperationalError(secret)
        return make_database_services(lines={'cut-a': ProductionLineRuntime(config, make_device_runtime())})

    monkeypatch.setattr(main, 'runtime_state', state)
    monkeypatch.setattr(workshop, '_service', lambda request: SimpleNamespace(confirm_task=confirm))
    endpoint = '/api/v1/workshop/tasks/operation-fixture/confirm?device_id=cut-a'
    body = {'report_date': '2026-11-17'}
    failures = []
    with configured_logging(tmp_path, 'web', 'web') as logs, TestClient(web, raise_server_exceptions=False) as client:
        assert client.post(endpoint, json=body).status_code == 409
        assert logs.incidents.empty()
        fault = OperationalError(secret)
        for headers in ({}, {'X-Production-Line': 'cut-a'}):
            response = client.post(endpoint, json=body, headers=headers)
            assert response.status_code == 500 and secret not in response.text
            request_id = response.headers['X-Request-ID']
            assert response.json()['request_id'] == request_id and request_id.startswith('req_')
            incident = logs.incidents.get_nowait()
            assert incident['request_id'] == request_id and incident['error_code'] == 'INTERNAL_ERROR'
            assert secret not in json.dumps(incident)
            failures.append(incident)
        assert logs.incidents.empty()

    assert (failures[0]['resource_id'], failures[0]['line_id'], failures[0]['source_id']) == ('confirm_task', 'cut-a', 'source-a')
    assert failures[0]['stage'] == 'workshop'
    assert 'resource_id' not in failures[1]
    for incident in failures:
        row, = OperationsLogQuery(tmp_path, 14).query(LogFilters(request_id=incident['request_id']))['items']
        assert row['id'] == incident['id']


def test_incident_minimum_error_level_filters_counts_and_pages(postgres_state):
    database, _, _ = postgres_state
    repository = OperationsRepository(database)
    now = datetime.now(UTC) - timedelta(minutes=1)
    repository.record([dict(timestamp=now, level=level, error_code='FILTER_TEST', component='fixture',
                            role='worker', resource_id=level, message='测试异常')
                       for level in ('WARNING', 'ERROR', 'CRITICAL')])
    web = embedded_app()
    web.state.runtime.operations = SimpleNamespace(repository=repository)
    web.include_router(router)
    @web.middleware('http')
    async def request_id(request: Request, call_next):
        request.state.request_id = 'web_filter_test'
        return await call_next(request)
    web.dependency_overrides[require_user] = lambda: {'role': 'admin', 'user_id': 'admin'}
    with TestClient(web) as client:
        url = '/api/v1/operations/incidents'
        pages = [client.get(url, params=dict(min_level='ERROR', page_size=1, page=page)) for page in (1, 2)]
        assert all(response.status_code == 200 for response in pages)
        results = [response.json()['data'] for response in pages]
        assert all(result['total'] == 2 for result in results)
        assert {result['items'][0]['level'] for result in results} == {'ERROR', 'CRITICAL'}
        assert client.get(url).json()['data']['total'] == 3
        assert client.get(url, params={'level': 'WARNING'}).json()['data']['total'] == 1
        assert client.get(url, params={'min_level': 'WARNING'}).status_code == 422


def test_incident_concurrency_confirmation_recovery_and_admin_routes(postgres_state):
    database, cfg, _ = postgres_state
    repository = OperationsRepository(database)
    now = datetime.now(UTC) - timedelta(minutes=4)
    item = dict(timestamp=now, level='ERROR', error_code='TASK_TEST', component='fixture', role='worker',
                resource_id='task-1', task_id='task-1', line_id='cut-a', message='后台任务执行失败')
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda i: repository.record([{**item, 'timestamp': now + timedelta(seconds=i)}]), range(8)))
    existing, = repository.list(IncidentFilters())['items']
    assert existing['occurrences'] == 8 and existing['context']['task_id'] == 'task-1'
    same = {**item, 'timestamp': now + timedelta(seconds=10)}
    repository.record([{**same, 'line_id': 'cut-b'}])
    assert repository.list(IncidentFilters())['total'] == 2

    service = SimpleNamespace(repository=repository, logs=OperationsLogQuery(cfg.logs_dir, 14))
    web = embedded_app()
    web.state.runtime.operations = service
    web.include_router(router)
    @web.middleware('http')
    async def context(request: Request, call_next):
        request.state.request_id = 'web_fixture'
        return await call_next(request)

    with TestClient(web) as client:
        notice = '/api/v1/operations/pending-notice'
        web.dependency_overrides[require_user] = lambda: {'role': 'worker', 'user_id': 'worker'}
        assert client.get('/api/v1/operations/logs').status_code == 403
        assert client.get('/api/v1/operations/incidents').status_code == 403
        assert client.get(notice).status_code == 403
        endpoint = f"/api/v1/operations/incidents/{existing['incident_id']}"
        assert client.post(endpoint + '/acknowledge', json={}).status_code == 403
        web.dependency_overrides[require_user] = lambda: {'role': 'admin', 'user_id': 'admin-1'}
        assert client.get('/api/v1/operations/logs').json()['data']['coverage_warnings']
        assert client.get('/api/v1/operations/logs?cursor=not-a-cursor').status_code == 422
        other, = client.get('/api/v1/operations/incidents?line_id=cut-b').json()['data']['items']
        pending = client.get(notice)
        assert pending.status_code == 200 and pending.headers['Cache-Control'] == 'no-store'
        assert pending.json()['data'] is True
        response = client.post(endpoint + '/acknowledge', json={'note': '正在排查'})
        assert response.status_code == 200, response.text
        assert response.json()['data']['status'] == 'acknowledged'
        assert response.json()['data']['recovered_at'] is None
        assert response.json()['data']['acknowledged_by'] == 'admin-1'
        assert client.post(endpoint + '/acknowledge', json={}).status_code == 409
        assert client.get(notice).json()['data'] is True
        assert client.post(f"/api/v1/operations/incidents/{other['incident_id']}/acknowledge", json={}).status_code == 200
        assert client.get(notice).json()['data'] is False

    after = now + timedelta(seconds=60)
    repository.observe([({**item, 'timestamp': after}, True, '完成真实任务')], [], [], after)
    assert repository.get(existing['incident_id'])['status'] == 'acknowledged'
    after += timedelta(seconds=30)
    repository.observe([({**item, 'timestamp': after}, True, '完成真实任务')], [], [], after)
    assert repository.get(existing['incident_id'])['status'] == 'recovered'
    repository.record([{**item, 'timestamp': after + timedelta(seconds=1)}])
    current, = repository.list(IncidentFilters(line_id='cut-a', status='open'))['items']
    assert current['incident_id'] != existing['incident_id'] and current['occurrences'] == 1
    repository.cleanup(datetime.now(UTC), 10)
    assert repository.get(existing['incident_id']) is None
    assert repository.get(current['incident_id']) is not None


def test_discovery_reuses_monitor_evidence_and_no_production_probes(postgres_state, monkeypatch):
    database, cfg, _ = postgres_state
    runtime = build_runtime(cfg, role='web')
    monitor = DatabaseMonitorRepository(database)
    try:
        identifier = monitor.save(DatabaseMonitorInput(name='Fixture', ip='192.0.2.4', port=1433))
        instant = datetime.now(UTC)
        now = instant.timestamp() - 640
        for offset in (0, 60, 120):
            row, = monitor.list()
            monitor.record(row, '连接超时', now + offset)
        monkeypatch.setattr(runtime.database_monitor, 'probe', lambda *_: (_ for _ in ()).throw(AssertionError('unexpected TCP probe')))
        service = runtime.operations
        service.discover()
        row, = service.repository.list(IncidentFilters(error_code='DATABASE_TCP_DOWN'))['items']
        assert row['source_id'] == identifier and row['status'] == 'open'
        assert row['occurrences'] == 3 and row['first_seen_at'] < row['last_seen_at']
        target, = monitor.list()
        monitor.record(target, None, now + 180)
        target, = monitor.list()
        monitor.record(target, '端口拒绝连接', now + 240)
        for offset in (300, 360):
            target, = monitor.list()
            monitor.record(target, None, now + offset)
        pending, recoveries = service.repository.business_status((instant - timedelta(days=1)).isoformat(), BUSINESS_ERROR_CODES)
        monitor_pending, = [item for item in pending if item['kind'] == 'database-monitor']
        assert monitor_pending['source_count'] == 4
        assert not recoveries
        # Move the already verified cycle marker back to allow the next sample.
        with database.connect() as connection:
            connection.execute("UPDATE operations_samples SET bucket=bucket-1 WHERE sample_key='cycle'")
        service.discover()
        updated = service.repository.get(row['incident_id'])
        assert updated['status'] == 'recovered' and 'TCP' in updated['recovery_evidence']
        assert updated['occurrences'] == 4
        assert updated['last_seen_at'].timestamp() == now + 240
        assert updated['recovered_at'].timestamp() == now + 360
        assert '端口拒绝连接' in updated['message']
        for offset in (420, 480, 540):
            target, = monitor.list()
            monitor.record(target, '连接超时', now + offset)
        with database.connect() as connection:
            connection.execute("UPDATE operations_samples SET bucket=bucket-1 WHERE sample_key='cycle'")
        service.discover()
        opened, = service.repository.list(IncidentFilters(error_code='DATABASE_TCP_DOWN', status='open'))['items']
        assert opened['resource_id'] != row['resource_id'] and opened['occurrences'] == 3
        assert service.overview()['runtime']['postgres'] == 'healthy'
    finally:
        runtime.database_runtime.close()
        runtime.database.close()


def test_dispatch_incident_recovers_only_when_the_original_dispatch_is_sent(postgres_state, tmp_path, monkeypatch):
    database, cfg, _ = postgres_state
    client = RecordingMagicClient(fail_assign=True)
    dispatch, _, admin = push_service(database, tmp_path, client)
    runtime = build_runtime(cfg, role='web')
    operations = runtime.operations
    try:
        original, other = [dispatch.create_dispatch(document_id=file['document_id'], machine_ip='10.1.133.197',
            material='A0', thickness=12, quantity=1, user=admin)['dispatch_id'] for file in dispatch.pdm.files[:2]]
        with configured_logging(cfg.logs_dir, 'web', 'web') as logs:
            with pytest.raises(MagicBoxError, match='指派失败'):
                dispatch.execute_dispatch(original, user=admin)
            failure = logs.incidents.get_nowait()
            assert failure['resource_id'] == original and failure['error_code'] == 'DISPATCH_ASSIGN'
            when = datetime.fromisoformat(failure['timestamp'])
            operations.repository.record([{**failure, 'timestamp': when}])
            incident, = operations.repository.list(IncidentFilters(error_code='DISPATCH_ASSIGN'))['items']
            assert incident['status'] == 'open'

            client.fail_assign = False
            assert dispatch.execute_dispatch(other, user=admin)['status'] == 'sent'
            _, recoveries = operations.repository.business_status((when - timedelta(days=1)).isoformat(), BUSINESS_ERROR_CODES)
            assert not recoveries
            monkeypatch.setattr(dispatch, '_now', lambda: when.isoformat(timespec='seconds'))
            assert dispatch.execute_dispatch(original, user=admin)['status'] == 'sent'

        operations.discover()
        recovered = operations.repository.get(incident['incident_id'])
        assert recovered['status'] == 'recovered' and recovered['recovered_at'] == when
        assert '指派成功' in recovered['recovery_evidence']
        assert operations.repository.related('dispatch', original)['status'] == 'sent'
        rows = operations.logs.query(LogFilters(resource_id=original))['items']
        assert {row['error_code'] for row in rows} == {'DISPATCH_ASSIGN', 'DISPATCH_SENT'}
    finally:
        runtime.database_runtime.close()
        runtime.database.close()


def test_business_discovery_drains_each_source_without_duplicate_events(postgres_state):
    database, cfg, _ = postgres_state
    now = datetime.now(UTC)
    old = now - timedelta(days=2)
    recent = (now - timedelta(minutes=1)).isoformat()
    with database.connect() as connection:
        connection.execute("""
            INSERT INTO report_jobs (report_id,device_id,status,stage_message,report_date,created_at,finished_at,request_id)
            SELECT 'report-burst-' || n,'cut-a','failed','生成失败','2026-11-17',%s,%s,'req_0123456789ab'
            FROM generate_series(1,51) AS n
        """, (old.isoformat(), recent))
        connection.execute("""
            INSERT INTO background_tasks (task_id,kind,device_id,payload,dedup_key,status,created_at,updated_at,next_attempt)
            SELECT 'task-burst-' || n,'report_snapshot_refresh','cut-a','{}','burst-' || n,'blocked',%s,%s,%s
            FROM generate_series(1,51) AS n
        """, (old.timestamp(), old.timestamp(), old.timestamp()))
        connection.execute("""
            INSERT INTO scheduled_report_runs
                (run_id,device_id,schedule_name,report_type,report_date,scheduled_for,trigger_type,report_id,status,
                 recipients_json,subject_template,chart_type,attach_report,max_attempts,created_by_id,created_by_name,created_at,updated_at)
            SELECT 'run-burst-' || n,'cut-a','测试计划','workshop','2026-11-17',%s,'manual',
                CASE WHEN n>51 THEN 'report-burst-' || (n-51) END,CASE WHEN n>51 THEN 'unknown' ELSE 'failed' END,
                '[]','','line',0,3,'tester','Tester',%s,%s
            FROM generate_series(1,102) AS n
        """, (recent, recent, recent))
        connection.execute("""
            INSERT INTO email_deliveries (delivery_id,run_id,report_id,recipients_json,subject,status,max_attempts,
                  next_attempt_at,created_at,updated_at,message_id)
            SELECT 'delivery-burst-' || n,'run-burst-' || (n+51),'report-burst-' || n,'[]','','unknown',3,
                %s,%s,%s,'msg-current-' || n
            FROM generate_series(1,51) AS n
        """, (old.isoformat(), old.isoformat(), old.isoformat()))
    runtime = build_runtime(cfg, role='web')
    try:
        operations = runtime.operations
        sources = {
            'REPORT_GENERATION_FAILED': ('reports', 'report-burst-'),
            'TASK_BLOCKED': ('background', 'task-burst-'),
            'AUTOMATION_RUN_FAILED': ('automation', 'run-burst-'),
            'EMAIL_RESULT_UNKNOWN': ('email', 'delivery-burst-'),
        }
        operations.discover()
        # Blocked tasks and unknown deliveries remain actionable beyond the sampling lookback.
        for code in sources:
            assert operations.repository.list(IncidentFilters(error_code=code, status='open'))['total'] == 50
        with database.connect() as connection:
            connection.execute("UPDATE operations_samples SET bucket=bucket-1 WHERE sample_key='cycle'")
        operations.discover()
        for code, (component, prefix) in sources.items():
            incidents = operations.repository.list(IncidentFilters(error_code=code, status='open', page_size=100))
            assert incidents['total'] == 51
            assert {item['occurrences'] for item in incidents['items']} == {1}
            assert {item['resource_id'] for item in incidents['items']} == {f'{prefix}{n}' for n in range(1, 52)}
            assert {item['context']['related_kind'] for item in incidents['items']} == {component}
            assert operations.repository.related(component, f'{prefix}51')['id'] == f'{prefix}51'
        assert {'group': 'reports', 'status': 'failed', 'count': 51} in operations.status()['work']

        with database.connect() as connection:
            connection.execute("""
                UPDATE email_deliveries SET status='failed',updated_at=%s WHERE delivery_id IN ('delivery-burst-2','delivery-burst-3')
            """, (recent,))
            connection.execute("""
                INSERT INTO email_delivery_resolutions
                SELECT 'resolution-' || n,'delivery-burst-' || n,'run-burst-' || (n+51),
                    CASE WHEN n=3 THEN 'msg-old-3' ELSE 'msg-current-' || n END,
                    'tester','Tester','verified','{}','{}','failed',%s
                FROM generate_series(1,3) AS n
            """, (recent,))
            connection.execute("UPDATE operations_samples SET bucket=bucket-1 WHERE sample_key='cycle'")
        operations.discover()
        resolved = operations.repository.list(IncidentFilters(error_code='EMAIL_RESULT_UNKNOWN', status='recovered'))['items']
        assert [item['resource_id'] for item in resolved] == ['delivery-burst-2']
        failed = operations.repository.list(IncidentFilters(error_code='EMAIL_DELIVERY_FAILED', status='open'))['items']
        assert {item['resource_id'] for item in failed} == {'delivery-burst-2', 'delivery-burst-3'}
    finally:
        runtime.database_runtime.close()
        runtime.database.close()


def test_consumer_shortfall_and_lease_recovery_use_distinct_instance_evidence(postgres_state, monkeypatch):
    from app.services import operations_service, worker_status

    _, cfg, _ = postgres_state
    runtime = build_runtime(cfg, role='web')
    now = datetime.now(UTC)
    clock = [now]

    class TestClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]

    monkeypatch.setattr(operations_service, 'datetime', TestClock)
    monkeypatch.setattr(worker_status, 'monotonic', lambda: clock[0].timestamp())
    service = runtime.operations
    repo = service.repository

    def sample():
        service.discover()
        clock[0] += timedelta(seconds=30)

    def incidents(code):
        return repo.list(IncidentFilters(error_code=code, end=clock[0]))['items']

    try:
        worker, = [row for row in service.status()['roles'] if row['role'] == 'worker']
        assert worker['state'] == 'abnormal' and worker['detail'] == 'capacity_shortfall'
        repo.record([dict(timestamp=now, error_code='EXECUTION_LEASE_LOST', component='leases', role='worker',
                          instance_id='consumer-a', resource_id='consumer-a', level='ERROR', message='执行租约失效')])
        sample()
        assert not incidents('CONSUMER_CAPACITY')
        sample()
        capacity, = incidents('CONSUMER_CAPACITY')
        lease, = incidents('EXECUTION_LEASE_LOST')
        assert capacity['status'] == lease['status'] == 'open'

        owner = runtime.coordination.acquire('worker:consumer-b', ttl=30)
        assert runtime.coordination.renew('worker:consumer-b', owner, ttl=30, status={'report_alive': True})
        sample()
        assert repo.get(capacity['incident_id'])['status'] == 'open'
        sample()
        assert repo.get(capacity['incident_id'])['status'] == 'recovered'
        assert repo.get(lease['incident_id'])['status'] == 'open'

        owner = runtime.coordination.acquire('worker:consumer-a', ttl=30)
        assert runtime.coordination.renew('worker:consumer-a', owner, ttl=30, status={'report_alive': True})
        sample()
        assert repo.get(lease['incident_id'])['status'] == 'open'
        sample()
        assert repo.get(lease['incident_id'])['status'] == 'recovered'
    finally:
        runtime.database_runtime.close()
        runtime.database.close()


def test_consumer_health_sampling_ignores_historical_leases(postgres_state, monkeypatch):
    from app.services import operations_service, worker_status

    database, cfg, _ = postgres_state
    runtime = build_runtime(cfg, role='web')
    clock = datetime.now(UTC)

    class TestClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock

    monkeypatch.setattr(operations_service, 'datetime', TestClock)
    monkeypatch.setattr(worker_status, 'monotonic', lambda: clock.timestamp())
    service = runtime.operations
    repo = service.repository

    def sample():
        nonlocal clock
        service.discover()
        clock += timedelta(seconds=30)

    def health_incidents():
        rows = repo.list(IncidentFilters(error_code='PROCESS_UNHEALTHY', end=clock))['items']
        return {row['instance_id']: row for row in rows if row['role'] == 'worker'}

    try:
        for instance in ('historical', 'existing-incident', 'recent', 'stopped', 'healthy'):
            lease = f'worker:{instance}'
            owner = runtime.coordination.acquire(lease, ttl=30)
            assert runtime.coordination.renew(lease, owner, ttl=30, status={'report_alive': True})
            if instance == 'stopped':
                runtime.coordination.release(lease, owner)
        with database.connect() as connection:
            connection.execute("""UPDATE process_leases SET expires_at=EXTRACT(EPOCH FROM clock_timestamp())-1
                WHERE name IN ('worker:historical','worker:existing-incident','worker:recent')""")
            connection.execute("""UPDATE process_leases SET heartbeat_at=heartbeat_at-3600,expires_at=expires_at-3600
                WHERE name IN ('worker:historical','worker:existing-incident')""")
        repo.record([dict(timestamp=clock, error_code='PROCESS_UNHEALTHY', component='health', role='worker',
                          instance_id='existing-incident', resource_id='existing-incident', level='ERROR', message='失联')])

        sample()
        sample()
        incidents = health_incidents()
        assert set(incidents) == {'existing-incident', 'recent'}
        assert incidents['existing-incident']['occurrences'] == 1
        assert incidents['existing-incident']['status'] == incidents['recent']['status'] == 'open'
        assert not repo.list(IncidentFilters(error_code='CONSUMER_CAPACITY', end=clock))['total']

        with database.connect() as connection:
            connection.execute("""UPDATE process_leases SET heartbeat_at=heartbeat_at-3600,expires_at=expires_at-3600
                WHERE name='worker:recent'""")
        sample()
        assert health_incidents()['recent']['occurrences'] == 1

        owner = runtime.coordination.acquire('worker:recent', ttl=30)
        assert runtime.coordination.renew('worker:recent', owner, ttl=30, status={'report_alive': True})
        sample()
        assert health_incidents()['recent']['status'] == 'open'
        sample()
        assert health_incidents()['recent']['status'] == 'recovered'
    finally:
        runtime.database_runtime.close()
        runtime.database.close()


def test_event_persistence_failure_leaves_local_logs_without_recursive_events(tmp_path):
    from app.services.operations_service import OperationsCollector

    calls = []
    def fail(items):
        calls.append(len(items))
        raise ConnectionError('private-postgres-dsn')
    service = SimpleNamespace(role='worker', repository=SimpleNamespace(record=fail))
    logger = logging.getLogger('app.test_operations')
    with configured_logging(tmp_path, 'worker', 'fixture') as logs:
        event(logger, logging.ERROR, '记录测试故障', error_code='TEST_OUTAGE', incident=True)
        with OperationsCollector(service, logs):
            pass
        assert len(calls) <= 2 and calls
        assert logs.incidents.empty()
    rows = OperationsLogQuery(tmp_path, 14).query(LogFilters())['items']
    assert {row.get('error_code') for row in rows} >= {'TEST_OUTAGE', 'OPERATIONS_WRITE_FAILED'}
    assert 'private-postgres-dsn' not in json.dumps(rows)


def test_configuration_events_require_consecutive_reports_from_the_same_role(postgres_state, monkeypatch):
    from app.services import operations_service, worker_status

    _, cfg, _ = postgres_state
    web = build_runtime(cfg, role='web')
    scheduler = build_runtime(cfg, role='scheduler')
    clock = [datetime.now(UTC)]

    class TestClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]

    monkeypatch.setattr(operations_service, 'datetime', TestClock)
    monkeypatch.setattr(worker_status, 'monotonic', lambda: clock[0].timestamp())
    monkeypatch.setattr(web.configuration_diagnostics, 'load_state', lambda: 'healthy')
    collector = web.operations.collect(None)
    collector._heartbeat()
    leases = web.coordination
    worker = leases.acquire('worker:consumer-a', ttl=30)
    automation = leases.acquire('automation', ttl=30)
    pending_snapshot = {'items': {}, 'sql_capacity_pending': True}

    def heartbeat(error_snapshot):
        assert leases.renew('worker:consumer-a', worker, ttl=30,
                            status={'report_alive': True, 'configuration': error_snapshot})
        assert leases.renew('automation', automation, ttl=30, status={
            'automation_alive': True, 'email_alive': True, 'configuration': pending_snapshot,
        })

    def sample(service, snapshot):
        heartbeat(snapshot)
        service.discover()
        clock[0] += timedelta(seconds=30)

    def incidents():
        return web.operations.repository.list(IncidentFilters(error_code='CONFIGURATION_LOAD_FAILED', end=clock[0]))['items']

    try:
        failed = {'items': {'example': {'digest': 'private-fingerprint'}}, 'database_error': 'private-configuration'}
        sample(web.operations, failed)
        assert not incidents()
        sample(scheduler.operations, failed)
        incident, = incidents()
        assert (incident['role'], incident['instance_id'], incident['status']) == ('worker', 'consumer-a', 'open')
        states = web.operations.status()['roles']
        assert next(row for row in states if row['role'] == 'web')['configuration_state'] == 'healthy'
        assert next(row for row in states if row['role'] == 'automation')['configuration_state'] == 'healthy'
        assert next(row for row in states if row['instance_id'] == 'consumer-a')['configuration_state'] == 'abnormal'
        assert 'private-fingerprint' not in str(states) and 'private-configuration' not in str(incident)

        sample(scheduler.operations, {'items': {}})
        sample(web.operations, {'error': '配置快照暂不可用'})
        sample(scheduler.operations, {'items': {}})
        assert incidents()[0]['status'] == 'open'
        sample(web.operations, {'items': {}})
        assert incidents()[0]['status'] == 'recovered'
    finally:
        for runtime in (web, scheduler):
            runtime.database_runtime.close()
            runtime.database.close()


def test_operations_repository_leaves_saturated_shared_pool_quickly(postgres_state):
    from contextlib import ExitStack
    from time import monotonic

    database, _, _ = postgres_state
    repo = OperationsRepository(database)
    with ExitStack() as leases:
        connections = [leases.enter_context(database.connect()) for _ in range(database.pool.max_size)]
        started = monotonic()
        with pytest.raises(PoolTimeout):
            repo.get('unknown')
        assert monotonic() - started < database.timeout / 2
        assert connections[0].execute('SELECT 1 AS available').fetchone()['available'] == 1
    assert repo.get('unknown') is None


def test_pool_wait_queue_rejection_is_a_service_unavailable_response():
    from psycopg_pool import TooManyRequests

    def busy(_):
        raise TooManyRequests('private pool details')

    web = embedded_app()
    web.state.runtime.operations = SimpleNamespace(repository=SimpleNamespace(get=busy))
    web.dependency_overrides[require_user] = lambda: {'role': 'admin', 'user_id': 'admin-1'}
    web.include_router(router)
    with TestClient(web) as client:
        response = client.get('/api/v1/operations/incidents/' + '0' * 32)
    assert response.status_code == 503
    assert response.json()['detail'] == '运维状态库暂不可用，请稍后重试'


def test_event_retry_does_not_prevent_health_sampling(tmp_path, monkeypatch):
    from app.services import operations_service

    collector_type = operations_service.OperationsCollector
    attempts = []
    samples = []
    released = []
    clock = [30.0]
    monkeypatch.setattr(operations_service, 'monotonic', lambda: clock[0])
    logger = logging.getLogger('app.test_operations')

    def record(items):
        attempts.append([item['error_code'] for item in items])
        if len(attempts) <= collector_type.BATCH_ATTEMPTS:
            raise ConnectionError('unavailable')

    service = SimpleNamespace(role='web', discover=lambda: samples.append(clock[0]),
                              repository=SimpleNamespace(record=record),
                              diagnostics=SimpleNamespace(load_state=lambda: 'healthy'),
                              coordination=SimpleNamespace(acquire=lambda *_args, **_kwargs: 'owner',
                                                           renew=lambda *_args, **_kwargs: True,
                                                           release=lambda *_args: released.append(True)))
    with configured_logging(tmp_path, 'web', 'fixture') as logs:
        event(logger, logging.ERROR, '第一批写入失败', error_code='FIRST_BATCH', incident=True)
        collector = collector_type(service, logs)

        def advance(_):
            clock[0] += 30
            if len(attempts) == collector_type.BATCH_ATTEMPTS:
                event(logger, logging.ERROR, '下一批可写入', error_code='SECOND_BATCH', incident=True)
            elif len(attempts) > collector_type.BATCH_ATTEMPTS:
                collector.stop_event.set()

        monkeypatch.setattr(collector.stop_event, 'wait', advance)
        collector._run()
    assert attempts == [['FIRST_BATCH']] * collector_type.BATCH_ATTEMPTS + [['SECOND_BATCH']]
    assert samples == [30.0, 60.0, 90.0, 120.0] and released == [True]
    rows = OperationsLogQuery(tmp_path, 14).query(LogFilters())['items']
    assert sum(row.get('error_code') == 'OPERATIONS_BATCH_DROPPED' for row in rows) == 1
