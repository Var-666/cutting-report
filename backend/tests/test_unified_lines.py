from tests.postgres_fixtures import state_settings
from dataclasses import replace
from datetime import date
import gc
import json
from pathlib import Path
from types import SimpleNamespace
import weakref

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.database.source_registry import DatabaseSourceRegistry, SourceConflict
from app.device_config import ProductionLineCatalog, load_line_catalog
from app.models.database_sources import DatabaseSourceRequest
from app.models.welding import WeldingQuery
from app.services.application_runtime import build_runtime
from app.services.database_runtime import database_context
from app.services.production_lines import supports_path, registered_lines
from app.services.runtime_settings_store import RuntimeSettingsSaveError, read_runtime_env, write_runtime_env
from app.worker import WorkerProcess
from app.scheduler import SchedulerProcess
from tests.line_fixtures import configuration


def test_new_instances_share_resources_without_sharing_scopes_or_snapshots(postgres_state, tmp_path):
    cfg = configuration(tmp_path, real=True, welding=True)
    catalog = load_line_catalog(cfg.production_lines_json)
    cutter = catalog.get('Cutter02').model_copy(update={'id': 'Cutter03', 'device_id': 'physical-3', 'machine_no': 'Cutter03'})
    weld = catalog.get('welding').model_copy(update={'id': 'welding-2', 'source_key': 'mysql-2'})
    configs = DatabaseSourceRegistry.from_json(cfg.database_sources_json).configs()
    configs.append(configs[-1].model_copy(update={'id': 'mysql-2', 'host': 'mysql-2.test'}))
    from app.database.source_registry import source_configs_json
    catalog = catalog.model_copy(update={'lines': (*catalog.lines, cutter, weld)})
    cfg = replace(cfg, production_lines_json=catalog.model_dump_json(), database_sources_json=source_configs_json(configs))
    runtime = build_runtime(state_settings(cfg,postgres_state), role='web')
    context = database_context(runtime)
    try:
        assert set(context.lines) == {'Cutter01', 'Cutter02', 'Cutter03', 'welding', 'welding-2'}
        one, three = (context.lines[key].cutting() for key in ('Cutter01', 'Cutter03'))
        assert one is not three and one.query_service.client is three.query_service.client
        assert one.settings.report_machine_no == 'Cutter01' and three.settings.report_machine_no == 'Cutter03'
        assert one.metadata.device_id == 'Cutter01' and three.metadata.device_id == 'Cutter03'
        assert one.report_provider.source_identity() != three.report_provider.source_identity()
        first, second = (context.lines[key].service for key in ('welding', 'welding-2'))
        assert first is not second and first.repository.client is not second.repository.client
        query = WeldingQuery(start=date(2026, 10, 1), end=date(2026, 10, 1))
        assert first._snapshot_key(query, 'daily').values != second._snapshot_key(query, 'daily').values
        assert supports_path(context, 'welding-2', '/api/v1/welding/dashboard')
        assert not supports_path(context, 'welding-2', '/api/v1/alarms/status')
        assert not hasattr(context, 'devices') and not hasattr(context, 'welding')
        assert not hasattr(runtime, 'devices') and not hasattr(runtime, 'welding')
    finally:
        runtime.database_runtime.close()


def test_source_change_rebuilds_only_bound_lines_and_worker_loads_it_at_new_operation(postgres_state, tmp_path):
    cfg = configuration(tmp_path, real=True, welding=True)
    web, worker = (build_runtime(state_settings(cfg,postgres_state), role=role) for role in ('web', 'worker'))
    old = database_context(web)
    old_worker = database_context(worker)
    payload = DatabaseSourceRequest.model_validate({**old.database_sources.get('cutting-production').model_dump(), 'host': 'new.test'})
    web.database_runtime.save_source(payload)
    fresh = database_context(web)
    try:
        assert fresh.lines['Cutter01'] is not old.lines['Cutter01']
        assert fresh.lines['Cutter02'] is not old.lines['Cutter02']
        assert fresh.lines['welding'] is old.lines['welding']
        assert old.lines['Cutter01'].cutting().query_service.client.connection_config()['host'] == 'cutting.test'
        assert fresh.lines['Cutter01'].cutting().query_service.client.connection_config()['host'] == 'new.test'
        assert fresh.lines['Cutter01'].report_provider.source_identity() != old.lines['Cutter01'].report_provider.source_identity()
        assert worker.database_runtime.current_context is old_worker
        assert database_context(worker).lines['Cutter01'].settings.source_fingerprint == fresh.lines['Cutter01'].settings.source_fingerprint
        process = WorkerProcess(cfg, web)
        called = []
        fresh.lines['Cutter01'].background.handle_background = lambda *args: called.append(args)
        with pytest.raises(RuntimeError, match='来源'):
            process._handle({'kind': 'current_refresh', 'device_id': 'Cutter01', 'payload': {'source_fingerprint': old.lines['Cutter01'].settings.source_fingerprint}})
        assert not called
        process._handle({'kind': 'current_refresh', 'device_id': 'Cutter01', 'payload': {'source_fingerprint': fresh.lines['Cutter01'].settings.source_fingerprint}})
        assert len(called) == 1
    finally:
        web.database_runtime.close()
        worker.database_runtime.close()


