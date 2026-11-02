from dataclasses import FrozenInstanceError, replace
from datetime import date
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from app.api import auth, database
from app.api.runtime import application_state, runtime_state
from app.config import DEVICE_CATALOG, settings
from app.services.application_runtime import ApplicationRuntime, build_runtime
from app.services.database_runtime import DatabaseServices, database_context
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


def test_runtime_graph_reuses_injected_repositories_and_has_an_immutable_service_snapshot(environment):
    _, state = environment
    context = state.database_runtime.current_context
    assert isinstance(state, ApplicationRuntime)
    assert isinstance(context, DatabaseServices)
    with pytest.raises(FrozenInstanceError):
        context.settings = replace(context.settings, app_version='changed')
    assert state.automation_service.database_runtime is state.database_runtime
    assert state.configuration_diagnostics.database_runtime is state.database_runtime
    for device in context.devices.values():
        assert device.pipeline.workshop_repository is device.workshop
        assert device.pipeline.range_service.workshop is device.workshop
        assert device.confirmations is device.confirmations
        assert device.confirmations.database is state.sqlite_database
        assert device.confirmations.scope_machine_no == device.settings.report_machine_no
        assert device.workers == ()


def test_depends_and_direct_access_share_one_canonical_request_snapshot(environment, monkeypatch):
    from fastapi import Depends, FastAPI, Request
    from fastapi.testclient import TestClient
    cfg, state = environment
    app = FastAPI()
    app.state.runtime = state
    original = state.database_runtime.snapshot
    reads = []

    def snapshot():
        reads.append(True)
        return original()

    monkeypatch.setattr(state.database_runtime, 'snapshot', snapshot)

    @app.get('/probe')
    def probe(request: Request, context: DatabaseServices = Depends(runtime_state),
              runtime: ApplicationRuntime = Depends(application_state)):
        assert runtime is state
        runtime.database_runtime.publish(replace(context, settings=replace(context.settings, app_version='next')), {})
        assert runtime_state(request) is context
        return {'version': context.settings.app_version}

    with TestClient(app) as client:
        assert client.get('/probe').json() == {'version': cfg.app_version}
        assert client.get('/probe').json() == {'version': 'next'}
    assert len(reads) == 2


def test_device_pipeline_uses_the_injected_daily_report_repository_to_publish_a_report(environment, monkeypatch):
    from app.models.domain import ReportFilter
    from tests.analysis_fakes import model_analysis
    cfg, state = environment
    cfg.echarts_vendor_path.parent.mkdir(parents=True)
    cfg.echarts_vendor_path.write_text('window.echarts={};')
    device = database_context(state).devices['Cutter01']
    stamp = '2026-08-18T10:00:00+08:00'
    user = state.users.bootstrap_admin(username='pipeline-admin', display_name='管理员', password='test-password', now=stamp)
    bundle = device.query_service.fetch_workshop_data(ReportFilter(report_date=date(2026, 8, 18)))
    device.workshop.sync_daily_report(report_date='2026-08-18', source_mode='demo',
        records=bundle.workshop_records, user=user, now=stamp, scope_machine_no=device.settings.report_machine_no)
    monkeypatch.setattr(device.report_analysis_service.llm, 'analyze_context', model_analysis)
    device.metadata.create_job(report_id='injected-pipeline', report_type='workshop', report_date='2026-08-18',
        project_code=None, ship_no=None, request_id='test-runtime-pipeline', created_at=stamp)
    device.pipeline.run(device.metadata.claim_next_job(stamp))
    job = device.metadata.get_job('injected-pipeline')
    assert job['status'] == 'completed'
    assert '切割生产车间日报' in (cfg.reports_dir / job['relative_path']).read_text()
    assert device.workshop.get_daily_report_records('2026-08-18')[1]


def test_directory_routes_use_the_overridden_snapshot_dependency(environment):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api import devices, production_lines
    _, state = environment
    app = FastAPI()
    app.include_router(devices.router)
    app.include_router(production_lines.router)
    app.dependency_overrides[auth.require_user] = lambda: {'role': 'admin'}
    app.dependency_overrides[runtime_state] = lambda: state.database_runtime.current_context

    @app.middleware('http')
    async def request_id(request, call_next):
        request.state.request_id = 'injected-directory'
        return await call_next(request)

    with TestClient(app) as client:
        for path in ('/api/v1/devices', '/api/v1/production-lines'):
            response = client.get(path)
            assert response.status_code == 200, response.text
            assert {item['id'] for item in response.json()['data']['items']} >= set(state.devices)


