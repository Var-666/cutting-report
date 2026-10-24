from dataclasses import replace
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from app.api import auth, database
from app.api.runtime import runtime_state
from app.config import DEVICE_CATALOG, settings
from app.services.application_runtime import build_runtime
from app.services.database_runtime import database_context
from app.services.runtime_settings_store import read_runtime_env, write_runtime_env
from app.workers.report_worker import ReportWorker


@pytest.fixture
def environment(tmp_path, monkeypatch):
    catalog = tmp_path / 'backend/app/configuration/devices.json'
    catalog.parent.mkdir(parents=True)
    catalog.write_text(DEVICE_CATALOG.model_dump_json())
    cfg = replace(settings, project_root=tmp_path, data_source_mode='demo',
                  worker_database_auth_enabled=False, file_database_enabled=False,
                  sqlserver_host='', sqlserver_database='', sqlserver_username='', sqlserver_password='',
                  worker_sqlserver_host='', worker_sqlserver_database='', worker_sqlserver_username='', worker_sqlserver_password='',
                  file_sqlserver_host='', file_sqlserver_database='', file_sqlserver_username='', file_sqlserver_password='')
    monkeypatch.setattr(database, 'settings', cfg)
    return cfg, build_runtime(cfg, role='web')


def request(state):
    return SimpleNamespace(app=SimpleNamespace(state=state), state=SimpleNamespace(request_id='test-hot-database'))


def payload(**changes):
    values = {'data_source_mode': 'sqlserver', 'worker_database_auth_enabled': True, 'file_database_enabled': True}
    for source in ('production', 'worker', 'file'):
        values[source] = {'host': f'{source}-db', 'database': source, 'username': 'reader',
                          'password': f'{source}-private', 'encrypt': True, 'trust_server_certificate': False}
    values.update(changes)
    return database.DatabaseConfigurationRequest.model_validate(values)


def test_save_publishes_all_database_controls_and_new_clients_without_changing_inflight_context(environment):
    cfg, state = environment
    old_request = request(state)
    old = runtime_state(old_request)
    slots = old.file_downloads._slots
    old_history = next(iter(old.devices.values())).energy_history_worker
    pending_completion = object()
    response = database.save_database_configuration(payload(), request(state))
    assert response['data']['restart_required'] is False
    fresh = runtime_state(request(state))
    assert fresh is not old
    assert runtime_state(old_request) is old
    assert old.settings.effective_data_source_mode == 'demo'
    assert old.cutting_dispatch.pdm.mode == 'mock'
    assert fresh.settings.effective_data_source_mode == 'sqlserver'
    assert fresh.settings.worker_database_auth_enabled and fresh.settings.file_database_enabled
    assert fresh.worker_auth.client.settings.sqlserver_connection('worker')['host'] == 'worker-db'
    assert fresh.cutting_dispatch.pdm.client.settings.sqlserver_connection('file')['host'] == 'file-db'
    assert all(device.query_service.client.settings.sqlserver_host == 'production-db' for device in fresh.devices.values())
    assert fresh.file_downloads._slots is slots
    old_history._pending_finish = pending_completion
    assert next(iter(fresh.devices.values())).energy_history_worker._pending_finish is pending_completion
    old_history._pending_finish = None
    assert state.automation_runtime.snapshot()[0].effective_data_source_mode == 'sqlserver'
    assert cfg.runtime_settings_env_path.stat().st_mode & 0o777 == 0o600
    assert read_runtime_env(cfg.runtime_settings_env_path)['SQLSERVER_PASSWORD'] == 'production-private'
    visible = database.get_database_configuration(request(state))['data']
    assert visible['production']['password_configured']
    assert visible['data_source_mode'] == 'sqlserver'
    assert database.database_status(request(state))['data']['mode'] == 'sqlserver'
    assert auth.auth_status(request(state), user=None)['data']['worker_database_auth_enabled'] is True
    assert 'private' not in str(visible) + str(response)

    blank = payload()
    for source in ('production', 'worker', 'file'):
        getattr(blank, source).password = None
    database.save_database_configuration(blank, request(state))
    assert database_context(state).settings.sqlserver_password == 'production-private'
    database.save_database_configuration(payload(data_source_mode='demo', worker_database_auth_enabled=False,
                                                file_database_enabled=False), request(state))
    disabled = database_context(state)
    assert disabled.settings.effective_data_source_mode == 'demo'
    assert not disabled.worker_auth.settings.worker_database_auth_enabled
    assert disabled.cutting_dispatch.pdm.mode == 'mock'
    assert not auth.auth_status(request(state), user=None)['data']['worker_database_auth_enabled']


