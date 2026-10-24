from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from app.config import LOADED_ENVIRONMENT, LOADED_SOURCES, Settings
from app.services.automation_configuration import AutomationConfigurationRequest
from app.services.runtime_settings_store import runtime_settings_lock
from app.services.runtime_settings_store import read_runtime_env


HOT_SETTINGS = {
    "AUTOMATION_ENABLED": ("automation_enabled", bool),
    "AUTOMATION_SCAN_SECONDS": ("automation_scan_seconds", float),
    "EMAIL_RETRY_BASE_SECONDS": ("email_retry_base_seconds", int),
    "SMTP_ENABLED": ("smtp_enabled", bool), "SMTP_HOST": ("smtp_host", str),
    "SMTP_PORT": ("smtp_port", int), "SMTP_USERNAME": ("smtp_username", str),
    "SMTP_PASSWORD": ("smtp_password", str), "SMTP_FROM_EMAIL": ("smtp_from_email", str),
    "SMTP_FROM_NAME": ("smtp_from_name", str), "SMTP_USE_TLS": ("smtp_use_tls", bool),
    "SMTP_USE_SSL": ("smtp_use_ssl", bool), "SMTP_TIMEOUT_SECONDS": ("smtp_timeout", float),
}


class AutomationRuntime:
    """Publish one immutable automation/SMTP configuration to live services."""

    def __init__(self, settings: Settings) -> None:
        self.update_lock = runtime_settings_lock
        self._settings = settings
        self._revision = 0
        self._file_version = None
        self._sources = {
            field: "default" if convert in {int, float} and LOADED_ENVIRONMENT.get(key) == "" else LOADED_SOURCES.get(key, "default")
            for key, (field, convert) in HOT_SETTINGS.items()
        }
        self._loaded_at = datetime.now(timezone.utc).isoformat()
        self._error = None

    def snapshot(self) -> tuple[Settings, int]:
        with self.update_lock:
            path = self._settings.runtime_settings_env_path
            try:
                stat = path.stat()
                version = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
            except FileNotFoundError:
                version = None
            except OSError:
                self._error = "自动化/SMTP 配置无法读取，保留上次运行快照"
                return self._settings, self._revision
            if version != self._file_version:
                try:
                    values = read_runtime_env(path)
                    changes = {}
                    for key, (field, convert) in HOT_SETTINGS.items():
                        if key in values:
                            value = values[key]
                            if convert is bool and value.strip().lower() not in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
                                raise ValueError("布尔配置值无效")
                            changes[field] = value.strip().lower() in {"1", "true", "yes", "on"} if convert is bool else convert(value)
                    updated = replace(self._settings, **changes)
                    AutomationConfigurationRequest.model_validate({
                        ("smtp_timeout_seconds" if name == "smtp_timeout" else name): getattr(updated, name)
                        for name, _ in HOT_SETTINGS.values()
                    })
                    if updated != self._settings:
                        self._settings = updated
                        self._revision += 1
                    self._sources.update(dict.fromkeys(changes, "data/settings.env"))
                    self._loaded_at = datetime.now(timezone.utc).isoformat()
                    self._error = None
                except (OSError, ValueError, TypeError, AttributeError):
                    # Keep the last usable configuration; never log the offending value.
                    self._error = "自动化/SMTP 配置无效，保留上次运行快照"
                self._file_version = version
            return self._settings, self._revision

    def apply(self, *, source: str = "runtime", **changes: object) -> None:
        with self.update_lock:
            self._settings = replace(self._settings, **changes)
            self._revision += 1
            self._sources.update(dict.fromkeys(changes, source))
            self._loaded_at = datetime.now(timezone.utc).isoformat()
            self._error = None

    def inspect(self) -> dict:
        """Read the published snapshot without loading files or applying changes."""
        with self.update_lock:
            return {"settings": self._settings, "revision": self._revision,
                    "sources": dict(self._sources), "loaded_at": self._loaded_at,
                    "error": self._error}
