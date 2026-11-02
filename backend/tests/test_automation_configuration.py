from dataclasses import replace
from types import SimpleNamespace

import pytest
from dotenv import dotenv_values
from fastapi import HTTPException
from pydantic import ValidationError

from app.api import automation
from app.config import settings
from app.services.automation_runtime import AutomationRuntime
from app.services.email_service import EmailService
from app.services.runtime_settings_store import write_runtime_env


def _runtime_settings(tmp_path, **changes):
    values = {
        "project_root": tmp_path,
        "automation_enabled": True,
        "automation_scan_seconds": 15,
        "email_retry_base_seconds": 60,
        "smtp_enabled": True,
        "smtp_host": "smtp.old.example",
        "smtp_port": 587,
        "smtp_username": "reporter",
        "smtp_password": "existing-secret",
        "smtp_from_email": "reports@example.com",
        "smtp_from_name": "报表系统",
        "smtp_use_tls": True,
        "smtp_use_ssl": False,
        "smtp_timeout": 20,
    }
    values.update(changes)
    return replace(settings, **values)


def _request(runtime=None):
    runtime = runtime or AutomationRuntime(automation.settings)
    return SimpleNamespace(
        state=SimpleNamespace(request_id="test-request"),
        app=SimpleNamespace(
            state=SimpleNamespace(
                automation_runtime=runtime,
                email_service=EmailService(automation.settings, runtime=runtime),
                automation=SimpleNamespace(summary=lambda: {}),
            )
        ),
    )


