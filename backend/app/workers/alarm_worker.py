from __future__ import annotations

import logging
import threading
from datetime import datetime
from time import monotonic
from zoneinfo import ZoneInfo

from app.config import Settings
from app.services.alarm_monitor_service import AlarmMonitorService


logger = logging.getLogger(__name__)


class AlarmWorker:
    def __init__(self, settings: Settings, service: AlarmMonitorService) -> None:
        self.settings = settings
        self.service = service
        self.stop_event = threading.Event()
        self.thread = threading.Thread(
            target=self._run_loop, name="alarm-monitor-worker", daemon=True
        )

    @property
    def enabled(self) -> bool:
        return (
            self.settings.alarm_monitor_enabled
            and self.settings.effective_data_source_mode == "sqlserver"
            and bool(self.settings.alarm_monitor_machine_nos)
        )

    def start(self) -> None:
        if self.enabled:
            self.thread.start()

    def stop(self) -> None:
        if not self.thread.is_alive():
            return
        self.stop_event.set()
        self.thread.join(timeout=5)

    def _run_loop(self) -> None:
        timezone = ZoneInfo(self.settings.timezone)
        interval = max(30, self.settings.alarm_monitor_interval_seconds)
        while not self.stop_event.is_set():
            started = monotonic()
            try:
                self.service.run_once(datetime.now(timezone))
            except Exception:
                logger.exception("alarm monitor cycle failed")
            elapsed = monotonic() - started
            self.stop_event.wait(max(1, interval - elapsed))
