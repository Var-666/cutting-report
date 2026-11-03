"""P13 uses fake drivers and isolated files; never connect to a site database."""
from dataclasses import replace
import gc
import json
import multiprocessing
import weakref

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import database
from app.api.auth import require_admin
from app.config import DEVICE_CATALOG, settings
from app.database.mysql import MySqlClient, MySqlUnavailable
from app.database.sqlserver import SqlServerClient
from app.database.source_registry import DatabaseSourceRegistry, SourceConflict, source_configs_json
from app.models.database_sources import DatabaseSourceConfig, DatabaseSourceRequest
from app.services.application_runtime import build_runtime
from app.services.database_configuration import test_source_connection as probe, SourceConnectionError
from app.services.runtime_settings_store import read_runtime_env, write_runtime_env, RuntimeSettingsSaveError


def source(source_id='quality', engine='sqlserver', **updates):
    return DatabaseSourceRequest(id=source_id, name='质量检测', engine=engine, host='isolated-host',
        port=1433 if engine == 'sqlserver' else 3306, database='isolated-db', username='reader',
        password='private-password', **updates)


def config(payload):
    return DatabaseSourceConfig.model_validate(payload.model_dump())


@pytest.fixture
def runtime(tmp_path):
    catalog = tmp_path / 'backend/app/configuration/devices.json'
    catalog.parent.mkdir(parents=True)
    catalog.write_text(DEVICE_CATALOG.model_dump_json())
    cfg = replace(settings, project_root=tmp_path, data_source_mode='demo', database_sources_json='[]',
        weld_enabled=False, worker_database_auth_enabled=False, file_database_enabled=False,
        energy_api_enabled=False, llm_enabled=False)
    state = build_runtime(cfg, role='web')
    yield state
    state.database_runtime.close()


def update(payload, **values):
    return DatabaseSourceRequest.model_validate({**payload.model_dump(), **values})


def test_registry_isolates_configs_dispatches_clients_and_is_lazy():
    sql = config(source())
    mysql = config(source('inspection', 'mysql'))
    registry = DatabaseSourceRegistry([sql, mysql])
    sql_client = registry.client('quality')
    mysql_client = registry.client('inspection')
    assert isinstance(sql_client, SqlServerClient)
    assert isinstance(mysql_client, MySqlClient) and mysql_client._pool is None
    assert registry.client('quality') is sql_client
    assert sql_client.connection_config()['port'] == 1433
    assert mysql_client.config.port == 3306
    assert 'private-password' not in repr(registry.get('quality'))
    assert 'password' not in registry.view('quality').model_dump()
    with pytest.raises(ValueError):
        DatabaseSourceRegistry([sql, sql])
    with pytest.raises(ValueError):
        DatabaseSourceRegistry([sql.model_copy(update={'id': 'production'})])
    with pytest.raises(ValueError):
        DatabaseSourceRegistry.from_json('[{}]')


def test_save_restart_hot_reload_and_old_request_config(runtime):
    manager = runtime.database_runtime
    initial = manager.snapshot()
    manager.save_source(source(), create=True)
    first = manager.snapshot()
    old = first.database_sources.client('quality')
    manager.save_source(source('inspection', 'mysql'), create=True)
    second = manager.snapshot()
    assert second.database_sources.client('quality') is old
    manager.save_source(update(source(), host='new-host', password=''))
    third = manager.snapshot()
    assert old.connection_config()['host'] == 'isolated-host'
    assert third.database_sources.client('quality').connection_config()['host'] == 'new-host'
    assert third.database_sources.get('quality').password.get_secret_value() == 'private-password'
    assert third.devices is initial.devices and third.welding is initial.welding
    worker = build_runtime(runtime.database_runtime.inspect()['settings'], role='worker')
    assert worker.database_runtime.snapshot().database_sources.get('quality').host == 'new-host'
    manager.set_source_enabled('inspection', False)
    assert not worker.database_runtime.snapshot().database_sources.get('inspection').enabled
    with pytest.raises(SourceConflict):
        manager.snapshot().database_sources.client('inspection')
    assert runtime.sqlite_database.path == worker.sqlite_database.path
    worker.database_runtime.close()
    assert manager._base.runtime_settings_env_path.stat().st_mode & 0o777 == 0o600


