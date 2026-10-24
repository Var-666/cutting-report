from __future__ import annotations

from dataclasses import replace

from app.config import Settings
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

    def snapshot(self) -> tuple[Settings, int]:
        with self.update_lock:
            path = self._settings.runtime_settings_env_path
            try:
                stat = path.stat()
                version = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
            except FileNotFoundError:
                version = None
            if version != self._file_version:
                values = read_runtime_env(path)
                changes = {}
                for key, (field, convert) in HOT_SETTINGS.items():
                    if key in values:
                        value = values[key]
                        changes[field] = value.strip().lower() in {"1", "true", "yes", "on"} if convert is bool else convert(value)
                updated = replace(self._settings, **changes)
                if updated != self._settings:
                    self._settings = updated
                    self._revision += 1
                self._file_version = version
            return self._settings, self._revision

    def apply(self, **changes: object) -> None:
        with self.update_lock:
            self._settings = replace(self._settings, **changes)
            self._revision += 1
