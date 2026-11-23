from tests.runtime_factories import install_runtime
from tests.postgres_fixtures import state_settings
from dataclasses import FrozenInstanceError, replace
from datetime import date
import gc
import weakref

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from tests.web_fixtures import embedded_app, request_for
from fastapi.testclient import TestClient

from app.api import database
from app.api.runtime import CurrentSettings, application_state, request_settings, runtime_state
from app.models.configuration_contracts import ReportCacheConfiguration
from app.models.database_sources import DatabaseSourceRequest
from app.runtime.application import ApplicationRuntime, build_runtime
from app.runtime.database import DatabaseServices, database_context
from app.services.runtime_settings_store import read_runtime_env, write_runtime_env
from tests.line_fixtures import configuration


@pytest.fixture
def environment(postgres_state, tmp_path):
    cfg = configuration(tmp_path)
    return cfg, build_runtime(state_settings(cfg,postgres_state), role='web')


def request(state, line_id='Cutter01'):
    app = FastAPI()
    install_runtime(app, state)
    return request_for(app, headers={'X-Production-Line': line_id}, request_id='test-hot-database')


def payload(cfg):
    values = database.database_configuration_data(cfg, 'Cutter01')
    values.update(data_source_mode='sqlserver')
    values['production'].update(host='new.test', password='updated-private')
    return database.DatabaseConfigurationRequest.model_validate(values)


def test_missing_application_runtime_fails_with_an_assembly_error():
    with pytest.raises(RuntimeError, match="应用运行时未装配"):
        application_state(request_for(FastAPI()))


def test_report_cache_save_hotloads_all_lines_and_preserves_inflight_snapshot(environment):
    from app.models.configuration_contracts import ReportCacheConfiguration
    cfg, runtime = environment
    original = database_context(runtime)
    write_runtime_env(cfg.runtime_settings_env_path, {'CUSTOM': 'keep'})
    result = database.save_report_cache_configuration(ReportCacheConfiguration(
        current_seconds=73, history_seconds=119, query_seconds=0, refresh_seconds=37), request(runtime))
    updated = database_context(runtime)
    assert result['data']['restart_required'] is False
    assert read_runtime_env(cfg.runtime_settings_env_path)['CUSTOM'] == 'keep'
    assert original.settings.leadership_report_cache_seconds == 600
    assert updated.settings.leadership_report_cache_seconds == 73
    for ident, line in updated.lines.items():
        assert line is not original.lines[ident]
        assert line.settings.leadership_report_cache_seconds == 73
        assert line.settings.leadership_report_history_cache_seconds == 119
        assert line.settings.report_data_cache_seconds == 0
        assert line.settings.report_background_refresh_seconds == 37
    assert database.get_report_cache_configuration(request(runtime))['data'] == {
        'current_seconds': 73, 'history_seconds': 119, 'query_seconds': 0, 'refresh_seconds': 37}


@pytest.mark.parametrize('failed_write, status', [('app.runtime.database.write_runtime_env', 500),
    ('app.runtime.database.CacheRevision.publish', 503)])
def test_report_cache_write_failure_keeps_file_and_published_services(environment, monkeypatch, failed_write, status):
    from app.models.configuration_contracts import ReportCacheConfiguration
    cfg, runtime = environment
    original = database_context(runtime)
    write_runtime_env(cfg.runtime_settings_env_path, {'CUSTOM': 'keep'})
    before = cfg.runtime_settings_env_path.read_bytes()
    def fail(*args):
        raise OSError('test write failure')
    monkeypatch.setattr(failed_write, fail)
    with pytest.raises(HTTPException) as error:
        database.save_report_cache_configuration(ReportCacheConfiguration(
            current_seconds=73, history_seconds=119, query_seconds=0, refresh_seconds=37), request(runtime))
    assert error.value.status_code == status
    assert runtime.database_runtime.current_context is original
    assert cfg.runtime_settings_env_path.read_bytes() == before