def test_atomic_save_failure_bad_file_and_diagnostics_keep_published_snapshot(runtime, monkeypatch):
    manager = runtime.database_runtime
    manager.save_source(source(), create=True)
    current = manager.snapshot()
    before = manager._base.runtime_settings_env_path.read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr('app.services.database_runtime.write_runtime_env', lambda *_: (_ for _ in ()).throw(OSError('secret')))
        with pytest.raises(RuntimeSettingsSaveError):
            manager.save_source(update(source(), host='other-host'))
    assert manager.current_context is current
    assert manager._base.runtime_settings_env_path.read_bytes() == before
    write_runtime_env(manager._base.runtime_settings_env_path, {'DATABASE_SOURCES_JSON': '{bad-private-json'})
    assert manager.snapshot() is current and manager.inspect()['error']
    observed = runtime.configuration_diagnostics.observed('test-key')
    assert 'private' not in json.dumps(observed)
    assert observed['items']['DATABASE_SOURCES_JSON']['value'] == '已配置'
    targets, invalid, _ = runtime.configuration_diagnostics.target()
    assert 'DATABASE_SOURCES_JSON' in invalid
    assert manager.current_context is current


def test_bindings_prevent_delete_and_engine_change(runtime):
    from app.services.database_runtime import DatabaseRuntime
    manager = runtime.database_runtime
    manager.save_source(source(), create=True)
    cfg = manager.snapshot().settings
    bound = DatabaseRuntime(cfg, manager.snapshot(), manager._build, runtime.automation_runtime,
        source_bindings={'quality': ('quality-reports',)})
    assert bound.snapshot().database_sources.view('quality').references == ['quality-reports']
    with pytest.raises(SourceConflict):
        bound.delete_source('quality')
    with pytest.raises(SourceConflict):
        bound.save_source(update(source(), engine='mysql', port=3306))
    assert bound.snapshot().database_sources.get('quality').engine == 'sqlserver'


# Separate processes exercise the actual settings_file_lock, not only its thread mutex.
def _save_process(cfg, payload, ready, go):
    state = build_runtime(cfg, role='web')
    ready.put(True)
    go.wait(10)
    state.database_runtime.save_source(DatabaseSourceRequest.model_validate(payload), create=True)
    state.database_runtime.close()


def test_concurrent_process_saves_preserve_other_sources_passwords_and_env(runtime):
    manager = runtime.database_runtime
    manager.save_source(source(), create=True)
    write_runtime_env(manager._base.runtime_settings_env_path, {'CUSTOM_KEY': 'preserved'})
    context = multiprocessing.get_context('spawn')
    ready, go = context.Queue(), context.Event()
    processes = [context.Process(target=_save_process, args=(manager._base,
        source(f'source_{index}').model_dump(mode='json') | {'password': f'private-{index}'}, ready, go)) for index in range(2)]
    for process in processes:
        process.start()
    try:
        for _ in processes:
            assert ready.get(timeout=20)
        go.set()
        for process in processes:
            process.join(20)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join()
    registry = manager.snapshot().database_sources
    assert {item.id for item in registry.configs()} == {'quality', 'source_0', 'source_1'}
    assert registry.get('quality').password.get_secret_value() == 'private-password'
    assert read_runtime_env(manager._base.runtime_settings_env_path)['CUSTOM_KEY'] == 'preserved'


class FakeConnection:
    def __init__(self, pool):
        self.pool = pool
    def start_transaction(self, **values):
        self.pool.transactions.append(values)
    def rollback(self):
        pass
    def close(self):
        self.pool.returns += 1
    def cursor(self, **kwargs):
        pool = self.pool
        class Cursor:
            def execute(self, sql, values):
                pool.queries.append((sql, values, kwargs))
            def fetchmany(self, limit):
                return [{'connected': 1}]
            def close(self):
                pass
        return Cursor()


class FakePool:
    def __init__(self, **values):
        self.values = values
        self.transactions, self.queries = [], []
        self.returns = self.drained = 0
    def get_connection(self):
        return FakeConnection(self)
    def _remove_connections(self):
        self.drained += 1


@pytest.fixture
def pools(monkeypatch):
    import mysql.connector.pooling
    made = []
    def create(**values):
        pool = FakePool(**values)
        made.append(pool)
        return pool
    monkeypatch.setattr(mysql.connector.pooling, 'MySQLConnectionPool', create)
    return made


