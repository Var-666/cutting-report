from __future__ import annotations

from dataclasses import replace

from app.config import Settings
from app.services.runtime_settings_store import runtime_settings_lock


class AutomationRuntime:
    """Publish one immutable automation/SMTP configuration to live services."""

    def __init__(self, settings: Settings) -> None:
        self.update_lock = runtime_settings_lock
        self._settings = settings
        self._revision = 0

    def snapshot(self) -> tuple[Settings, int]:
        with self.update_lock:
            return self._settings, self._revision

    def apply(self, **changes: object) -> None:
        with self.update_lock:
            self._settings = replace(self._settings, **changes)
            self._revision += 1