def test_welding_settings_publish_without_rebuilding_cutting_or_echoing_password(environment):
    cfg, state = environment
    old_request = request(state)
    old = runtime_state(old_request)
    body = payload(data_source_mode='demo', worker_database_auth_enabled=False, file_database_enabled=False,
                   welding={'enabled': True, 'host': 'weld.test', 'port': 3307, 'database': 'weld', 'username': 'readonly', 'password': 'weld-private'})
    database.save_welding_configuration(body.welding, request(state))
    fresh = runtime_state(request(state))
    assert fresh.devices is old.devices
    assert fresh.cutting_dispatch is old.cutting_dispatch
    assert fresh.welding is not old.welding
    assert fresh.welding.settings.weld_db_port == 3307
    assert fresh.welding.repository.client._pool is None
    assert not old.welding.settings.weld_enabled
    assert runtime_state(old_request) is old
    visible = database.get_database_configuration(request(state))['data']['welding']
    assert visible['password_configured'] is True and 'password' not in visible
    body.welding.password = None
    database.save_welding_configuration(body.welding, request(state))
    assert runtime_state(request(state)).settings.weld_db_password == 'weld-private'


def test_welding_only_save_and_reload_preserve_an_incomplete_cutting_profile(environment, monkeypatch):
    cfg, _ = environment
    cfg = replace(cfg, data_source_mode='sqlserver')
    monkeypatch.setattr(database, 'settings', cfg)
    state = build_runtime(cfg, role='web')
    original = database_context(state)
    body = database.WeldingConnectionRequest(enabled=True, host='weld.test', database='weld',
        username='readonly', password='weld-private')
    result = database.save_welding_configuration(body, request(state))
    active = database_context(state)
    assert result['data']['saved'] is True
    assert active.settings.weld_enabled
    assert active.devices is original.devices
    assert active.settings.effective_data_source_mode == 'sqlserver'
    assert active.settings.sqlserver_host == ''
    assert active.welding.repository.client._pool is None
    saved = read_runtime_env(cfg.runtime_settings_env_path)
    assert saved['WELD_ENABLED'] == 'true' and not any(key.startswith('SQLSERVER_') for key in saved)
    worker = build_runtime(cfg, role='worker')
    assert database_context(worker).settings.weld_enabled
    assert worker.database_runtime.inspect()['error'] is None
    body.enabled = False
    body.password = None
    database.save_welding_configuration(body, request(state))
    assert not database_context(state).settings.weld_enabled
    assert database_context(state).settings.weld_db_password == 'weld-private'
    assert not database_context(worker).settings.weld_enabled


def test_unified_save_accepts_only_welding_changes_without_saving_incomplete_cutting_fields(environment, monkeypatch):
    cfg, _ = environment
    cfg = replace(cfg, data_source_mode='sqlserver')
    monkeypatch.setattr(database, 'settings', cfg)
    state = build_runtime(cfg, role='web')
    body = database.DatabaseConfigurationRequest.model_validate(database.database_configuration_data(cfg))
    body.welding = database.WeldingConnectionRequest(enabled=True, host='weld.test', database='weld',
        username='readonly', password='weld-private')
    database.save_database_configuration(body, request(state))
    assert database_context(state).settings.weld_enabled
    assert read_runtime_env(cfg.runtime_settings_env_path)['WELD_ENABLED'] == 'true'
    assert 'APP_DATA_SOURCE_MODE' not in read_runtime_env(cfg.runtime_settings_env_path)
    body.production.host = 'changed-cutting-profile'
    with pytest.raises(HTTPException) as exc:
        database.save_database_configuration(body, request(state))
    assert exc.value.status_code == 422
    assert database_context(state).settings.sqlserver_host == ''


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
    monkeypatch.setattr('app.services.database_runtime.write_runtime_env', lambda *_: (_ for _ in ()).throw(OSError('disk unavailable')))
    with pytest.raises(HTTPException) as error:
        database.save_database_configuration(payload(), request(state))
    assert error.value.status_code == 500
    assert cfg.runtime_settings_env_path.read_bytes() == before
    assert database_context(state) is original