def test_report_cache_policy_reaches_all_background_roles_at_new_operation_boundary(environment, postgres_state):
    from app.models.configuration_contracts import ReportCacheConfiguration
    cfg, web = environment
    roles = [build_runtime(state_settings(cfg, postgres_state), role=role)
             for role in ('scheduler', 'worker', 'automation')]
    original = [database_context(runtime) for runtime in roles]
    try:
        database.save_report_cache_configuration(ReportCacheConfiguration(
            current_seconds=97, history_seconds=777, query_seconds=0, refresh_seconds=41), request(web))
        for runtime, before in zip(roles, original):
            assert runtime.database_runtime.current_context is before
            updated = database_context(runtime)
            assert before.settings.leadership_report_cache_seconds == 600
            assert updated.settings.leadership_report_cache_seconds == 97
            assert updated.settings.leadership_report_history_cache_seconds == 777
            assert updated.settings.report_data_cache_seconds == 0
            assert updated.settings.report_background_refresh_seconds == 41
            observed = runtime.configuration_diagnostics.observed('test-owner')
            assert observed
    finally:
        for runtime in roles:
            runtime.database_runtime.close()
            runtime.database.close()


def test_runtime_graph_uses_fixed_repositories_and_immutable_snapshot(environment):
    _, runtime = environment
    context = database_context(runtime)
    assert isinstance(runtime, ApplicationRuntime) and isinstance(context, DatabaseServices)
    with pytest.raises(FrozenInstanceError):
        context.settings = context.settings
    for line in context.lines.values():
        if line.config.type != "cutting":
            continue
        device = line.cutting()
        assert device.pipeline.workshop_repository is device.workshop
        assert device.pipeline.range_service.confirmations.scope_machine_no == device.settings.report_machine_no
        assert device.confirmations.database is runtime.database
        assert device.workers == ()


def test_direct_and_injected_dependencies_share_one_request_snapshot(environment, monkeypatch):
    cfg, state = environment
    app = embedded_app(cfg)
    app.state.runtime = state
    original = state.database_runtime.snapshot
    reads = []
    def snapshot():
        reads.append(True)
        return original()
    monkeypatch.setattr(state.database_runtime, 'snapshot', snapshot)
    @app.get('/probe')
    def probe(request: Request, settings: CurrentSettings, save: bool = False,
              context: DatabaseServices = Depends(runtime_state), runtime: ApplicationRuntime = Depends(application_state)):
        if save:
            runtime.database_runtime.save_report_cache(ReportCacheConfiguration(
                current_seconds=73, history_seconds=119, query_seconds=0, refresh_seconds=37))
        assert runtime_state(request) is context
        assert settings is request_settings(request) is context.settings
        return {'seconds': settings.leadership_report_cache_seconds}
    with TestClient(app) as client:
        assert client.get('/probe?save=true').json() == {'seconds': cfg.leadership_report_cache_seconds}
        assert client.get('/probe').json() == {'seconds': 73}
    assert len(reads) == 2


def test_cold_http_capacity_matches_diagnostics_after_save(postgres_state, tmp_path, monkeypatch):
    from fastapi.routing import APIRoute

    from app.main import create_app

    cfg = state_settings(configuration(tmp_path, http_query_max_concurrent=3), postgres_state)
    app = create_app(cfg)

    def probe(settings: CurrentSettings):
        return {'capacity': settings.http_query_max_concurrent}

    app.router.routes.insert(0, APIRoute('/probe', probe, methods=['GET']))
    with TestClient(app) as client:
        runtime = app.state.runtime
        pool = app.state.workloads['query']
        assert pool.sql_capacity is runtime.database_runtime.current_context.database_sources.capacity
        diagnostics = runtime.configuration_diagnostics
        monkeypatch.setitem(diagnostics.process, 'HTTP_QUERY_MAX_CONCURRENT', '3')
        write_runtime_env(cfg.runtime_settings_env_path, {'HTTP_QUERY_MAX_CONCURRENT': '4'})
        read = client.get('/probe').json()['capacity']
        row = next(item for item in diagnostics.report({'state': 'missing'})['items']
                   if item['key'] == 'HTTP_QUERY_MAX_CONCURRENT')
    assert row['processes']['web']['value'] == read == pool.limit == 3
    assert row['saved_value'] == 4 and row['restart_required']
    assert '覆盖进程环境变量' in row['saved_source']