def test_worker_loads_the_same_saved_snapshot_and_diagnostics_remains_read_only(environment, monkeypatch):
    cfg, web = environment
    worker = build_runtime(cfg, role='worker')
    old = database_context(worker)
    database.save_database_configuration(payload(), request(web))
    assert worker.database_runtime.inspect()['settings'].effective_data_source_mode == 'demo'
    current = database_context(worker)
    assert current.settings.effective_data_source_mode == 'sqlserver'
    assert old.settings.effective_data_source_mode == 'demo'
    observed = {'state': 'running', 'owner': 'test-worker',
                'configuration': worker.configuration_diagnostics.observed('test-worker')}
    monkeypatch.setattr(web.database_runtime, 'snapshot', lambda: pytest.fail('diagnostics cannot apply settings'))
    report = web.configuration_diagnostics.report(observed)
    keys = {'SQLSERVER_HOST', 'SQLSERVER_PASSWORD', 'WORKER_SQLSERVER_PASSWORD', 'FILE_SQLSERVER_HOST',
            'APP_DATA_SOURCE_MODE', 'APP_EFFECTIVE_DATA_SOURCE_MODE', 'WORKER_DATABASE_AUTH_ENABLED',
            'FILE_DATABASE_ENABLED', 'connections.worker.host'}
    rows = [row for row in report['items'] if row['key'] in keys]
    assert len(rows) == len(keys)
    assert all(row['status'] == 'effective' and not row['restart_required'] for row in rows)
    assert all(row['processes']['web']['source'].startswith('data/settings.env') for row in rows)
    assert 'private' not in str(report)


@pytest.mark.parametrize('values', [
    {'SQLSERVER_PORT': '0'}, {'SQLSERVER_PORT': 'not-a-port'},
    {'WORKER_DATABASE_AUTH_ENABLED': 'bad-boolean'}, {'APP_DATA_SOURCE_MODE': 'unknown'},
    {'APP_DATA_SOURCE_MODE': 'sqlserver', 'SQLSERVER_PASSWORD': 'invalid-private'},
    {'SQLSERVER_PASSWORD': 'secret\nwith-newline'},
])
def test_invalid_file_keeps_last_usable_snapshot_and_can_recover(environment, values):
    cfg, state = environment
    old = database_context(state)
    write_runtime_env(cfg.runtime_settings_env_path, {key: value for key, value in values.items() if '\n' not in value})
    if any('\n' in value for value in values.values()):
        # The normal writer rejects controls; simulate a malformed external edit instead.
        cfg.runtime_settings_env_path.write_text("SQLSERVER_PASSWORD='secret\nwith-newline'\n")
    assert database_context(state) is old
    assert state.database_runtime.inspect()['error']
    assert 'private' not in state.database_runtime.inspect()['error']
    if 'SQLSERVER_PORT' in values:
        write_runtime_env(cfg.runtime_settings_env_path, {'SQLSERVER_PORT': '1434'})
    else:
        cfg.runtime_settings_env_path.unlink()
    assert database_context(state).settings.effective_data_source_mode == 'demo'
    assert state.database_runtime.inspect()['error'] is None


def test_failed_save_or_incomplete_enabled_profile_cannot_publish_or_destroy_saved_settings(environment, monkeypatch):
    cfg, state = environment
    original = database_context(state)
    invalid = payload(worker={'host': 'hr', 'database': '', 'username': '', 'password': None})
    with pytest.raises(HTTPException) as error:
        database.save_database_configuration(invalid, request(state))
    assert error.value.status_code == 422
    assert not cfg.runtime_settings_env_path.exists()
    write_runtime_env(cfg.runtime_settings_env_path, {'CUSTOM_VALUE': 'keep'})
    before = cfg.runtime_settings_env_path.read_bytes()
    monkeypatch.setattr(database, '_write_runtime_env', lambda _: (_ for _ in ()).throw(OSError('disk unavailable')))
    with pytest.raises(HTTPException) as error:
        database.save_database_configuration(payload(), request(state))
    assert error.value.status_code == 500
    assert cfg.runtime_settings_env_path.read_bytes() == before
    assert database_context(state) is original


