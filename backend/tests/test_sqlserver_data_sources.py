from dataclasses import replace

import pytest
from dotenv import dotenv_values

from app.api import database
from app.config import settings
from app.database.sqlserver import SqlServerClient, SqlServerConfigurationError
from app.services.worker_auth_service import WorkerAuthService


def _production_settings(**changes):
    values = {
        "sqlserver_host": "prod-db",
        "sqlserver_port": 1433,
        "sqlserver_database": "production",
        "sqlserver_username": "prod-reader",
        "sqlserver_password": "prod-secret",
        "worker_sqlserver_host": "",
        "worker_sqlserver_database": "",
        "worker_sqlserver_username": "",
        "worker_sqlserver_password": "",
        "file_sqlserver_host": "",
        "file_sqlserver_database": "",
        "file_sqlserver_username": "",
        "file_sqlserver_password": "",
    }
    values.update(changes)
    return replace(settings, **values)


def test_worker_and_file_clients_use_their_own_database_profiles() -> None:
    configured = _production_settings(
        worker_sqlserver_host="hr-db",
        worker_sqlserver_port=1434,
        worker_sqlserver_database="hr",
        worker_sqlserver_username="hr-reader",
        worker_sqlserver_password="hr-secret",
        file_sqlserver_host="file-db",
        file_sqlserver_port=1435,
        file_sqlserver_database="pdm",
        file_sqlserver_username="file-reader",
        file_sqlserver_password="file-secret",
    )

    worker_connection = SqlServerClient(configured, source="worker")._connection_string()
    file_connection = SqlServerClient(configured, source="file")._connection_string()

    assert "SERVER=hr-db,1434" in worker_connection
    assert "DATABASE=hr" in worker_connection
    assert "UID=prod-reader" not in worker_connection
    assert "SERVER=file-db,1435" in file_connection
    assert "DATABASE=pdm" in file_connection
    assert "UID=prod-reader" not in file_connection


def test_partial_dedicated_profile_never_mixes_primary_credentials() -> None:
    configured = _production_settings(worker_sqlserver_host="hr-db")

    with pytest.raises(SqlServerConfigurationError) as exc_info:
        SqlServerClient(configured, source="worker")._connection_string()

    message = str(exc_info.value)
    assert "WORKER_SQLSERVER_DATABASE" in message
    assert "WORKER_SQLSERVER_USERNAME" in message
    assert "WORKER_SQLSERVER_PASSWORD" in message


def test_unconfigured_legacy_profile_has_an_explicit_primary_fallback() -> None:
    configured = _production_settings()

    worker_profile = configured.sqlserver_connection("worker")

    assert worker_profile["uses_primary_fallback"] is True
    assert worker_profile["host"] == "prod-db"


def test_worker_auth_constructs_a_worker_database_client() -> None:
    service = WorkerAuthService(_production_settings())

    assert service.client.source == "worker"


def test_runtime_env_write_is_atomic_preserves_unknown_keys_and_quotes_secrets(
    tmp_path, monkeypatch
) -> None:
    runtime_settings = replace(settings, project_root=tmp_path)
    path = runtime_settings.runtime_settings_env_path
    path.parent.mkdir(parents=True)
    path.write_text("CUSTOM_SETTING='keep-me'\nSQLSERVER_HOST='old'\n", encoding="utf-8")
    monkeypatch.setattr(database, "settings", runtime_settings)

    database._write_runtime_env(
        {
            "SQLSERVER_HOST": "new-db",
            "SQLSERVER_PASSWORD": "pa'ss\\word${UNCHANGED}",
        }
    )

    saved = dotenv_values(path, interpolate=False)
    assert saved["CUSTOM_SETTING"] == "keep-me"
    assert saved["SQLSERVER_HOST"] == "new-db"
    assert saved["SQLSERVER_PASSWORD"] == "pa'ss\\word${UNCHANGED}"
    assert path.stat().st_mode & 0o777 == 0o600


def test_database_configuration_response_never_contains_passwords(monkeypatch) -> None:
    configured = _production_settings(
        worker_sqlserver_password="worker-secret",
        file_sqlserver_password="file-secret",
    )
    monkeypatch.setattr(database, "settings", configured)

    payload = database._database_configuration_data()

    assert payload["production"]["password_configured"] is True
    assert payload["worker"]["password_configured"] is True
    assert "password" not in payload["production"]
    assert "prod-secret" not in str(payload)
    assert "worker-secret" not in str(payload)
