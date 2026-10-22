from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api import automation, database
from app.config import settings
from app.services.automation_runtime import AutomationRuntime
from app.services.runtime_settings_store import read_runtime_env, runtime_settings_lock
from app.database.sqlserver import SqlServerConfigurationError
from app.models.domain import ReportFilter
from app.services.query_service import QueryService


def test_database_and_automation_save_share_read_modify_publish_lock(tmp_path, monkeypatch):
    configured = replace(settings, project_root=tmp_path)
    monkeypatch.setattr(database, "settings", configured)
    monkeypatch.setattr(automation, "settings", configured)
    runtime = AutomationRuntime(configured)
    assert runtime.update_lock is runtime_settings_lock
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(automation_runtime=runtime)),
                              state=SimpleNamespace(request_id="test"))
    entered, release, database_done = Event(), Event(), Event()
    from app.services import runtime_settings_store as store
    original_replace = store.os.replace
    calls = 0

    def slow_replace(source, target):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            assert release.wait(2)
        return original_replace(source, target)

    monkeypatch.setattr(store.os, "replace", slow_replace)

    def save_database():
        database._write_runtime_env({"SQLSERVER_HOST": "production-db"})
        database_done.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(automation.save_automation_configuration,
                            automation.AutomationConfigurationRequest(smtp_host="new-smtp"), request)
        assert entered.wait(1)
        second = pool.submit(save_database)
        try:
            assert not database_done.wait(0.05), "another writer must not read an old settings version"
        finally:
            release.set()
        first.result(timeout=2)
        second.result(timeout=2)
    saved = read_runtime_env(configured.runtime_settings_env_path)
    assert saved["SQLSERVER_HOST"] == "production-db"
    assert saved["SMTP_HOST"] == runtime.snapshot()[0].smtp_host == "new-smtp"
    assert configured.runtime_settings_env_path.stat().st_mode & 0o777 == 0o600


def test_blank_database_password_preserves_latest_saved_value_before_restart(tmp_path, monkeypatch):
    configured = replace(settings, project_root=tmp_path, sqlserver_password="old-startup-password")
    monkeypatch.setattr(database, "settings", configured)
    database._write_runtime_env({"SQLSERVER_PASSWORD": "new-saved-password"})
    payload = database.SavedConnectionRequest(host="db", database="production", username="reader")
    values = database._connection_env_values("production", payload)
    assert values["SQLSERVER_PASSWORD"] == "new-saved-password"


def test_failed_atomic_write_preserves_file_and_removes_temporary_secret(tmp_path, monkeypatch):
    from app.services import runtime_settings_store as store
    path = tmp_path / "settings.env"
    store.write_runtime_env(path, {"SMTP_HOST": "original"})
    original = path.read_bytes()
    monkeypatch.setattr(store.os, "fsync", lambda _: (_ for _ in ()).throw(OSError("disk unavailable")))
    with pytest.raises(OSError):
        store.write_runtime_env(path, {"SMTP_PASSWORD": "secret"})
    assert path.read_bytes() == original
    assert list(tmp_path.glob(".settings-*.tmp")) == []


@pytest.mark.parametrize("mode", ["excel", "sqlsever", "unknown", "SQLSERVER", "demo "])
def test_invalid_data_source_mode_fails_instead_of_returning_demo(mode):
    with pytest.raises(ValueError, match="APP_DATA_SOURCE_MODE"):
        replace(settings, data_source_mode=mode)


def test_production_demo_requires_explicit_mode():
    with pytest.raises(ValueError, match="禁止隐式演示数据"):
        replace(settings, app_env="production", data_source_mode="", demo_mode=True)
    assert replace(settings, app_env="production", data_source_mode="demo", demo_mode=True).effective_data_source_mode == "demo"
    assert replace(settings, app_env="production", data_source_mode="", demo_mode=False).effective_data_source_mode == "sqlserver"


def test_database_configuration_cannot_save_removed_excel_mode():
    connections = {"production": {}, "worker": {}, "file": {}}
    with pytest.raises(ValidationError) as error:
        database.DatabaseConfigurationRequest(data_source_mode="excel", **connections)
    assert error.value.errors()[0]["loc"] == ("data_source_mode",)
    for mode in ("demo", "sqlserver"):
        assert database.DatabaseConfigurationRequest(data_source_mode=mode, **connections).data_source_mode == mode


def test_sqlserver_query_failure_does_not_fall_back_to_local_or_demo_data(monkeypatch):
    service = QueryService(replace(settings, data_source_mode="sqlserver"))

    def unavailable(*args):
        raise SqlServerConfigurationError("生产数据库未配置")

    monkeypatch.setattr(service.client, "fetch_all", unavailable)
    with pytest.raises(SqlServerConfigurationError, match="生产数据库未配置"):
        service.fetch_report_data(ReportFilter(report_date=date(2026, 10, 13)))


@pytest.mark.parametrize(("mode", "enabled", "token", "provided", "expected"), [
    ("demo", True, "admin-secret", "admin-secret", 409),
    ("sqlserver", False, "admin-secret", "admin-secret", 403),
    ("sqlserver", True, "", None, 503),
    ("sqlserver", True, "admin-secret", "wrong", 401),
])
def test_removing_local_workbench_keeps_production_execution_guards(monkeypatch, mode, enabled, token, provided, expected):
    monkeypatch.setattr(database, "settings", replace(settings, data_source_mode=mode,
                        sql_workbench_enabled=enabled, sql_workbench_admin_token=token))
    with pytest.raises(HTTPException) as error:
        database.execute_sql(database.SqlExecuteRequest(sql="SELECT 1"), object(), provided)
    assert error.value.status_code == expected


def test_demo_status_does_not_advertise_sql_execution_even_when_flag_is_set(monkeypatch):
    monkeypatch.setattr(database, "settings", replace(settings, data_source_mode="demo", sql_workbench_enabled=True))
    request = SimpleNamespace(state=SimpleNamespace(request_id="test"))
    status = database.database_status(request)["data"]
    assert status["sql_workbench_enabled"] is False
    assert status["arbitrary_sql_enabled"] is False