def test_configuration_response_never_contains_smtp_password(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(automation, "settings", _runtime_settings(tmp_path))

    payload = automation.automation_configuration_data(automation.settings)

    assert payload["smtp_password_configured"] is True
    assert "smtp_password" not in payload
    assert "existing-secret" not in str(payload)


def test_configuration_save_preserves_other_settings_and_existing_password(
    tmp_path, monkeypatch
) -> None:
    runtime_settings = _runtime_settings(tmp_path)
    path = runtime_settings.runtime_settings_env_path
    path.parent.mkdir(parents=True)
    path.write_text(
        "SQLSERVER_HOST='production-db'\nSMTP_HOST='smtp.old.example'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(automation, "settings", runtime_settings)
    payload = automation.AutomationConfigurationRequest(
        automation_enabled=True,
        automation_scan_seconds=30,
        email_retry_base_seconds=120,
        smtp_enabled=True,
        smtp_host="smtp.new.example",
        smtp_port=465,
        smtp_username="mailer",
        smtp_password=None,
        smtp_from_email="automation@example.com",
        smtp_from_name="切割报表",
        smtp_use_tls=False,
        smtp_use_ssl=True,
        smtp_timeout_seconds=25,
    )

    response = automation.save_automation_configuration(payload, _request())

    saved = dotenv_values(path, interpolate=False)
    assert response["data"]["restart_required"] is False
    assert saved["SQLSERVER_HOST"] == "production-db"
    assert saved["AUTOMATION_SCAN_SECONDS"] == "30.0"
    assert saved["SMTP_HOST"] == "smtp.new.example"
    assert saved["SMTP_PASSWORD"] == "existing-secret"
    assert saved["SMTP_USE_TLS"] == "false"
    assert saved["SMTP_USE_SSL"] == "true"
    assert path.stat().st_mode & 0o777 == 0o600


def test_configuration_save_updates_live_status_and_preserves_password(
    tmp_path, monkeypatch
) -> None:
    runtime_settings = _runtime_settings(tmp_path)
    monkeypatch.setattr(automation, "settings", runtime_settings)
    runtime = AutomationRuntime(runtime_settings)
    request = _request(runtime)

    response = automation.save_automation_configuration(
        automation.AutomationConfigurationRequest(
            automation_enabled=False,
            automation_scan_seconds=3,
            email_retry_base_seconds=12,
            smtp_enabled=True,
            smtp_host="smtp.live.example",
            smtp_port=465,
            smtp_username="live-user",
            smtp_password=None,
            smtp_from_email="live@example.com",
            smtp_use_tls=False,
            smtp_use_ssl=True,
            smtp_timeout_seconds=8,
        ),
        request,
    )

    live = automation.get_automation_configuration(request)["data"]
    status = automation.status(request)["data"]
    current, revision = runtime.snapshot()
    assert response["data"]["restart_required"] is False
    assert revision == 1
    assert current.smtp_password == "existing-secret"
    assert live["automation_enabled"] is False
    assert live["automation_scan_seconds"] == 3
    assert live["email_retry_base_seconds"] == 12
    assert live["smtp_password_configured"] is True
    assert "existing-secret" not in str(live)
    assert status["automation_enabled"] is False
    assert status["smtp"]["host"] == "smtp.live.example"
    assert status["smtp"]["use_ssl"] is True


def test_failed_save_keeps_active_configuration(tmp_path, monkeypatch) -> None:
    runtime_settings = _runtime_settings(tmp_path)
    monkeypatch.setattr(automation, "settings", runtime_settings)
    runtime = AutomationRuntime(runtime_settings)
    request = _request(runtime)

    def fail_write(_path, _values):
        raise OSError("disk unavailable")

    monkeypatch.setattr("app.services.automation_runtime.write_runtime_env", fail_write)
    with pytest.raises(HTTPException, match="配置保存失败"):
        automation.save_automation_configuration(
            automation.AutomationConfigurationRequest(
                smtp_enabled=True,
                smtp_host="smtp.new.example",
                smtp_from_email="reports@example.com",
            ),
            request,
        )
    current, revision = runtime.snapshot()
    assert revision == 0
    assert current.smtp_host == "smtp.old.example"


def test_configuration_rejects_conflicting_or_incomplete_smtp_settings() -> None:
    with pytest.raises(ValidationError, match="STARTTLS"):
        automation.AutomationConfigurationRequest(
            smtp_use_tls=True,
            smtp_use_ssl=True,
        )

    with pytest.raises(ValidationError, match="SMTP"):
        automation.AutomationConfigurationRequest(
            smtp_enabled=True,
            smtp_host="",
            smtp_from_email="",
            smtp_use_tls=True,
            smtp_use_ssl=False,
        )


def test_save_uses_runtime_root_and_keeps_existing_operation_snapshot(tmp_path, monkeypatch):
    initial = _runtime_settings(tmp_path, smtp_password='runtime-secret')
    runtime = AutomationRuntime(initial)
    request = _request(runtime)
    unrelated = _runtime_settings(tmp_path / 'other-app', smtp_password='wrong-global-secret')
    monkeypatch.setattr(automation, 'settings', unrelated)
    payload = automation.AutomationConfigurationRequest(smtp_enabled=True, smtp_host='new-smtp',
        smtp_from_email='new@example.com', smtp_password='')
    automation.save_automation_configuration(payload, request)
    current, _ = runtime.snapshot()
    assert current.smtp_host == 'new-smtp' and current.smtp_password == 'runtime-secret'
    assert initial.smtp_host == 'smtp.old.example' and initial.smtp_password == 'runtime-secret'
    assert not unrelated.runtime_settings_env_path.exists()


def test_failed_candidate_preparation_does_not_write_or_publish(tmp_path, monkeypatch):
    initial = _runtime_settings(tmp_path)
    path = initial.runtime_settings_env_path
    write_runtime_env(path, {'CUSTOM_VALUE': 'keep'})
    runtime = AutomationRuntime(initial)
    before = path.read_bytes()
    monkeypatch.setattr('app.services.automation_runtime.replace', lambda *_, **__: (_ for _ in ()).throw(ValueError('private-details')))
    with pytest.raises(HTTPException) as error:
        automation.save_automation_configuration(automation.AutomationConfigurationRequest(), _request(runtime))
    assert error.value.status_code == 500 and 'private-details' not in error.value.detail
    assert path.read_bytes() == before
    assert runtime.inspect()['settings'] is initial and runtime.inspect()['revision'] == 0


def test_blank_password_keeps_the_latest_value_saved_before_restart(
    tmp_path, monkeypatch
) -> None:
    runtime_settings = _runtime_settings(tmp_path, smtp_password="old-secret")
    monkeypatch.setattr(automation, "settings", runtime_settings)
    base = {
        "smtp_enabled": True,
        "smtp_host": "smtp.example.com",
        "smtp_from_email": "reports@example.com",
        "smtp_use_tls": True,
        "smtp_use_ssl": False,
    }

    automation.save_automation_configuration(
        automation.AutomationConfigurationRequest(
            **base, smtp_password="new-secret"
        ),
        _request(),
    )
    automation.save_automation_configuration(
        automation.AutomationConfigurationRequest(**base, smtp_password=None),
        _request(),
    )

    saved = dotenv_values(
        runtime_settings.runtime_settings_env_path, interpolate=False
    )
    assert saved["SMTP_PASSWORD"] == "new-secret"