def test_mysql_close_defers_drain_until_all_inflight_sessions_return(pools):
    client = MySqlClient(config(source('inspection', 'mysql')))
    with client.snapshot() as first:
        with client.snapshot() as second:
            first.fetch('SELECT %s AS value', ('a',))
            client.close()
            assert pools[0].drained == 0
            second.fetch('SELECT %s AS value', ('b',))
        assert pools[0].drained == 0
    assert pools[0].returns == 2 and pools[0].drained == 1
    assert pools[0].transactions == [dict(isolation_level='REPEATABLE READ', consistent_snapshot=True, readonly=True)] * 2
    with pytest.raises(MySqlUnavailable):
        with client.snapshot():
            pass


def test_repeated_runtime_updates_release_old_pools_and_keep_retained_requests(runtime, pools):
    manager = runtime.database_runtime
    payload = source('inspection', 'mysql')
    manager.save_source(payload, create=True)
    old_context = manager.snapshot()
    old_client = old_context.database_sources.client('inspection')
    with old_client.snapshot():
        pass
    for index in range(4):
        manager.save_source(update(payload, host=f'new-{index}'))
        client = manager.snapshot().database_sources.client('inspection')
        with client.snapshot():
            pass
        del client
        gc.collect()
    assert pools[0].drained == 0  # retained request can still begin using its selected client
    with old_client.snapshot():
        pass
    reference = weakref.ref(old_client)
    del old_context, old_client
    gc.collect()
    assert reference() is None and pools[0].drained == 1
    assert [pool.drained for pool in pools[1:]] == [1, 1, 1, 0]
    manager.close()
    assert all(pool.drained == 1 for pool in pools)


@pytest.mark.parametrize('engine', ['sqlserver', 'mysql'])
def test_probe_is_readonly_redacted_unsaved_and_releases_resources(runtime, monkeypatch, pools, engine):
    manager = runtime.database_runtime
    payload = source(engine=engine)
    manager.save_source(payload, create=True)
    before = manager._base.runtime_settings_env_path.read_bytes()
    seen = []
    def query(self, sql, values):
        seen.append((self.connection_config(), sql, values))
        return [{'connected': 1}]
    monkeypatch.setattr(SqlServerClient, 'fetch_all', query)
    candidate = manager.source_for_test(update(payload, password='', enabled=False))
    result = probe(candidate)
    assert result['connected'] and not result['saved']
    assert manager._base.runtime_settings_env_path.read_bytes() == before
    if engine == 'sqlserver':
        assert seen[0][1:] == ('SELECT 1 AS connected', ())
    else:
        assert pools[0].drained == 1 and pools[0].values['pool_size'] == 1
        assert pools[0].queries[0][0] == 'SELECT 1 AS connected'
    monkeypatch.setattr(SqlServerClient, 'fetch_all', lambda *_: (_ for _ in ()).throw(RuntimeError('private-password')))
    with pytest.raises(SourceConnectionError) as error:
        probe(config(source()))
    assert 'private-password' not in str(error.value)


@pytest.fixture
def http(runtime):
    from app.main import safe_connection_validation
    from fastapi.exceptions import RequestValidationError
    app = FastAPI()
    app.state.runtime = runtime
    app.dependency_overrides[require_admin] = lambda: {'role': 'admin'}
    app.include_router(database.router)
    app.add_exception_handler(RequestValidationError, safe_connection_validation)
    @app.middleware('http')
    async def context(request, call_next):
        request.state.request_id = 'source-contract'
        return await call_next(request)
    with TestClient(app) as client:
        yield app, client


