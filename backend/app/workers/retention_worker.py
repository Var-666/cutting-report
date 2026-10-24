from __future__ import annotations

import logging
import threading

from app.config import Settings
from app.services.retention_service import RetentionService


logger = logging.getLogger(__name__)


class RetentionWorker:
    """Apply all configured operational-data retention policies periodically."""

    def __init__(self, settings: Settings, service: RetentionService, *, managed: bool = False) -> None:
        self.settings = settings
        self.service = service
        self.stop_event = threading.Event()
        self.thread = threading.Thread(
            target=self._run_loop,
            name="retention-worker",
            daemon=True,
        ) if not managed else None

    @property
    def enabled(self) -> bool:
        return self.service.has_pending_file_deletions() or any(
            days > 0
            for days in (
                self.settings.report_retention_days,
                self.settings.analysis_retention_days,
                self.settings.alarm_retention_days,
                self.settings.dispatch_retention_days,
                self.settings.audit_retention_days,
            )
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
        interval = max(1, self.settings.retention_cleanup_interval_hours) * 3600
        while not self.stop_event.is_set():
            try:
                result = self.service.cleanup_expired()
                if any(result["records"].values()) or any(result["files"].values()):
                    logger.info("data retention cleanup completed", extra=result)
            except Exception:
                logger.exception("data retention cleanup cycle failed")
            self.stop_event.wait(interval)
