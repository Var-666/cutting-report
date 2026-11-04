from tests.postgres_fixtures import state_settings
from dataclasses import FrozenInstanceError, replace
from datetime import date
from types import SimpleNamespace
import gc
import weakref

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from app.api import database
from app.api.runtime import application_state, runtime_state
from app.models.database_sources import DatabaseSourceRequest
from app.services.application_runtime import ApplicationRuntime, build_runtime
from app.services.database_runtime import DatabaseServices, database_context
from app.services.runtime_settings_store import read_runtime_env, write_runtime_env
from tests.line_fixtures import configuration


@pytest.fixture
def environment(postgres_state, tmp_path):
    cfg = configuration(tmp_path)
    return cfg, build_runtime(state_settings(cfg,postgres_state), role='web')


def request(state, line_id='Cutter01'):
    return SimpleNamespace(app=SimpleNamespace(state=state), state=SimpleNamespace(request_id='test-hot-database'),
                           headers={'X-Production-Line': line_id})


def payload(cfg):
    values = database.database_configuration_data(cfg, 'Cutter01')
    values.update(data_source_mode='sqlserver')
    values['production'].update(host='new.test', password='updated-private')
    return database.DatabaseConfigurationRequest.model_validate(values)


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
    app = FastAPI()
    app.state.runtime = state
    original = state.database_runtime.snapshot
    reads = []
    def snapshot():
        reads.append(True)
        return original()
    monkeypatch.setattr(state.database_runtime, 'snapshot', snapshot)
    @app.get('/probe')
    def probe(request: Request, context: DatabaseServices = Depends(runtime_state), runtime: ApplicationRuntime = Depends(application_state)):
        runtime.database_runtime.publish(replace(context, settings=replace(context.settings, app_version='next')), {})
        assert runtime_state(request) is context
        return {'version': context.settings.app_version}
    with TestClient(app) as client:
        assert client.get('/probe').json() == {'version': cfg.app_version}
        assert client.get('/probe').json() == {'version': 'next'}
    assert len(reads) == 2


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
        assert fresh.lines[key].cutting().query_service.client.connection_config()['host'] == 'new.test'
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
                                  {'WORKER_DATABASE_AUTH_ENABLED': 'bad'}, {'APP_DATA_SOURCE_MODE': 'unknown'}])
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
        patch.setattr('app.services.database_runtime.write_runtime_env', lambda *_: (_ for _ in ()).throw(OSError()))
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