def test_admin_crud_contracts_secret_redaction_and_no_arbitrary_sql(http, monkeypatch):
    app, client = http
    body = source().model_dump(mode='json') | {'password': 'private-password'}
    response = client.post('/api/v1/database/sources', json=body)
    assert response.status_code == 201, response.text
    data = response.json()['data']
    assert 'password' not in data and data['password_configured']
    assert client.get('/api/v1/database/sources').json()['data']['items'] == [data]
    assert client.get('/api/v1/database/sources/quality').json()['data'] == data
    assert client.post('/api/v1/database/sources', json=body).status_code == 409
    body['password'] = ''
    body['host'] = 'changed'
    assert client.put('/api/v1/database/sources/quality', json=body).status_code == 200
    assert client.patch('/api/v1/database/sources/quality/enabled', json={'enabled': False}).json()['data']['enabled'] is False
    invalid = client.post('/api/v1/database/sources/test-connection', json={**body, 'query_timeout': -1, 'password': 'private-password'})
    assert invalid.status_code == 422 and 'private-password' not in invalid.text
    assert client.post('/api/v1/database/sources/test-connection', json={**body, 'sql': 'DELETE FROM task'}).status_code == 422
    assert client.get('/api/v1/database/sources/missing').status_code == 404
    assert client.delete('/api/v1/database/sources/quality').json()['data']['deleted']
    assert client.get('/api/v1/database/sources').json()['data']['items'] == []
    app.dependency_overrides.clear()
    for method, path, body in [('get', '/sources', None), ('get', '/sources/quality', None),
            ('post', '/sources', body), ('put', '/sources/quality', body),
            ('patch', '/sources/quality/enabled', {'enabled': False}),
            ('delete', '/sources/quality', None), ('post', '/sources/test-connection', body)]:
        response = client.request(method, '/api/v1/database' + path, json=body)
        assert response.status_code in (401, 403)


def test_sqlserver_independent_config_keeps_parameters_deadline_and_cleanup(monkeypatch):
    import sys
    from types import SimpleNamespace
    from app.core.report_deadline import operation_deadline
    calls = []
    class Connection:
        timeout = 0
        def close(self):
            calls.append('closed')
    connection = Connection()
    def connect(value, **options):
        calls.append((value, options))
        return connection
    monkeypatch.setitem(sys.modules, 'pyodbc', SimpleNamespace(connect=connect))
    sql = config(update(source(), password='semi;brace}secret'))
    client = SqlServerClient(sql)
    with operation_deadline(8):
        with client.connect() as selected:
            assert selected is connection and 1 <= selected.timeout <= 8
    assert calls[-1] == 'closed'
    assert calls[0][1] == {'readonly': True}
    assert 'PWD={semi;brace}}secret}' in calls[0][0]
    assert 'DATABASE=isolated-db' in calls[0][0]


def test_api_contracts_and_worker_role_cannot_manage_sources(http):
    from app.api.auth import require_user
    from app.models.contracts import ApiEnvelope, Items
    from app.models.database_sources import DatabaseSourceView
    app, client = http
    response = client.post('/api/v1/database/sources', json=source().model_dump(mode='json') | {'password': 'private'})
    ApiEnvelope[DatabaseSourceView].model_validate(response.json())
    ApiEnvelope[Items[DatabaseSourceView]].model_validate(client.get('/api/v1/database/sources').json())
    app.dependency_overrides.clear()
    app.dependency_overrides[require_user] = lambda: {'role': 'worker'}
    for method, path in [('get', ''), ('get', '/quality'), ('post', ''), ('put', '/quality'),
            ('patch', '/quality/enabled'), ('delete', '/quality'), ('post', '/test-connection')]:
        assert client.request(method, '/api/v1/database/sources' + path, json={}).status_code == 403


def test_blank_password_preserves_latest_file_then_current_snapshot_and_literal_secrets(runtime):
    manager = runtime.database_runtime
    secret = "private'\\${UNCHANGED};}"
    manager.save_source(update(source(), password=secret), create=True)
    assert manager.snapshot().database_sources.get('quality').password.get_secret_value() == secret
    # Another completed save is authoritative even when this process has an older snapshot.
    latest = config(update(source(), password='latest-private'))
    write_runtime_env(manager._base.runtime_settings_env_path, {'DATABASE_SOURCES_JSON': source_configs_json([latest])})
    manager.save_source(update(source(), password=''))
    assert manager.snapshot().database_sources.get('quality').password.get_secret_value() == 'latest-private'
    # An empty saved password on a disabled profile must not erase the published password.
    empty = config(update(source(), password='', enabled=False))
    write_runtime_env(manager._base.runtime_settings_env_path, {'DATABASE_SOURCES_JSON': source_configs_json([empty])})
    manager.save_source(update(source(), password=''))
    assert manager.snapshot().database_sources.get('quality').password.get_secret_value() == 'latest-private'