def test_report_worker_resolves_current_pipeline_for_each_job(environment, monkeypatch):
    cfg, state = environment
    provider = lambda: {key: device.pipeline for key, device in database_context(state).devices.items()}
    worker = ReportWorker(cfg, state.metadata, pipelines=provider)
    database.save_database_configuration(payload(), request(state))
    device_id, device = next(iter(database_context(state).devices.items()))
    received = []
    monkeypatch.setattr(device.pipeline, 'run', lambda job: received.append(device.pipeline.settings.sqlserver_host))
    worker._process_report({'report_id': 'test', 'device_id': device_id}, ZoneInfo('Asia/Shanghai'))
    assert received == ['production-db']


def _worker_process_snapshot(configuration, connection):
    from app.worker import WorkerProcess
    process = WorkerProcess(configuration)
    try:
        connection.send('ready')
        connection.recv()
        process._schedule()
        current = database_context(process.runtime)
        connection.send((current.settings.effective_data_source_mode,
                         current.worker_auth.settings.worker_database_auth_enabled,
                         current.cutting_dispatch.pdm.mode,
                         current.settings.sqlserver_host,
                         process.repository.task_counts()['pending']))
    finally:
        process.stop()
        connection.close()


def test_independent_worker_process_loads_saved_web_configuration_before_scheduling(environment):
    import multiprocessing
    cfg, state = environment
    ctx = multiprocessing.get_context('spawn')
    parent, child = ctx.Pipe()
    worker = ctx.Process(target=_worker_process_snapshot, args=(cfg, child))
    worker.start()
    try:
        assert parent.poll(15), 'worker startup did not complete'
        assert parent.recv() == 'ready'
        database.save_database_configuration(payload(), request(state))
        parent.send('refresh')
        assert parent.poll(15), 'worker failed to load saved configuration'
        mode, hr_enabled, pdm_mode, host, pending = parent.recv()
        assert (mode, hr_enabled, pdm_mode, host) == ('sqlserver', True, 'sqlserver', 'production-db')
        assert pending > 0
        worker.join(timeout=5)
        assert worker.exitcode == 0
    finally:
        parent.close()
        child.close()
        if worker.is_alive():
            worker.terminate()
            worker.join(timeout=5)


def test_http_save_stays_admin_only_and_never_echoes_credentials(environment):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    cfg, state = environment
    app = FastAPI()
    for name, value in vars(state).items():
        setattr(app.state, name, value)
    app.include_router(database.router)
    app.include_router(auth.router)

    @app.middleware('http')
    async def request_id(request, call_next):
        request.state.request_id = 'database-runtime-http'
        return await call_next(request)

    state.workshop.bootstrap_admin(username='hot-admin', display_name='测试管理员', password='test-admin-password',
                                   now='2026-10-24T10:00:00+08:00')
    body = payload().model_dump(mode='json')
    for source in ('production', 'worker', 'file'):
        body[source]['password'] = f'{source}-private'
    with TestClient(app) as client:
        assert client.put('/api/v1/database/configuration', json=body).status_code == 401
        login = client.post('/api/v1/auth/login', json={'username': 'hot-admin', 'password': 'test-admin-password'})
        assert login.status_code == 200
        saved = client.put('/api/v1/database/configuration', json=body)
        assert saved.status_code == 200
        assert saved.json()['data']['restart_required'] is False
        assert 'private' not in saved.text
        config = client.get('/api/v1/database/configuration')
        assert config.status_code == 200 and config.json()['data']['data_source_mode'] == 'sqlserver'
        assert 'private' not in config.text
        status = client.get('/api/v1/database/status')
        assert status.status_code == 200 and all(item['enabled'] for item in status.json()['data']['data_sources'])
        assert client.get('/api/v1/auth/status').json()['data']['worker_database_auth_enabled'] is True


def test_an_open_connection_finishes_on_its_original_profile_after_a_new_save(environment, monkeypatch):
    import sys
    _, state = environment
    database.save_database_configuration(payload(), request(state))
    old_client = next(iter(database_context(state).devices.values())).query_service.client
    connection = SimpleNamespace(closed=False)
    connection.close = lambda: setattr(connection, 'closed', True)
    monkeypatch.setitem(sys.modules, 'pyodbc', SimpleNamespace(connect=lambda _: connection))
    with old_client.connect() as active:
        changed = payload()
        changed.production.host = 'replacement-production-db'
        database.save_database_configuration(changed, request(state))
        assert active is connection and not connection.closed
        assert 'SERVER=production-db,1433' in old_client._connection_string()
        new_client = next(iter(database_context(state).devices.values())).query_service.client
        assert 'SERVER=replacement-production-db,1433' in new_client._connection_string()
    assert connection.closed
