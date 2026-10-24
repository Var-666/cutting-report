import json
import os
from dataclasses import fields, replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.auth import require_user
from app.api.configuration import router
from app.config import DEVICE_CATALOG, Settings, _environment
from app.configuration_sources import load_environment
from app.services.automation_runtime import AutomationRuntime
from app.services.configuration_diagnostics import ConfigurationDiagnostics
from app.services.runtime_settings_store import write_runtime_env


@pytest.fixture
def configured(tmp_path):
    path = tmp_path / 'backend/app/configuration/devices.json'
    path.parent.mkdir(parents=True)
    path.write_text(DEVICE_CATALOG.model_dump_json(), encoding='utf-8')

    def create(process=None):
        values, sources, _ = load_environment(tmp_path, process or {})
        token = _environment.set(values)
        try:
            settings = Settings(project_root=tmp_path)
        finally:
            _environment.reset(token)
        runtime = AutomationRuntime(settings)
        runtime._sources = {item.name: sources.get(item.metadata['env'], 'default')
                            for item in fields(Settings) if 'env' in item.metadata}
        diagnostics = ConfigurationDiagnostics(settings, runtime, process=process or {}, sources=sources)
        return diagnostics
    return create


def worker(diagnostics, owner='test-worker'):
    return {'state': 'running', 'owner': owner, 'configuration': diagnostics.observed(owner)}


def item(report, key):
    return next(row for row in report['items'] if row['key'] == key)


def test_priority_interpolation_null_empty_and_no_environment_mutation(tmp_path, monkeypatch):
    (tmp_path / 'data').mkdir()
    (tmp_path / '.env').write_text('VALUE=base\nBASE=base\nDERIVED=${BASE}\nONLY_BASE=yes\nNULL\n')
    (tmp_path / 'data/settings.env').write_text("VALUE='saved'\nLITERAL='${BASE}'\nEMPTY=''\nNULL\n")
    before = dict(os.environ)
    values, sources, errors = load_environment(tmp_path, {'VALUE': 'process', 'BASE': 'process'})
    assert not errors
    assert values['VALUE'] == 'saved' and sources['VALUE'] == 'data/settings.env'
    assert values['BASE'] == values['DERIVED'] == 'process'
    assert sources['BASE'] == 'process' and sources['DERIVED'] == '.env'
    assert values['LITERAL'] == '${BASE}' and values['EMPTY'] == '' and 'NULL' not in values
    assert os.environ == before


def test_database_pending_then_restart_and_sources_stay_actual(configured, tmp_path):
    (tmp_path / '.env').write_text('SQLSERVER_HOST=base\nLLM_MAX_CONCURRENT=4\n')
    process = {'SQLSERVER_HOST': 'process'}
    web = configured(process)
    write_runtime_env(web.settings.runtime_settings_env_path, {'SQLSERVER_HOST': 'saved'})
    result = item(web.report(worker(web)), 'SQLSERVER_HOST')
    assert result['status'] == 'pending' and result['restart_required']
    assert result['processes']['web']['source'] == '进程环境变量'
    assert result['saved_source'].startswith('data/settings.env')
    restarted = configured(process)
    result = item(restarted.report(worker(restarted)), 'SQLSERVER_HOST')
    assert result['status'] == 'effective' and not result['restart_required']
    assert result['processes']['web']['source'].startswith('data/settings.env')
    assert item(web.report(worker(web)), 'LLM_MAX_CONCURRENT')['processes']['web']['source'] == '.env'
    assert item(web.report(worker(web)), 'ALARM_RETENTION_DAYS')['processes']['web']['source'] == '程序默认值'