def test_invalid_bindings_and_failed_save_preserve_file_and_snapshot(postgres_state, tmp_path, monkeypatch):
    cfg = configuration(tmp_path, real=True)
    runtime = build_runtime(state_settings(cfg,postgres_state), role='web')
    current = database_context(runtime)
    write_runtime_env(cfg.runtime_settings_env_path, {'CUSTOM': 'keep'})
    before = cfg.runtime_settings_env_path.read_bytes()
    for action in (lambda: runtime.database_runtime.delete_source('cutting-production'),
                   lambda: runtime.database_runtime.set_source_enabled('cutting-production', False)):
        with pytest.raises(ValueError):
            action()
        assert runtime.database_runtime.current_context is current
        assert cfg.runtime_settings_env_path.read_bytes() == before
    monkeypatch.setattr('app.services.database_runtime.write_runtime_env', lambda *_: (_ for _ in ()).throw(OSError()))
    payload = DatabaseSourceRequest.model_validate({**current.database_sources.get('cutting-production').model_dump(), 'host': 'new.test'})
    with pytest.raises(RuntimeSettingsSaveError):
        runtime.database_runtime.save_source(payload)
    assert runtime.database_runtime.current_context is current
    assert cfg.runtime_settings_env_path.read_bytes() == before


def test_shared_mysql_scope_is_required_and_bound_in_sql(postgres_state, tmp_path):
    from app.database.welding import WeldingRepository
    cfg = configuration(tmp_path, welding=True)
    catalog = load_line_catalog(cfg.production_lines_json)
    first = catalog.get('welding')
    second = first.model_copy(update={'id': 'welding-2'})
    with pytest.raises(ValidationError):
        ProductionLineCatalog(lines=(*catalog.lines, second))
    first, second = first.model_copy(update={'device_nos': (0,)}), second.model_copy(update={'device_nos': (2,)})
    ProductionLineCatalog(lines=(*catalog.lines[:-1], first, second))
    repo = WeldingRepository(SimpleNamespace(), device_nos=(0,))
    query = WeldingQuery(start=date(2026, 10, 1), end=date(2026, 10, 1), device_no=2)
    sql, values = repo.conditions(query)
    assert 'j.device_no IN (%s)' in sql and 'j.device_no = %s' in sql
    assert values[2:4] == (0, 2)
    sql, values = repo.part_conditions(query)
    assert 'j.device_no IN (%s)' in sql and 0 in values
    catalog = load_line_catalog(cfg.production_lines_json)
    catalog = catalog.model_copy(update={'lines': (*catalog.lines[:-1], first, second)})
    runtime = build_runtime(state_settings(replace(cfg, production_lines_json=catalog.model_dump_json()),postgres_state), role='web')
    context = database_context(runtime)
    a, b = (context.lines[key].service for key in ('welding', 'welding-2'))
    assert a.repository.client is b.repository.client
    assert a.repository.device_nos == (0,) and b.repository.device_nos == (2,)
    assert a.source_identity() != b.source_identity()
    assert a._snapshot_key(query, 'daily').values != b._snapshot_key(query, 'daily').values