def test_save_uses_runtime_path_and_password_instead_of_global_startup_settings(environment, tmp_path, monkeypatch):
    cfg, _ = environment
    cfg = replace(cfg, sqlserver_password='runtime-password')
    state = build_runtime(cfg, role='web')
    unrelated = replace(cfg, project_root=tmp_path / 'other-app', sqlserver_password='wrong-global-password')
    monkeypatch.setattr(database, 'settings', unrelated)
    body = payload()
    body.production.password = None
    database.save_database_configuration(body, request(state))
    assert read_runtime_env(cfg.runtime_settings_env_path)['SQLSERVER_PASSWORD'] == 'runtime-password'
    assert database_context(state).settings.sqlserver_password == 'runtime-password'
    assert not unrelated.runtime_settings_env_path.exists()


def test_failed_candidate_build_preserves_file_and_published_services(environment, monkeypatch):
    cfg, state = environment
    write_runtime_env(cfg.runtime_settings_env_path, {'CUSTOM_VALUE': 'keep'})
    original = database_context(state)
    before = cfg.runtime_settings_env_path.read_bytes()
    monkeypatch.setattr(state.database_runtime, '_build', lambda _: (_ for _ in ()).throw(RuntimeError('private-source-details')))
    with pytest.raises(HTTPException) as error:
        database.save_database_configuration(payload(), request(state))
    assert error.value.status_code == 503
    assert 'private-source-details' not in error.value.detail
    assert cfg.runtime_settings_env_path.read_bytes() == before
    assert state.database_runtime.current_context is original


def test_welding_connection_test_keeps_embedded_context_and_does_not_save(environment, monkeypatch):
    from contextlib import contextmanager
    from app.database.welding import WeldingClient
    cfg, _ = environment
    cfg = replace(cfg, weld_db_password='embedded-password')
    embedded = SimpleNamespace(settings=cfg)
    seen = []

    @contextmanager
    def connect(client):
        seen.append(client.settings)
        yield SimpleNamespace(fetch=lambda *_, **__: [{'connected': 1}])

    with monkeypatch.context() as patch:
        patch.setattr(WeldingClient, 'snapshot', connect)
        body = database.WeldingConnectionRequest(host='test-weld', username='reader')
        result = database.test_welding_connection(body, request(embedded))
        assert result['data']['connected']
    assert seen[0].weld_enabled and seen[0].weld_db_pool_size == 1
    assert seen[0].weld_db_password == 'embedded-password'
    assert not cfg.runtime_settings_env_path.exists()
    with pytest.raises(HTTPException) as error:
        database.test_welding_connection(database.WeldingConnectionRequest(), request(embedded))
    assert error.value.status_code == 503, 'an incomplete connection retains the existing unavailable response'


def test_diagnostics_distinguishes_hot_welding_timezone_from_startup_timeouts(environment):
    cfg, web = environment
    body = database.WeldingConnectionRequest(enabled=True, host='weld.test', database='weld',
        username='readonly', password='private-weld-password', timezone='UTC')
    database.save_welding_configuration(body, request(web))
    background = build_runtime(cfg, role='worker')
    database_context(background)
    timeout = 121 if cfg.weld_db_query_timeout != 121 else 122
    write_runtime_env(cfg.runtime_settings_env_path, {'WELD_DB_QUERY_TIMEOUT_SECONDS': timeout})
    worker = {'state': 'running', 'owner': 'diagnostic-worker',
              'configuration': background.configuration_diagnostics.observed('diagnostic-worker')}
    report = web.configuration_diagnostics.report(worker)
    rows = {row['key']: row for row in report['items']}
    hot = rows['WELD_DB_TIMEZONE']
    assert hot['status'] == 'effective' and not hot['restart_required']
    assert hot['processes']['web']['value'] == hot['saved_value'] == 'UTC'
    assert hot['processes']['web']['source'].startswith('data/settings.env')
    startup = rows['WELD_DB_QUERY_TIMEOUT_SECONDS']
    assert startup['status'] == 'pending' and startup['restart_required']
    assert 'private-weld-password' not in str(report)


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
    app.state.runtime = state
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
        assert status.status_code == 200
        sources = status.json()['data']['data_sources']
        assert all(item['enabled'] for item in sources if item['source'] != 'welding')
        assert next(item for item in sources if item['source'] == 'welding')['enabled'] is False
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