@pytest.mark.parametrize('root_path', ['', '/cutting'])
def test_diagnostics_authentication_uses_published_context_without_reloading(environment, monkeypatch, root_path):
    from app.api.configuration import router
    cfg, state = environment
    app = embedded_app(cfg)
    app.state.runtime = state
    app.include_router(router)
    monkeypatch.setattr(state.sessions, 'user_for_session', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(state.database_runtime, 'snapshot', lambda: pytest.fail('只读诊断不得加载快照'))
    with TestClient(app, root_path=root_path) as client:
        assert client.get(f'{root_path}/api/v1/configuration/diagnostics').status_code == 401


def test_welding_save_uses_selected_instance_and_keeps_other_lines_and_password(environment):
    cfg, state = environment
    old_request = request(state)
    old = runtime_state(old_request)
    body = database.WeldingConnectionRequest(enabled=True, host='weld.test', database='weld', username='reader', password='weld-private')
    database.save_welding_configuration(body, request(state, 'welding'))
    fresh = database_context(state)
    assert all(fresh.lines[key] is old.lines[key] for key in ("Cutter01", "Cutter02"))
    assert fresh.lines['welding'].settings.device_id == 'welding'
    assert fresh.lines['welding'].service.repository.client._pool is None
    assert runtime_state(old_request) is old
    visible = database.get_database_configuration(request(state, 'welding'))['data']['welding']
    assert visible['password_configured'] and 'password' not in visible
    body.password = None
    database.save_welding_configuration(body, request(state, 'welding'))
    assert database_context(state).database_sources.get('welding-production').password.get_secret_value() == 'weld-private'
    with pytest.raises(HTTPException) as error:
        database.save_welding_configuration(body, request(state, 'Cutter01'))
    assert error.value.status_code == 422


def test_cutting_save_rebuilds_bound_services_preserves_old_request_and_shared_capacity(environment):
    cfg, state = environment
    old_request = request(state)
    old = runtime_state(old_request)
    result = database.save_database_configuration(payload(cfg), request(state))
    fresh = database_context(state)
    assert not result['data']['restart_required']
    assert old.settings.effective_data_source_mode == 'demo'
    assert fresh.settings.effective_data_source_mode == 'sqlserver'
    assert runtime_state(old_request) is old
    assert fresh.file_downloads._slots is old.file_downloads._slots
    for key in ('Cutter01', 'Cutter02'):
        assert fresh.lines[key].cutting().query_service.repository.client.connection_config()['host'] == 'new.test'
        assert fresh.lines[key].background.energy_history_worker._completion is old.lines[key].background.energy_history_worker._completion
    saved = read_runtime_env(cfg.runtime_settings_env_path)
    assert 'SQLSERVER_PASSWORD' not in saved and 'WELD_DB_HOST' not in saved
    assert cfg.runtime_settings_env_path.stat().st_mode & 0o777 == 0o600
    assert 'private' not in str(database.get_database_configuration(request(state)))


def test_worker_sees_same_published_values_but_diagnostics_does_not_reload(postgres_state, environment, monkeypatch):
    cfg, web = environment
    write_runtime_env(cfg.runtime_settings_env_path, {'PRODUCTION_LINES_JSON': cfg.production_lines_json, 'DATABASE_SOURCES_JSON': cfg.database_sources_json})
    worker = build_runtime(state_settings(cfg,postgres_state), role='worker')
    old = database_context(worker)
    database.save_database_configuration(payload(cfg), request(web))
    assert worker.database_runtime.current_context is old
    assert database_context(worker).settings.effective_data_source_mode == 'sqlserver'
    monkeypatch.setattr(web.database_runtime, 'snapshot', lambda: pytest.fail('diagnostics cannot reload'))
    observed = worker.configuration_diagnostics.observed('worker-owner')
    report = web.configuration_diagnostics.report({'state': 'running', 'owner': 'worker-owner', 'configuration': observed})
    keys = {'PRODUCTION_LINES_JSON', 'DATABASE_SOURCES_JSON', 'APP_DATA_SOURCE_MODE'}
    rows = [row for row in report['items'] if row['key'] in keys]
    assert len(rows) == len(keys)
    assert all(row['processes']['web']['matches_saved'] for row in rows)
    assert 'private' not in str(report) and 'digest' not in str(report)


@pytest.mark.parametrize('values', [{'PRODUCTION_LINES_JSON': 'bad'}, {'DATABASE_SOURCES_JSON': 'bad'},
                                  {'WORKER_DATABASE_AUTH_ENABLED': 'bad'}, {'APP_DATA_SOURCE_MODE': 'unknown'},
                                  {'LLM_MODEL': 'x' * 257}, {'LEADERSHIP_REPORT_CACHE_SECONDS': '-1'},
                                  {'LEADERSHIP_REPORT_HISTORY_CACHE_SECONDS': 'bad'}, {'REPORT_DATA_CACHE_SECONDS': '2147483648'}])
def test_invalid_reload_keeps_last_snapshot_and_recovers(environment, values):
    cfg, state = environment
    old = database_context(state)
    write_runtime_env(cfg.runtime_settings_env_path, values)
    assert database_context(state) is old
    assert state.database_runtime.inspect()['error']
    cfg.runtime_settings_env_path.unlink()
    assert database_context(state) is old
    assert state.database_runtime.inspect()['error'] is None


def test_failed_candidate_or_disk_write_keeps_published_graph_and_file(environment, monkeypatch):
    cfg, state = environment
    original = database_context(state)
    write_runtime_env(cfg.runtime_settings_env_path, {'CUSTOM': 'keep'})
    before = cfg.runtime_settings_env_path.read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(state.database_runtime, '_build', lambda *_: (_ for _ in ()).throw(RuntimeError('private-details')))
        with pytest.raises(HTTPException) as error:
            database.save_database_configuration(payload(cfg), request(state))
        assert error.value.status_code == 503 and 'private-details' not in error.value.detail
    with monkeypatch.context() as patch:
        patch.setattr('app.runtime.database.write_runtime_env', lambda *_: (_ for _ in ()).throw(OSError()))
        with pytest.raises(HTTPException) as error:
            database.save_database_configuration(payload(cfg), request(state))
        assert error.value.status_code == 500
    assert state.database_runtime.current_context is original
    assert cfg.runtime_settings_env_path.read_bytes() == before


def test_replaced_mysql_client_lives_until_last_operation_reference_is_released(postgres_state, tmp_path):
    cfg = configuration(tmp_path, welding=True)
    runtime = build_runtime(state_settings(cfg,postgres_state), role='web')
    old = database_context(runtime)
    client = old.lines['welding'].service.repository.client
    reference = weakref.ref(client)
    body = DatabaseSourceRequest.model_validate({**old.database_sources.get('welding-production').model_dump(), 'host': 'new.test'})
    runtime.database_runtime.save_source(body)
    assert not client._closed and reference() is client
    del client, old
    gc.collect()
    assert reference() is None


def test_shared_connection_edits_merge_once_and_conflicts_do_not_publish(postgres_state, tmp_path):
    cfg = configuration(tmp_path, real=True, worker_source_key='cutting-production', file_source_key='cutting-production')
    state = build_runtime(state_settings(cfg,postgres_state), role='web')
    values = database.database_configuration_data(cfg, 'Cutter01')
    values['production']['host'] = 'new-shared.test'
    state.database_runtime.save_configuration(database.DatabaseConfigurationRequest.model_validate(values), line_id='Cutter01')
    fresh = database_context(state)
    assert fresh.database_sources.get('cutting-production').host == 'new-shared.test'
    values = database.database_configuration_data(fresh.settings, 'Cutter01')
    values['production']['host'] = 'one.test'
    values['worker']['host'] = 'another.test'
    before = cfg.runtime_settings_env_path.read_bytes()
    with pytest.raises(ValueError, match='冲突'):
        state.database_runtime.save_configuration(database.DatabaseConfigurationRequest.model_validate(values), line_id='Cutter01')
    assert database_context(state) is fresh
    assert cfg.runtime_settings_env_path.read_bytes() == before
