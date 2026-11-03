from dataclasses import replace

import pytest
from dotenv import dotenv_values

from app.api import database
from app.config import settings
from app.database.source_registry import DatabaseSourceRegistry, source_configs_json
from app.database.sqlserver import SqlServerClient, SqlServerConfigurationError
from app.models.database_sources import DatabaseSourceConfig
from app.services.worker_auth_service import WorkerAuthService
from tests.line_fixtures import configuration


def test_worker_and_file_use_explicitly_bound_registry_clients(tmp_path):
    from app.services.application_runtime import build_runtime
    cfg = configuration(tmp_path)
    sources = DatabaseSourceRegistry.from_json(cfg.database_sources_json).configs()
    for key in ('hr', 'pdm'):
        sources.append(DatabaseSourceConfig(id=key, name=key, engine='sqlserver', host=key + '.test', port=1433,
            database=key, username='reader', password=key + '-private'))
    cfg = replace(cfg, worker_database_auth_enabled=True, file_database_enabled=True, worker_source_key='hr',
                  file_source_key='pdm', database_sources_json=source_configs_json(sources))
    context = build_runtime(cfg, role='web').database_runtime.snapshot()
    assert context.worker_auth.client is context.database_sources.client('hr')
    assert context.file_downloads.pdm.client is context.database_sources.client('pdm')
    assert context.worker_auth.client.connection_config()['database'] == 'hr'
    assert context.file_downloads.pdm.client.connection_config()['database'] == 'pdm'


def test_disabled_or_incompatible_source_cannot_be_used():
    cfg = DatabaseSourceConfig(id='production-test', name='测试', engine='sqlserver', host='', port=1433,
                               database='', username='', enabled=False)
    with pytest.raises(SqlServerConfigurationError, match='尚未启用'):
        SqlServerClient(cfg).connection_config()
    with pytest.raises(ValueError, match='不是 SQL Server'):
        SqlServerClient(cfg.model_copy(update={'engine': 'mysql'}))


def test_configuration_response_is_from_selected_source_and_never_echoes_credentials(tmp_path):
    cfg = configuration(tmp_path, welding=True)
    payload = database.database_configuration_data(cfg, 'Cutter01')
    assert payload['production']['password_configured']
    assert payload['welding'] is None
    weld = database.database_configuration_data(cfg, 'welding')
    assert weld['welding']['password_configured']
    assert 'password' not in payload['production'] and 'private' not in str(payload) + str(weld)
    status = database._database_source_status(cfg)
    assert {item['source'] for item in status} == {'cutting-production', 'welding-production'}
    assert all(not item['uses_primary_fallback'] for item in status)


def test_runtime_env_write_is_atomic_preserves_unknown_keys_and_quotes_secrets(
    tmp_path, monkeypatch
) -> None:
    runtime_settings = replace(settings, project_root=tmp_path)
    path = runtime_settings.runtime_settings_env_path
    path.parent.mkdir(parents=True)
    path.write_text("CUSTOM_SETTING='keep-me'\nSQLSERVER_HOST='old'\n", encoding="utf-8")
    monkeypatch.setattr(database, "settings", runtime_settings)

    from app.services.runtime_settings_store import write_runtime_env
    write_runtime_env(
        path, {
            "SQLSERVER_HOST": "new-db",
            "SQLSERVER_PASSWORD": "pa'ss\\word${UNCHANGED}",
        }
    )

    saved = dotenv_values(path, interpolate=False)
    assert saved["CUSTOM_SETTING"] == "keep-me"
    assert saved["SQLSERVER_HOST"] == "new-db"
    assert saved["SQLSERVER_PASSWORD"] == "pa'ss\\word${UNCHANGED}"
    assert path.stat().st_mode & 0o777 == 0o600