def test_diagnostics_does_not_reload_hot_settings_or_write_files(configured, tmp_path, monkeypatch):
    web, background = configured(), configured()
    path = web.settings.runtime_settings_env_path
    write_runtime_env(path, {'SMTP_HOST': 'new-smtp', 'SMTP_PASSWORD': 'new-password'})
    previous = web.runtime.inspect()
    before = {path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    monkeypatch.setattr(web.runtime, 'snapshot', lambda: pytest.fail('diagnostics must not reload'))
    assert item(web.report(worker(background)), 'SMTP_PASSWORD')['status'] == 'error'
    assert web.runtime.inspect() == previous
    assert before == {path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    background.runtime.snapshot()
    web.runtime.apply(source='data/settings.env', smtp_host='new-smtp', smtp_password='new-password')
    result = item(web.report(worker(background)), 'SMTP_PASSWORD')
    assert result['status'] == 'effective'
    assert result['processes']['worker']['source'].startswith('data/settings.env')


def test_worker_versions_are_independent_and_missing_stale_is_not_effective(configured):
    web, background = configured(), configured()
    background.settings = replace(background.settings, llm_max_concurrent=3)
    result = web.report(worker(background))
    row = item(result, 'LLM_MAX_CONCURRENT')
    assert row['status'] == 'error' and row['process_mismatch']
    assert row['processes']['web']['status'] == 'effective'
    assert row['processes']['worker']['status'] == 'pending'
    assert result['processes']['web']['version'] != result['processes']['worker']['version']
    for state in ['missing', 'lost', 'stopped', 'unavailable']:
        report = web.report({**worker(background), 'state': state})
        assert not report['processes']['worker']['available']
        assert item(report, 'LLM_MAX_CONCURRENT')['processes']['worker']['value'] == '未知'


def test_all_devices_catalog_and_global_gating_share_same_rules(configured, tmp_path):
    web = configured({'ENERGY_API_ENABLED': 'true'})
    report = web.report(worker(web))
    for device in DEVICE_CATALOG.devices:
        assert item(report, f'devices.{device.id}.machine_no')['processes']['web']['value'] == device.machine_no
        row = item(report, f'devices.{device.id}.energy_api_enabled')
        assert row['processes']['web']['value'] == (device.energy is not None)
        assert 'devices.json' in row['processes']['web']['source']
    path = tmp_path / 'backend/app/configuration/devices.json'
    payload = json.loads(path.read_text())
    payload['devices'][1]['name'] = '测试名称'
    path.write_text(json.dumps(payload))
    assert item(web.report(worker(web)), 'devices.Cutter02.name')['status'] == 'pending'
    path.write_text('{broken}')
    report = web.report(worker(web))
    assert report['errors'] and item(report, 'DEVICE_CONFIG_PATH')['status'] == 'error'


@pytest.mark.parametrize(('key', 'value'), [('LLM_MAX_CONCURRENT', '11'), ('APP_PORT', 'bad-secret'),
    ('SMTP_PORT', 'nan'), ('SMTP_ENABLED', 'invalid-secret'), ('SMTP_PORT', '70000'),
    ('SMTP_TIMEOUT_SECONDS', 'nan'), ('ENERGY_API_TIMEOUT_SECONDS', 'inf'), ('APP_DATA_SOURCE_MODE', 'excel')])
def test_invalid_parameters_are_reported_without_raw_input(configured, key, value):
    web = configured()
    write_runtime_env(web.settings.runtime_settings_env_path, {key: value})
    report = web.report(worker(web))
    assert item(report, key)['status'] == 'error'
    assert not item(report, key)['valid']
    assert 'bad-secret' not in json.dumps(report) and 'invalid-secret' not in json.dumps(report)


def test_malformed_file_and_legacy_conflict_never_log_values(configured, caplog, tmp_path):
    web = configured()
    write_runtime_env(web.settings.runtime_settings_env_path, {'REPORT_MACHINE_NO': 'secret-conflict'})
    assert web.report(worker(web))['errors']
    web.settings.runtime_settings_env_path.write_text('SMTP_PASSWORD="secret-unclosed\n')
    report = web.report(worker(web))
    web.runtime.snapshot()
    assert report['errors'] and web.runtime.inspect()['error']
    assert 'secret-unclosed' not in json.dumps(report) + caplog.text
    assert 'secret-conflict' not in json.dumps(report) + caplog.text


def test_invalid_hot_configuration_keeps_last_usable_runtime(configured):
    web = configured()
    write_runtime_env(web.settings.runtime_settings_env_path, {'SMTP_PORT': '70000'})
    previous = web.runtime.inspect()['settings']
    assert web.runtime.snapshot()[0] == previous
    assert web.runtime.inspect()['error']
    write_runtime_env(web.settings.runtime_settings_env_path, {'SMTP_PORT': '465'})
    assert web.runtime.snapshot()[0].smtp_port == 465
    assert web.runtime.inspect()['error'] is None


def test_sensitive_values_urls_and_digests_never_leave_api(configured):
    secrets = {'SQLSERVER_PASSWORD': 'secret-db', 'SQLSERVER_USERNAME': 'secret-user',
               'LLM_API_KEY': 'secret-key', 'LLM_BASE_URL': 'https://secret-user:secret-pass@server/?token=secret-query',
               'FILE_HTTP_COOKIE': 'secret-cookie', 'FILE_HTTP_AUTHORIZATION': 'secret-auth',
               'SMTP_PASSWORD': 'secret-smtp', 'SQL_WORKBENCH_ADMIN_TOKEN': 'secret-token'}
    web = configured(secrets)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_user] = lambda: {'role': 'admin'}
    app.state.workshop = SimpleNamespace(user_for_session=lambda *_, **__: None)
    app.state.configuration_diagnostics = web
    app.state.worker_status_reader = SimpleNamespace(status=lambda: worker(web))

    @app.middleware('http')
    async def request_id(request, call_next):
        request.state.request_id = 'test'
        return await call_next(request)

    with TestClient(app) as client:
        response = client.get('/api/v1/configuration/diagnostics')
        assert response.status_code == 200
        assert 'digest' not in response.text and 'owner' not in response.text
        for value in secrets.values():
            assert value not in response.text
        for key in secrets:
            assert item(response.json()['data'], key)['processes']['web']['value'] == '已配置'
        app.dependency_overrides[require_user] = lambda: {'role': 'worker'}
        assert client.get('/api/v1/configuration/diagnostics').status_code == 403
        app.dependency_overrides.clear()
        assert client.get('/api/v1/configuration/diagnostics').status_code in {401, 503}
        assert client.put('/api/v1/configuration/diagnostics', json={}).status_code == 405


def test_diagnostic_failure_is_sanitized(configured):
    from app.api.configuration import configuration_diagnostics
    from fastapi import HTTPException
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        worker_status_reader=SimpleNamespace(status=lambda: (_ for _ in ()).throw(ValueError('secret'))))))
    with pytest.raises(HTTPException) as error:
        configuration_diagnostics(request)
    assert error.value.status_code == 503 and 'secret' not in error.value.detail