def test_catalog_reload_preserves_stable_ids_and_exposes_disabled_instance(postgres_state, tmp_path):
    cfg = configuration(tmp_path, welding=True)
    runtime = build_runtime(state_settings(cfg,postgres_state), role='web')
    original = database_context(runtime)
    changed = original.catalog.model_copy(update={'lines': tuple(line.model_copy(update={'enabled': False}) if line.id == 'welding' else line for line in original.catalog.lines)})
    runtime.database_runtime.save_lines(changed)
    fresh = database_context(runtime)
    assert not next(line for line in registered_lines(fresh) if line['id'] == 'welding')['enabled']
    assert not fresh.lines['welding'].config.enabled
    assert fresh.lines['Cutter01'] is original.lines['Cutter01']
    with pytest.raises(ValueError):
        runtime.database_runtime.save_lines(changed.model_copy(update={'lines': changed.lines[:-1]}))


def test_missing_new_config_fails_instead_of_loading_legacy_directory(postgres_state, tmp_path):
    with pytest.raises(ValueError, match='migrate_production_lines'):
        build_runtime(state_settings(Settings(project_root=tmp_path, production_lines_json=''),postgres_state), role='web')


def test_new_cutting_and_welding_instances_hotload_in_both_processes(postgres_state, tmp_path):
    from app.database.source_registry import source_configs_json
    cfg = configuration(tmp_path, real=True, welding=True)
    web, worker = (build_runtime(state_settings(cfg,postgres_state), role=role) for role in ('web', 'worker'))
    original = database_context(web)
    registry = original.database_sources
    for old_key, new_key in (('cutting-production', 'sql-3'), ('welding-production', 'mysql-2')):
        source = registry.get(old_key)
        payload = DatabaseSourceRequest.model_validate({**source.model_dump(), 'id': new_key, 'host': new_key + '.test'})
        web.database_runtime.save_source(payload, create=True)
    catalog = database_context(web).catalog
    cutter = catalog.get('Cutter02').model_copy(update={'id': 'Cutter03', 'device_id': 'physical-3', 'machine_no': 'Cutter03', 'source_key': 'sql-3'})
    weld = catalog.get('welding').model_copy(update={'id': 'weld-2', 'source_key': 'mysql-2'})
    web.database_runtime.save_lines(catalog.model_copy(update={'lines': (*catalog.lines, cutter, weld)}))
    for runtime in (web, worker):
        context = database_context(runtime)
        assert {'Cutter03', 'weld-2'} <= context.lines.keys()
        assert context.lines['Cutter03'].cutting().query_service.client.connection_config()['host'] == 'sql-3.test'
        assert context.lines['weld-2'].service.repository.client.config.host == 'mysql-2.test'
        process = SchedulerProcess(context.settings, runtime)
        process._schedule()
        with runtime.coordination.connect() as connection:
            queued = {row['device_id'] for row in connection.execute('SELECT device_id FROM background_tasks')}
        assert {'Cutter03', 'weld-2'} <= queued
    assert database_context(web).lines['Cutter01'] is original.lines['Cutter01']


