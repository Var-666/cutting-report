from dataclasses import replace
from types import SimpleNamespace

import pytest
from dotenv import dotenv_values
from pydantic import ValidationError

from app.api import automation
from app.config import settings


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


def _request():
    return SimpleNamespace(state=SimpleNamespace(request_id="test-request"))


def test_configuration_response_never_contains_smtp_password(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(automation, "settings", _runtime_settings(tmp_path))

    payload = automation._automation_configuration_data()

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
    assert response["data"]["restart_required"] is True
    assert saved["SQLSERVER_HOST"] == "production-db"
    assert saved["AUTOMATION_SCAN_SECONDS"] == "30.0"
    assert saved["SMTP_HOST"] == "smtp.new.example"
    assert saved["SMTP_PASSWORD"] == "existing-secret"
    assert saved["SMTP_USE_TLS"] == "false"
    assert saved["SMTP_USE_SSL"] == "true"
    assert path.stat().st_mode & 0o777 == 0o600


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
