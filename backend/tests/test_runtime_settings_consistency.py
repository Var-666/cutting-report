from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event
from types import SimpleNamespace

import pytest

from app.api import automation, database
from app.config import settings
from app.services.automation_runtime import AutomationRuntime
from app.services.runtime_settings_store import read_runtime_env, runtime_settings_lock


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


@pytest.mark.parametrize("mode", ["sqlsever", "unknown", "SQLSERVER", "demo "])
def test_invalid_data_source_mode_fails_instead_of_returning_demo(mode):
    with pytest.raises(ValueError, match="APP_DATA_SOURCE_MODE"):
        replace(settings, data_source_mode=mode)


def test_production_demo_requires_explicit_mode():
    with pytest.raises(ValueError, match="禁止隐式演示数据"):
        replace(settings, app_env="production", data_source_mode="", demo_mode=True)
    assert replace(settings, app_env="production", data_source_mode="demo", demo_mode=True).effective_data_source_mode == "demo"
    assert replace(settings, app_env="production", data_source_mode="", demo_mode=False).effective_data_source_mode == "sqlserver"