def test_secondary_connections_show_actual_fallback_values_and_sources(configured, tmp_path):
    (tmp_path / '.env').write_text('SQLSERVER_PORT=1444\nSQLSERVER_DRIVER=Custom Driver\n')
    web = configured()
    report = web.report(worker(web))
    row = item(report, 'connections.worker.port')
    assert row['processes']['web']['value'] == 1444
    assert row['processes']['web']['source'] == '.env'
    assert item(report, 'connections.worker.uses_primary_fallback')['processes']['web']['value'] is True
    assert item(report, 'APP_EFFECTIVE_DATA_SOURCE_MODE')['processes']['web']['value'] == 'demo'
    write_runtime_env(web.settings.runtime_settings_env_path, {'WORKER_SQLSERVER_HOST': 'dedicated', 'WORKER_SQLSERVER_PORT': '1555'})
    restarted = configured()
    report = restarted.report(worker(restarted))
    assert item(report, 'connections.worker.port')['processes']['web']['value'] == 1555
    assert item(report, 'connections.worker.driver')['processes']['web']['source'] == '.env'
    assert item(report, 'connections.worker.uses_primary_fallback')['processes']['web']['value'] is False


def test_loader_preserves_dotenv_disable_switch(tmp_path):
    (tmp_path / 'data').mkdir()
    (tmp_path / '.env').write_text('VALUE=base\n')
    (tmp_path / 'data/settings.env').write_text('VALUE=saved\n')
    values, sources, errors = load_environment(tmp_path, {'PYTHON_DOTENV_DISABLED': 'true', 'VALUE': 'process'})
    assert values['VALUE'] == 'process' and sources['VALUE'] == 'process' and not errors