def test_disabled_line_keeps_historical_report_access_and_rejects_new_work(postgres_state, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.auth import require_admin
    from app.api.reports import router
    cfg = configuration(tmp_path)
    runtime = build_runtime(state_settings(cfg,postgres_state), role='web')
    original = database_context(runtime)
    original.lines['Cutter01'].cutting().metadata.create_job(report_id='historical', report_type='equipment',
        report_date='2026-11-01', project_code=None, ship_no=None, request_id='old', created_at='2026-11-01T08:00:00+08:00')
    catalog = original.catalog.model_copy(update={'lines': tuple(line.model_copy(update={'enabled': False}) if line.id == 'Cutter01' else line for line in original.catalog.lines)})
    runtime.database_runtime.save_lines(catalog)
    app = FastAPI()
    app.state.runtime = runtime
    app.include_router(router)
    app.dependency_overrides[require_admin] = lambda: {'role': 'admin'}
    @app.middleware('http')
    async def context(request, call_next):
        request.state.request_id = 'history'
        return await call_next(request)
    with TestClient(app) as client:
        response = client.get('/api/v1/reports?device_id=Cutter01')
        assert response.status_code == 200
        assert response.json()['data']['items'][0]['report_id'] == 'historical'
        response = client.post('/api/v1/reports?device_id=Cutter01', json={'report_date': '2026-11-01', 'report_type': 'equipment'})
        assert response.status_code == 503


def test_analysis_latest_and_version_remain_bound_to_operation_source_after_switch(postgres_state, tmp_path):
    runtime = build_runtime(state_settings(configuration(tmp_path),postgres_state), role='web')
    before = database_context(runtime)
    old = before.lines['Cutter01'].cutting().metadata
    query = dict(report_type='workshop', report_date='2026-11-01', model_name='test-model', prompt_version='test-prompt')
    def publish(repository, key, value):
        repository.save_analysis(cache_key=key, **query, input_hash='input', data_version=value, status='success', mode='model',
            result={'overview': value}, generated_at='2026-11-01T08:00:00+08:00', publish_latest=True)
        repository.save_analysis_source_version(report_type='workshop', report_date='2026-11-01', data_version=value,
                                                updated_at='2026-11-01T08:00:00+08:00')
    publish(old, 'old-source', 'old-version')
    payload = DatabaseSourceRequest.model_validate({**before.database_sources.get('cutting-production').model_dump(), 'host': 'new.test'})
    runtime.database_runtime.save_source(payload)
    fresh = database_context(runtime).lines['Cutter01'].cutting().metadata
    assert fresh.device_id == old.device_id == 'Cutter01'
    assert fresh.get_latest_analysis(**query) is None
    assert fresh.get_analysis_source_version(report_type='workshop', report_date='2026-11-01') is None
    publish(old, 'old-still-running', 'late-old-result')
    assert fresh.get_latest_analysis(**query) is None
    publish(fresh, 'new-source', 'new-version')
    assert fresh.get_latest_analysis(**query)['overview'] == 'new-version'
    assert old.get_latest_analysis(**query)['overview'] == 'late-old-result'


def test_fresh_example_bootstraps_explicit_demo_without_legacy_config(postgres_state, tmp_path):
    from app.config import _environment
    from app.configuration_sources import read_env_file
    example = Path(__file__).resolve().parents[2] / '.env.example'
    values, errors = read_env_file(example, environment={}, interpolate=True)
    assert not errors
    token = _environment.set(values)
    try:
        cfg = Settings(project_root=tmp_path)
    finally:
        _environment.reset(token)
    runtime = build_runtime(state_settings(cfg,postgres_state), role='web')
    context = database_context(runtime)
    assert context.settings.effective_data_source_mode == 'demo'
    assert set(context.lines) == {'Cutter01', 'Cutter02', 'welding'}
    assert context.lines['Cutter01'].cutting().query_service.client is None
    assert not context.lines['welding'].config.enabled


def test_completion_date_upgrade_isolates_old_analysis_in_the_bound_source(state_database, postgres_state, tmp_path):
    from app.database.metadata import MetadataRepository
    cfg = configuration(tmp_path)
    from app.database.source_registry import DatabaseSourceRegistry
    scope = 'Cutter01:' + DatabaseSourceRegistry.from_json(cfg.database_sources_json).fingerprint('cutting-production')
    legacy = MetadataRepository(state_database, device_id='Cutter01', analysis_scope=scope)
    query = dict(report_type='workshop', report_date='2026-11-01', model_name='test', prompt_version='test')
    legacy.save_analysis(cache_key='legacy-analysis', **query, input_hash='input', data_version='old-version',
        status='success', mode='model', result={'overview': '保留已有分析'}, generated_at='2026-11-01T08:00:00+08:00', publish_latest=True)
    legacy.save_analysis_source_version(report_type='workshop', report_date='2026-11-01', data_version='old-version', updated_at='2026-11-01T08:00:00+08:00')
    for _ in range(2):
        runtime = build_runtime(state_settings(cfg,postgres_state), role='web')
        selected = database_context(runtime).lines['Cutter01'].cutting().metadata
        assert selected.get_latest_analysis(**query) is None
        assert selected.get_analysis_source_version(report_type='workshop', report_date='2026-11-01') is None
        assert legacy.get_latest_analysis(**query)['overview'] == '保留已有分析'
        runtime.database_runtime.close()



def test_welding_capabilities_and_api_paths_keep_cutting_actions_unavailable(tmp_path):
    from types import SimpleNamespace
    cfg = configuration(tmp_path, welding=True)
    context = SimpleNamespace(catalog=load_line_catalog(cfg.production_lines_json))
    line = next(item for item in registered_lines(context) if item['type'] == 'welding')
    assert line['capabilities'] == ['reports', 'analysis', 'settings']
    assert supports_path(context, line['id'], '/api/v1/welding/dashboard')
    assert supports_path(context, line['id'], '/api/v1/welding/analysis', 'POST')
    assert not supports_path(context, line['id'], '/api/v1/alarms/status')
    assert not supports_path(context, line['id'], '/api/v1/downloads', 'POST')
