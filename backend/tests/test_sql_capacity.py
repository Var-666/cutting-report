from tests.postgres_fixtures import state_settings
"""Offline SQL boundary/load checks: real lock files, blocking driver substitutes."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
import multiprocessing as mp
from pathlib import Path
from threading import Event, Lock
from time import monotonic, sleep
from types import SimpleNamespace
import sys

import anyio
import pytest

from app.core.sql_capacity import SqlCapacity, SqlCapacityError, sql_identity
from app.core.report_deadline import operation_deadline, remaining_seconds, ReportOperationTimeout
from app.core.request_cancellation import bind_request_id, reset_request_id, sql_request_cancellations, SqlRequestCancelled
from app.database.sqlserver import SqlServerClient
from app.database.mysql import MySqlClient, MySqlUnavailable
from app.models.database_sources import DatabaseSourceConfig
from tests.line_fixtures import configuration


def source(key='a', engine='sqlserver', **changes):
    values = dict(id=key, name=key, engine=engine, host='offline.test', port=3306 if engine == 'mysql' else 1433,
                  database='offline', username='reader', password='test-only')
    return DatabaseSourceConfig(**(values | changes))


def capacity(root, **changes):
    return SqlCapacity(configuration(root, sql_global_max_concurrent=3, sqlserver_default_max_concurrent=2,
        sql_line_max_concurrent=1, sql_global_max_waiters=32, sql_queue_timeout_seconds=2, **changes))


def wait_until(condition):
    until = monotonic() + 3
    while not condition():
        assert monotonic() < until, 'resource state did not settle'
        sleep(.005)


def usage(resource, kind='global'):
    return next(row for row in resource.status()['items'] if row['resource_type'] == kind)


def test_twenty_arrivals_obey_all_three_limits_and_lock_files_do_not_grow_per_query(tmp_path):
    resource = capacity(tmp_path)
    configs = [source('a'), source('b')]
    resource.register_sources(configs)
    guard = Lock()
    active, peaks = {}, {}
    def query(index):
        config = configs[index % 2]
        line = f'line-{index % 4}'
        with sql_identity(line), resource.slot(config):
            keys = ('global', f'source:{config.id}', line)
            with guard:
                for key in keys:
                    active[key] = active.get(key, 0) + 1
                    peaks[key] = max(peaks.get(key, 0), active[key])
            sleep(.01)
            with guard:
                for key in keys: active[key] -= 1
        return index
    with ThreadPoolExecutor(max_workers=20) as pool:
        assert sorted(pool.map(query, range(20))) == list(range(20))
    assert peaks['global'] <= 3
    assert all(value <= (2 if key.startswith('source:') else 1) for key, value in peaks.items() if key != 'global')
    files = set(resource.directory.glob('*.lock'))
    for index in range(100): query(index)
    assert set(resource.directory.glob('*.lock')) == files
    assert all(row['executing'] == row['waiting'] == 0 for row in resource.status()['items'])


def test_saturated_source_and_full_waiting_queue_do_not_reserve_unrelated_permits(tmp_path):
    resource = capacity(tmp_path)
    a, b = source('a', sql_max_concurrent=1), source('b')
    resource.register_sources([a, b])
    resource.wait_limit = 2
    entered = Event()
    def blocked():
        entered.set()
        with resource.slot(a): pass
    with ThreadPoolExecutor(max_workers=2) as pool, resource.slot(a):
        jobs = [pool.submit(blocked) for _ in range(2)]
        wait_until(lambda: usage(resource)['waiting'] == 2)
        with pytest.raises(SqlCapacityError) as busy:
            with resource.slot(a): pass
        assert busy.value.reason == 'queue_full'
        with resource.slot(b):
            assert usage(resource)['executing'] == 2
    for job in jobs: job.result()
    assert usage(resource)['rejected'] == 1


@pytest.mark.parametrize('action', ['timeout', 'cancel', 'deadline'])
def test_waiting_timeout_cancellation_and_total_deadline_release_tickets(tmp_path, action):
    resource = capacity(tmp_path)
    resource.timeout = .05 if action == 'timeout' else 1
    config = source(sql_max_concurrent=1)
    resource.register_sources([config])
    request = sql_request_cancellations.activate('sql-wait-test')
    def waiting():
        token = bind_request_id(request)
        try:
            with operation_deadline(.05 if action == 'deadline' else 2), resource.slot(config): pass
        finally:
            reset_request_id(token)
    with ThreadPoolExecutor(max_workers=1) as pool, resource.slot(config):
        job = pool.submit(waiting)
        wait_until(lambda: usage(resource)['waiting'] == 1)
        if action == 'cancel': sql_request_cancellations.cancel(request)
        with pytest.raises(SqlRequestCancelled if action == 'cancel' else ReportOperationTimeout if action == 'deadline' else SqlCapacityError):
            job.result(timeout=2)
        assert usage(resource)['waiting'] == 0
        assert usage(resource)['timeouts'] == (0 if action == 'cancel' else 1)
    sql_request_cancellations.finish(request)
    with resource.slot(config): pass
    assert usage(resource)['executing'] == 0


def test_nested_deadlines_cannot_extend_the_outer_operation():
    with operation_deadline(.1):
        before = remaining_seconds()
        with operation_deadline(10):
            assert 0 < remaining_seconds() <= before


class OdbcConnection:
    timeout = 0
    def __init__(self, resource, *, failure=False):
        self.resource = resource
        self.failure = failure
        self.description = [('value',)]
        self.batches = [[(1,)], []]
        self.closed = False
    def cursor(self): return self
    def execute(self, sql, values):
        assert usage(self.resource)['executing'] >= 1
        if self.failure: raise TimeoutError('driver test failure')
    def fetchall(self): return [(1,)]
    def fetchmany(self, size): return self.batches.pop(0)
    def nextset(self): return False
    def close(self):
        assert usage(self.resource)['executing'] >= 1
        self.closed = True
    def cancel(self): pass


@pytest.mark.parametrize('mode', ['fetch', 'stream', 'result_sets', 'connect_failure', 'query_failure'])
def test_sqlserver_holds_one_permit_until_the_connection_or_stream_closes(tmp_path, monkeypatch, mode):
    resource = capacity(tmp_path)
    config = source()
    resource.register_sources([config])
    connection = OdbcConnection(resource, failure=mode == 'query_failure')
    def connect(*args, **kwargs):
        assert kwargs == {'readonly': True}
        if mode == 'connect_failure': raise TimeoutError('connect timeout')
        return connection
    monkeypatch.setitem(sys.modules, 'pyodbc', SimpleNamespace(connect=connect))
    client = SqlServerClient(config, capacity=resource)
    with sql_identity('line-a'):
        if mode == 'stream':
            rows = client.iter_rows('SELECT value', ())
            assert next(rows) == {'value': 1}
            assert usage(resource)['executing'] == 1
            rows.close()
        elif mode in ('connect_failure', 'query_failure'):
            with pytest.raises(TimeoutError): client.fetch_all('SELECT value', ())
        elif mode == 'result_sets':
            assert client.fetch_result_sets('SELECT value', ()) == [[{'value': 1}]]
        else:
            assert client.fetch_all('SELECT value', ()) == [{'value': 1}]
    assert usage(resource)['executing'] == usage(resource)['waiting'] == 0


def test_mysql_admits_before_pool_checkout_and_returns_each_independent_transaction(tmp_path, monkeypatch):
    resource = capacity(tmp_path)
    config = source('mysql', 'mysql', pool_size=2, sql_max_concurrent=3)
    resource.register_sources([config])
    client = MySqlClient(config, capacity=resource)
    active = [0, 0]
    guard = Lock()
    transactions = []
    class Connection:
        def start_transaction(self, **options): transactions.append((id(self), options))
        def rollback(self): assert usage(resource)['executing'] > 0
        def close(self):
            assert usage(resource)['executing'] > 0
            with guard: active[0] -= 1
    def checkout():
        with guard:
            active[0] += 1
            active[1] = max(active)
            assert active[0] <= 2, 'pool exhausted before admission'
        return Connection()
    monkeypatch.setattr(client, '_get_pool', lambda: SimpleNamespace(get_connection=checkout))
    def query(_):
        with client.snapshot(): sleep(.01)
    with ThreadPoolExecutor(max_workers=20) as pool: list(pool.map(query, range(20)))
    assert active[0] == 0 and active[1] <= 2
    assert len(transactions) == 20
    assert all(options == dict(isolation_level='REPEATABLE READ', consistent_snapshot=True, readonly=True) for _, options in transactions)
    assert usage(resource)['executing'] == usage(resource)['waiting'] == 0


@pytest.mark.parametrize('failure', ['checkout', 'begin', 'query', 'cancel'])
def test_mysql_failure_and_cancellation_restore_shared_and_local_pool_slots(tmp_path, monkeypatch, failure):
    resource = capacity(tmp_path)
    client = MySqlClient(source('mysql', 'mysql', pool_size=1, sql_max_concurrent=1), capacity=resource)
    failing = [True]
    cleanup = []
    class Connection:
        def start_transaction(self, **options):
            if failing[0] and failure == 'begin': raise TimeoutError('transaction timeout')
        def rollback(self): cleanup.append('rollback')
        def close(self): cleanup.append('close')
    def checkout():
        if failing[0] and failure == 'checkout': raise TimeoutError('connection timeout')
        return Connection()
    monkeypatch.setattr(client, '_get_pool', lambda: SimpleNamespace(get_connection=checkout))
    with pytest.raises(SqlRequestCancelled if failure == 'cancel' else MySqlUnavailable):
        with client.snapshot():
            if failure == 'cancel': raise SqlRequestCancelled('cancelled')
            raise TimeoutError('query timeout')
    assert usage(resource)['executing'] == usage(resource)['waiting'] == 0
    assert cleanup == ([] if failure == 'checkout' else ['rollback', 'close'])
    failing[0] = False
    with client.snapshot():
        assert usage(resource)['executing'] == 1
    assert usage(resource)['executing'] == 0


def _process_slot(root, channel):
    resource = capacity(Path(root))
    config = source(sql_max_concurrent=1)
    resource.register_sources([config])
    channel.send('waiting')
    with resource.slot(config):
        channel.send('entered')
        sleep(20)


def test_web_and_worker_processes_share_source_capacity_and_crash_releases_it(tmp_path):
    resource = capacity(tmp_path)
    config = source(sql_max_concurrent=1)
    resource.register_sources([config])
    ctx = mp.get_context('spawn')
    parent, child = ctx.Pipe()
    process = ctx.Process(target=_process_slot, args=(str(tmp_path), child))
    try:
        with resource.slot(config):
            process.start()
            assert parent.poll(5) and parent.recv() == 'waiting'
            assert not parent.poll(.1)
        assert parent.poll(5) and parent.recv() == 'entered'
        resource.timeout = .05
        with pytest.raises(SqlCapacityError):
            with resource.slot(config): pass
        process.terminate()
        process.join(5)
        assert not process.is_alive()
        with resource.slot(config): pass
    finally:
        if process.is_alive(): process.kill(); process.join(5)
        parent.close(); child.close()


def test_http_burst_queues_before_threads_and_other_lines_and_snapshots_still_run(tmp_path, monkeypatch):
    from app.api import workloads
    resource = capacity(tmp_path)
    resource.register_sources([source('a'), source('b')])
    monkeypatch.setattr(workloads, 'default_sql_capacity', lambda: resource)
    pool = workloads.RequestWorkload(4, 'SQL test', max_waiters=32, partitioned=True)
    local = workloads.RequestWorkload(1, 'SQLite test', max_waiters=1)
    entered, release = Event(), Event()
    def slow():
        entered.set()
        assert release.wait(3)
        return 1
    async def exercise():
        with sql_identity('line-a', 'a'):
            tasks = [asyncio.create_task(pool.run_sync(slow)) for _ in range(20)]
        try:
            with anyio.fail_after(2):
                while not entered.is_set() or len(pool._queue) != 19: await anyio.sleep(.005)
            assert pool._threads.borrowed_tokens == 1
            assert await local.run_sync(lambda: 'cached') == 'cached'
            with sql_identity('line-b', 'b'):
                with anyio.fail_after(.5): assert await pool.run_sync(lambda: 'independent') == 'independent'
        finally: release.set()
        assert await asyncio.gather(*tasks) == [1] * 20
        assert pool.status()['executing'] == pool.status()['waiting'] == 0
    anyio.run(exercise)


def test_http_and_sql_waiting_share_one_admission_deadline(tmp_path):
    from app.api.workloads import RequestWorkload
    resource = capacity(tmp_path)
    config = source(sql_max_concurrent=1)
    resource.register_sources([config])
    pool = RequestWorkload(1, 'two stages', max_waiters=2, queue_timeout_seconds=.3)
    entered, release = Event(), Event()
    def blocker():
        entered.set()
        assert release.wait(3)
    def query():
        with resource.slot(config): pass
    async def exercise():
        first = asyncio.create_task(pool.run_sync(blocker))
        with anyio.fail_after(1):
            while not entered.is_set(): await anyio.sleep(.005)
        started = monotonic()
        waiting = asyncio.create_task(pool.run_sync(query))
        await anyio.sleep(.2)
        release.set()
        with pytest.raises(SqlCapacityError) as failure:
            await waiting
        assert failure.value.reason == 'queue_timeout'
        assert monotonic() - started < .45
        await first
    with resource.slot(config): anyio.run(exercise)
    assert usage(resource)['waiting'] == 0


@pytest.mark.parametrize('cancellation', ['disconnect', 'scope'])
def test_http_disconnect_and_cancel_scope_release_a_database_waiter(tmp_path, cancellation):
    from fastapi import Request
    from app.api.workloads import RequestWorkload
    resource = capacity(tmp_path)
    config = source(sql_max_concurrent=1)
    resource.register_sources([config])
    pool = RequestWorkload(1, 'cancellable SQL', max_waiters=1)
    request_id = sql_request_cancellations.activate('disconnect-test')
    gone = Event()
    async def receive():
        return {'type': 'http.disconnect'} if gone.is_set() else {'type': 'http.request', 'body': b'', 'more_body': False}
    request = Request({'type': 'http', 'method': 'GET', 'path': '/', 'headers': []}, receive)
    scopes = []
    def query():
        with resource.slot(config): pass
    async def exercise():
        token = bind_request_id(request_id)
        async def call():
            with anyio.CancelScope() as scope:
                scopes.append(scope)
                if cancellation == 'disconnect':
                    with pytest.raises(SqlRequestCancelled): await pool.run_sync(query, _request=request)
                else:
                    await pool.run_sync(query)
        try:
            async with anyio.create_task_group() as group:
                group.start_soon(call)
                with anyio.fail_after(1):
                    while usage(resource)['waiting'] != 1: await anyio.sleep(.005)
                if cancellation == 'disconnect': gone.set()
                else: scopes[0].cancel()
                with anyio.fail_after(.5):
                    while usage(resource)['waiting']: await anyio.sleep(.005)
            assert pool.status()['executing'] == 0
        finally: reset_request_id(token)
    try:
        with resource.slot(config): anyio.run(exercise)
    finally: sql_request_cancellations.finish(request_id)


def test_source_quota_save_is_cold_and_preserves_clients_and_existing_data_identity(postgres_state, tmp_path, monkeypatch):
    from hashlib import sha256
    import json
    from app.models.database_sources import DatabaseSourceRequest
    from app.services.application_runtime import build_runtime
    from app.services.database_runtime import database_context
    from app.services.runtime_settings_store import RuntimeSettingsSaveError
    runtime = build_runtime(state_settings(configuration(tmp_path, real=True),postgres_state), role='web')
    before = database_context(runtime)
    key = 'cutting-production'
    config = before.database_sources.get(key)
    # The exact P1/P2 serialization, before the quota field existed.
    legacy_json = json.dumps([{**config.model_dump(mode='json', exclude={'password', 'sql_max_concurrent'}),
                             'password': config.password.get_secret_value()}], ensure_ascii=False)
    expected = sha256(legacy_json.encode()).hexdigest()
    assert before.database_sources.fingerprint(key) == expected
    runtime.database_runtime.save_source(DatabaseSourceRequest.model_validate({**config.model_dump(), 'sql_max_concurrent': 1}))
    fresh = database_context(runtime)
    assert fresh.database_sources.fingerprint(key) == expected
    assert fresh.database_sources.client(key) is before.database_sources.client(key)
    assert fresh.lines['Cutter01'] is before.lines['Cutter01']
    stat = next(item for item in fresh.database_sources.capacity.status(fresh.database_sources.configs())['items'] if item['resource'] == key)
    assert stat['execution_capacity'] == 6 and stat['configured_capacity'] == 1 and stat['restart_required']
    report = runtime.configuration_diagnostics.report({'state': 'missing'})
    row = next(item for item in report['items'] if item['key'] == 'DATABASE_SOURCES_JSON')
    assert row['restart_required'] and row['processes']['web']['status'] == 'pending'
    restarted = build_runtime(state_settings(fresh.settings,postgres_state), role='web')
    assert database_context(restarted).database_sources.capacity.source_limit(key) == 1
    monkeypatch.setattr('app.services.database_runtime.write_runtime_env', lambda *a, **kw: (_ for _ in ()).throw(OSError('test')))
    new = DatabaseSourceRequest.model_validate({**config.model_dump(), 'id': 'not-published', 'sql_max_concurrent': 1})
    with pytest.raises(RuntimeSettingsSaveError): runtime.database_runtime.save_source(new, create=True)
    assert fresh.database_sources.capacity.source_limit('not-published') == 6


@pytest.mark.parametrize('failure', ['queue_timeout', 'operation_timeout'])
def test_http_reports_busy_sql_with_instance_identity_and_exposes_only_process_statistics(postgres_state, tmp_path, monkeypatch, failure):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.api.auth import require_admin
    from app.database.source_registry import DatabaseSourceRegistry, source_configs_json
    from app.services.application_runtime import build_runtime
    from app.services.database_runtime import database_context
    cfg = configuration(tmp_path, real=True, sql_queue_timeout_seconds=.05)
    configs = DatabaseSourceRegistry.from_json(cfg.database_sources_json).configs()
    configs[0] = configs[0].model_copy(update={'sql_max_concurrent': 1})
    runtime = build_runtime(state_settings(replace(cfg, database_sources_json=source_configs_json(configs)),postgres_state), role='web')
    context = database_context(runtime)
    if failure == 'operation_timeout':
        from app.api import workloads
        monkeypatch.setattr(workloads, 'settings', SimpleNamespace(report_operation_timeout_seconds=.02))
    resource = context.database_sources.capacity
    config = context.database_sources.get('cutting-production')
    monkeypatch.setattr(app.state, 'runtime', runtime, raising=False)
    monkeypatch.setitem(app.dependency_overrides, require_admin, lambda: {'role': 'admin'})
    class Cursor:
        description = [('task_id',)]
        def execute(self, *args):
            assert any(item['resource'] == 'Cutter01' and item['executing'] == 1 for item in resource.status()['items'])
        def fetchall(self): return []
        def close(self): pass
        def cancel(self): pass
    class Connection:
        def cursor(self): return Cursor()
        def close(self): pass
    monkeypatch.setitem(sys.modules, 'pyodbc', SimpleNamespace(connect=lambda *a, **kw: Connection()))
    client = TestClient(app)
    body = {'dataset': 'equipment', 'report_date': '2026-11-03', 'limit': 10}
    with resource.slot(config):
        blocked = client.post('/api/v1/database/preview?device_id=Cutter01', json=body)
    if failure == 'operation_timeout':
        assert blocked.status_code == 504 and blocked.json()['error']['code'] == 'OPERATION_TIMEOUT'
    else:
        assert blocked.status_code == 503 and blocked.headers['X-SQL-Resource-Error'] == 'queue_timeout'
    assert client.post('/api/v1/database/preview?device_id=Cutter01', json=body).status_code == 200
    status = client.get('/api/v1/database/resources')
    assert status.status_code == 200
    assert status.json()['data']['web']['scope'] == 'process'
    assert status.json()['data']['worker'] is None
    assert 'cutting-private' not in status.text and 'cutting.test' not in status.text


def test_shared_hr_pdm_and_dispatch_queries_never_borrow_a_selected_line_identity():
    from app.core.sql_capacity import current_sql_identity
    from app.services.worker_auth_service import WorkerAuthService
    from app.database.pdm import SqlServerPdmRepository
    from app.services.magic_box_service import MagicBoxConfigProvider
    from tests.line_fixtures import cutting_settings
    seen = []
    client = SimpleNamespace(fetch_all=lambda *args: seen.append(current_sql_identity.get().line_id) or [])
    cfg = cutting_settings()
    with sql_identity('Cutter01', 'cutting-production'):
        for service in (WorkerAuthService(cfg, client=client), SqlServerPdmRepository(cfg, client=client), MagicBoxConfigProvider(cfg, client=client)):
            service._fetch_all('SELECT 1', ())
    assert seen == [None, None, None]


def test_worker_and_automation_share_web_quota_and_retry_without_losing_task(postgres_state, tmp_path, monkeypatch):
    from datetime import datetime
    from app.database.source_registry import DatabaseSourceRegistry, source_configs_json
    from app.services.application_runtime import build_runtime
    from app.services.database_runtime import database_context
    from app.models.domain import QueryBundle
    from app.worker import WorkerProcess
    cfg = configuration(tmp_path, real=True, sql_queue_timeout_seconds=.05)
    configs = DatabaseSourceRegistry.from_json(cfg.database_sources_json).configs()
    configs[0] = configs[0].model_copy(update={'sql_max_concurrent': 1})
    cfg = replace(cfg, database_sources_json=source_configs_json(configs))
    web = build_runtime(state_settings(cfg,postgres_state), role='web')
    worker = WorkerProcess(state_settings(cfg,postgres_state))
    worker.owner = worker.repository.acquire('worker', ttl=30)
    context = database_context(worker.runtime)
    resource = context.database_sources.capacity
    line = context.lines['Cutter01']
    client = context.database_sources.client('cutting-production')
    connections = []
    def connect(*args, **kwargs):
        assert usage(resource, 'line')['resource'] == 'Cutter01'
        connection = OdbcConnection(resource)
        connections.append(connection)
        return connection
    monkeypatch.setitem(sys.modules, 'pyodbc', SimpleNamespace(connect=connect))
    monkeypatch.setattr(line.background, 'handle_background', lambda *a: client.fetch_all('SELECT 1', ()))
    def prepare(*args):
        client.fetch_all('SELECT 1', ())
        return QueryBundle(summary_records=[], workshop_records=[], equipment_records=[])
    saved = []
    device = SimpleNamespace(settings=line.settings, query_service=SimpleNamespace(fetch_workshop_data=prepare),
        workshop=SimpleNamespace(sync_daily_report=lambda **kw: saved.append(kw)))
    run = dict(report_date='2026-11-03', created_by_id=1, created_by_name='test')
    now = datetime.fromisoformat('2026-11-03T10:00:00+08:00')
    worker.repository.enqueue('current_refresh', 'Cutter01', {})
    task = worker.repository.claim_task(worker.owner, analysis=False)
    try:
        web_context = database_context(web)
        with web_context.database_sources.capacity.slot(web_context.database_sources.get('cutting-production')):
            with worker.scope():
                worker._execute(task)
                worker._flush_completions()
            with pytest.raises(SqlCapacityError):
                worker.runtime.automation_service._ensure_workshop_report(run, now, device)
        assert not connections and not saved
        assert worker.repository.task_counts() == {'deferred': 1}
        with worker.repository.connect() as connection:
            row = connection.execute('SELECT last_error,next_attempt,updated_at FROM background_tasks').fetchone()
            assert row['last_error'] == 'SqlCapacityError' and row['next_attempt'] > row['updated_at']
            connection.execute('UPDATE background_tasks SET next_attempt=0')
        worker.repository.admit_deferred()
        retry = worker.repository.claim_task(worker.owner, analysis=False)
        assert retry['task_id'] == task['task_id'] and retry['attempts'] == 2
        with worker.scope():
            worker._execute(retry)
            worker._flush_completions()
        worker.runtime.automation_service._ensure_workshop_report(run, now, device)
        assert worker.repository.task_counts() == {'completed': 1}
        assert len(connections) == 2 and all(connection.closed for connection in connections)
        assert len(saved) == 1 and usage(resource)['executing'] == usage(resource)['waiting'] == 0
    finally:
        worker.stop()
        web.database_runtime.close()


def test_unsaved_probe_ids_share_fixed_slots_without_polluting_source_registration(tmp_path):
    resource = capacity(tmp_path)
    for index in range(20):
        config = source(f'unsaved-{index}')
        with resource.slot(config, probe=True): pass
        assert not resource.knows_source(config.id)
    assert {row['resource'] for row in resource.status()['items']} == {'sql', 'probe:sqlserver'}
    assert len(list(resource.directory.glob('*.lock'))) == 2
    for key in ('one', 'two'):
        with resource.slot(source(key)): pass
    assert resource.knows_source('one') and resource.knows_source('two')


@pytest.mark.parametrize('boundary', ['equipment', 'leadership', 'energy_tasks', 'energy_range', 'energy_monthly_range'])
@pytest.mark.parametrize('error', [SqlRequestCancelled('cancelled'), SqlCapacityError('queue_full'), ReportOperationTimeout('deadline')])
def test_report_boundaries_preserve_cancellation_capacity_and_total_timeout(monkeypatch, boundary, error):
    from datetime import date
    from app.api import energy, equipment, leadership
    def fail(*args, **kwargs): raise error
    device = SimpleNamespace(query_service=SimpleNamespace(fetch_equipment_data=fail),
        energy_period_service=SimpleNamespace(get_monthly_range=fail),
        range_report_service=SimpleNamespace(leadership=fail, equipment=fail))
    day = date(2026, 11, 3)
    module, handler, args = {
        'equipment': (equipment, equipment.get_equipment_report, dict(report_date=day, refresh=False)),
        'leadership': (leadership, leadership.get_leadership_report, dict(report_month=None, start_date=day, end_date=day, refresh=False)),
        'energy_tasks': (energy, energy.get_task_energy_day, dict(report_date=day, refresh=False)),
        'energy_range': (energy, energy.get_energy_range, dict(start_date=day, end_date=day, refresh=False)),
        'energy_monthly_range': (energy, energy.get_energy_monthly_range, dict(start_date=day, end_date=day)),
    }[boundary]
    monkeypatch.setattr(module, 'device_state', lambda request: device)
    with pytest.raises(type(error)) as caught:
        handler(SimpleNamespace(), **args)
    assert caught.value is error