def test_health_never_exposes_internal_configuration_fingerprints(configured):
    import anyio
    from app.main import health
    web = configured()
    state = SimpleNamespace(worker_status_reader=SimpleNamespace(status=lambda: worker(web)))
    request = SimpleNamespace(app=SimpleNamespace(state=state), state=SimpleNamespace(request_id='health'))
    response = anyio.run(health, request)
    assert 'configuration' not in response['data']['workers']
    assert 'digest' not in json.dumps(response)


def test_worker_heartbeat_uses_its_own_snapshot_and_diagnostic_failure_is_isolated(configured):
    from app.worker import WorkerProcess
    web, background = configured(), configured()
    saved = []
    runtime = SimpleNamespace(coordination=SimpleNamespace(
        task_counts=lambda: {}, renew=lambda *args, **kwargs: saved.append(kwargs['status']) or True),
        devices={}, configuration_diagnostics=background)
    process = WorkerProcess(background.settings, runtime=runtime)
    process.owner = 'test-worker'
    process._heartbeat()
    assert saved[-1]['configuration']['version'] == background.observed(process.owner)['version']
    assert saved[-1]['configuration']['version'] != web.observed(process.owner)['version']
    background.observed = lambda _: (_ for _ in ()).throw(ValueError('secret-diagnostic-error'))
    process._heartbeat()
    assert saved[-1]['configuration'] == {'error': '配置快照暂不可用'}


def test_example_defaults_only_choose_demo_mode_explicitly():
    from app.configuration_sources import read_env_file
    example = Path(__file__).resolve().parents[2] / '.env.example'
    values, errors = read_env_file(example, environment={}, interpolate=True)
    assert not errors
    differences = set()
    for field in fields(Settings):
        if 'env' not in field.metadata or field.metadata['env'] not in values:
            continue
        token = _environment.set({})
        try:
            default = field.default_factory()
        finally:
            _environment.reset(token)
        token = _environment.set(values)
        try:
            configured = field.default_factory()
        finally:
            _environment.reset(token)
        if default != configured:
            differences.add(field.metadata['env'])
    assert differences == {'APP_DATA_SOURCE_MODE'}
    assert values['ALARM_RETENTION_DAYS'] == '30'


def test_invalid_hot_boolean_never_silently_disables_automation(configured):
    web = configured()
    write_runtime_env(web.settings.runtime_settings_env_path, {'AUTOMATION_ENABLED': 'wrong'})
    current, revision = web.runtime.snapshot()
    assert current.automation_enabled is True and revision == 0
    assert web.runtime.inspect()['error']


def test_empty_numeric_values_use_default_source_including_inherited_connections(configured, monkeypatch):
    from app.services import automation_runtime
    process = {'SQLSERVER_PORT': '', 'SMTP_PORT': ''}
    web = configured(process)
    monkeypatch.setattr(automation_runtime, 'LOADED_ENVIRONMENT', process)
    monkeypatch.setattr(automation_runtime, 'LOADED_SOURCES', dict.fromkeys(process, 'process'))
    web.runtime = AutomationRuntime(web.settings)
    report = web.report(worker(web))
    for key in ['SQLSERVER_PORT', 'SMTP_PORT', 'connections.worker.port']:
        row = item(report, key)
        assert row['processes']['web']['source'] == '程序默认值'
        assert row['saved_source'] == '程序默认值'
