"""Availability monitoring through the public HTTP and persistent queue contracts."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from time import time
from types import SimpleNamespace
import errno
import socket

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.auth import require_user
from app.api.database_monitors import router
from app.database.coordination import CoordinationRepository
from app.database.database_monitors import DatabaseMonitorRepository, DuplicateMonitor
from app.database.postgres import ExecutionLeaseLost, execution_scope
from app.models.database_monitor import DatabaseMonitorInput
from app.services.database_monitor_service import DatabaseMonitorService


def target(**changes):
    return DatabaseMonitorInput(**(dict(name='生产数据库', ip='192.0.2.1', port=1433) | changes))


@pytest.fixture
def monitor(state_database, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr('app.database.database_monitors.time', lambda: clock[0])
    monkeypatch.setattr('app.services.database_monitor_service.time', lambda: clock[0])
    repository = DatabaseMonitorRepository(state_database)
    service = DatabaseMonitorService(repository, 'Asia/Shanghai')
    return service, repository, clock


def test_failure_confirmation_recovery_dedup_and_history(monitor, monkeypatch):
    service, repository, clock = monitor
    identifier = repository.save(target())
    assert service.list()[0]['state'] == 'unknown'
    error = ['连接超时']
    monkeypatch.setattr(service, 'probe', lambda *_: error[0])
    for expected in ['suspect', 'suspect', 'down', 'down']:
        service.run_once(clock[0])
        assert service.list()[0]['state'] == expected
        # A delayed/retried queue round never probes the same observation again.
        service.run_once(clock[0] - 1)
        clock[0] += 60
    history = repository.incidents(1, 15)
    assert history['total'] == 1
    event = history['items'][0]
    assert event['started_at'] == 1000 and event['confirmed_at'] == 1120 and event['ended_at'] is None
    assert service.list()[0]['failures'] == 4
    error[0] = None
    service.run_once(clock[0])
    assert service.list()[0]['state'] == 'recovering'
    clock[0] += 60
    service.run_once(clock[0])
    assert service.list()[0]['state'] == 'healthy'
    event = repository.incidents(1, 15)['items'][0]
    assert event['end_reason'] == 'recovered' and event['ended_at'] == clock[0]
    assert service.list()[0]['failure_started_at'] is None
    repository.delete(identifier)
    assert service.list() == [] and repository.incidents(1, 15)['total'] == 1


def test_stale_detection_is_unknown_and_does_not_count_a_gap_as_consecutive(monitor, monkeypatch):
    service, repository, clock = monitor
    repository.save(target())
    monkeypatch.setattr(service, 'probe', lambda *_: None)
    service.run_once(clock[0])
    clock[0] += 181
    assert service.list()[0]['state'] == 'unknown'
    monkeypatch.setattr(service, 'probe', lambda *_: '连接超时')
    service.run_once(clock[0])
    clock[0] += 60
    service.run_once(clock[0])
    clock[0] += 181
    service.run_once(clock[0])
    assert service.list()[0]['state'] == 'suspect' and service.list()[0]['failures'] == 1
    assert repository.incidents(1, 15)['total'] == 0


@pytest.mark.parametrize(('change', 'reason'), [({'ip': '192.0.2.2'}, 'changed'), ({'enabled': False}, 'paused'), (None, 'deleted')])
def test_edit_pause_delete_reject_inflight_result_without_claiming_recovery(monitor, change, reason):
    service, repository, clock = monitor
    identifier = repository.save(target())
    for _ in range(3):
        repository.record(repository.list()[0], '端口拒绝连接', clock[0])
        clock[0] += 60
    old = repository.list()[0]
    if change is None:
        repository.delete(identifier)
    else:
        repository.save(target(**change), identifier)
    assert repository.record(old, None, clock[0]) is False
    event = repository.incidents(1, 15)['items'][0]
    assert event['end_reason'] == reason
    if reason == 'paused':
        assert service.list()[0]['state'] == 'paused'
        repository.save(target(), identifier)
        assert service.list()[0]['state'] == 'unknown'
    if reason == 'changed':
        assert service.list()[0]['state'] == 'unknown'


def test_concurrent_duplicate_endpoints_and_duplicate_results_are_atomic(monitor):
    service, repository, clock = monitor
    barrier = Barrier(2)
    def save(_):
        barrier.wait()
        try:
            return repository.save(target(ip='2001:db8::1'))
        except DuplicateMonitor:
            return None
    with ThreadPoolExecutor(2) as pool:
        assert sum(value is not None for value in pool.map(save, range(2))) == 1
    old = repository.list()[0]
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: repository.record(old, '连接超时', clock[0]), range(2)))
    assert sorted(results) == [False, True]
    assert service.list()[0]['failures'] == 1


def test_http_crud_validation_permissions_and_global_scope(monitor):
    service, repository, clock = monitor
    app = FastAPI()
    app.state.runtime = SimpleNamespace(database_monitor=service)
    app.include_router(router)
    user = [{'role': 'admin'}]
    app.dependency_overrides[require_user] = lambda: user[0]
    @app.middleware('http')
    async def context(request, call_next):
        request.state.request_id = 'monitor-test'
        return await call_next(request)
    with TestClient(app) as client:
        base = '/api/v1/database-monitors'
        for bad in [dict(ip='host.test'), dict(port=0), dict(port=65536), dict(port=1.5), dict(port=True), dict(name='  '), dict(ip='0.0.0.0')]:
            assert client.post(base, json=dict(name='数据库', ip='192.0.2.1', port=1433) | bad).status_code == 422
        response = client.post(base, json=dict(name='数据库', ip='192.0.2.1', port=1433))
        assert response.status_code == 200
        identifier = response.json()['data']['monitor_id']
        listed = client.get(base, headers={'X-Production-Line': 'welding'}).json()['data']
        assert listed['timezone'] == 'Asia/Shanghai' and listed['items'][0]['state'] == 'unknown'
        assert set(listed['items'][0]) == {'monitor_id', 'name', 'ip', 'port', 'enabled', 'state', 'last_checked_at', 'last_success_at', 'failure_started_at', 'failures', 'successes', 'last_error'}
        assert client.post(base, json=dict(name='重复', ip='192.0.2.1', port=1433)).status_code == 409
        assert client.put(f'{base}/{identifier}', json=dict(name='已暂停', ip='192.0.2.1', port=1433, enabled=False)).status_code == 200
        assert client.get(base).json()['data']['items'][0]['state'] == 'paused'
        assert client.get(base + '/incidents/page').json()['data']['total'] == 0
        user[0] = {'role': 'worker'}
        for method, url in [('get', base), ('post', base), ('put', f'{base}/{identifier}'), ('delete', f'{base}/{identifier}'), ('get', base + '/incidents/page')]:
            assert getattr(client, method)(url).status_code == 403
        user[0] = {'role': 'admin'}
        assert client.delete(f'{base}/{identifier}').status_code == 200
        assert client.delete(f'{base}/{identifier}').status_code == 404
        assert client.get(base).json()['data']['items'] == []


def test_scheduler_consumer_dedup_and_stale_owner_fence(postgres_state, tmp_path, monkeypatch):
    from app.scheduler import SchedulerProcess
    from app.worker import WorkerProcess
    from tests.line_fixtures import configuration
    from tests.postgres_fixtures import state_settings
    cfg = state_settings(configuration(tmp_path), postgres_state)
    scheduler = SchedulerProcess(cfg)
    consumer = WorkerProcess(cfg)
    queue = CoordinationRepository(postgres_state[0])
    try:
        scheduler.runtime.database_monitor.repository.save(target())
        scheduler._schedule()
        scheduler._schedule()
        with queue.connect() as connection:
            assert connection.execute("SELECT COUNT(*) AS n FROM background_tasks WHERE kind='database_monitor'").fetchone()['n'] == 1
            connection.execute("DELETE FROM background_tasks WHERE kind<>'database_monitor'")
        owner = queue.acquire('worker:test-consumer', ttl=30)
        other = queue.acquire('worker:other', ttl=30)
        task = queue.claim_task(owner, analysis=False)
        assert task['kind'] == 'database_monitor' and queue.claim_task(other, analysis=False) is None
        monkeypatch.setattr(consumer.runtime.database_monitor, 'probe', lambda *_: None)
        with execution_scope(consumer.runtime.database, 'worker:test-consumer', owner):
            consumer._handle(task)
        assert consumer.runtime.database_monitor.list()[0]['state'] == 'healthy'
        old = consumer.runtime.database_monitor.repository.list()[0]
        with queue.connect() as connection:
            connection.execute('UPDATE process_leases SET expires_at=0 WHERE owner=%s', (owner,))
        with execution_scope(consumer.runtime.database, 'worker:test-consumer', owner), pytest.raises(ExecutionLeaseLost):
            consumer.runtime.database_monitor.repository.record(old, '连接超时', time())
        # An obsolete queued round must never run a delayed burst of probes.
        monkeypatch.setattr(consumer.runtime.database_monitor, 'probe', lambda *_: pytest.fail('expired round'))
        consumer._handle(task | {'created_at': time() - 61})
    finally:
        scheduler.stop()
        consumer.stop()


@pytest.mark.parametrize(('exception', 'message'), [(TimeoutError(), '连接超时'), (ConnectionRefusedError(), '端口拒绝连接'),
    (OSError(errno.EHOSTUNREACH, 'private details'), '网络或主机不可达'), (OSError('private details'), '连接失败')])
def test_probe_errors_are_bounded_and_sanitized(monkeypatch, exception, message):
    def unavailable(*args, **kwargs):
        assert kwargs['timeout'] == 5
        raise exception
    monkeypatch.setattr(socket, 'create_connection', unavailable)
    assert DatabaseMonitorService.probe('192.0.2.1', 1433) == message


def test_probe_detects_listening_port_and_stopped_service():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        port = listener.getsockname()[1]
        assert DatabaseMonitorService.probe('127.0.0.1', port) is None
    assert DatabaseMonitorService.probe('127.0.0.1', port) == '端口拒绝连接'
