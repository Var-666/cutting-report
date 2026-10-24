from __future__ import annotations

import logging
import threading

from app.config import Settings
from app.services.file_download_service import FileDownloadService


logger = logging.getLogger(__name__)


class DownloadCleanupWorker:
    """Run retention cleanup at startup and once per day thereafter."""

    INTERVAL_SECONDS = 24 * 60 * 60

    def __init__(self, settings: Settings, service: FileDownloadService, *, managed: bool = False) -> None:
        self.settings = settings
        self.service = service
        self.stop_event = threading.Event()
        self.thread = threading.Thread(
            target=self._run_loop,
            name="download-cleanup-worker",
            daemon=True,
        ) if not managed else None

    @property
    def enabled(self) -> bool:
        return self.settings.file_download_retention_days > 0

    def start(self) -> None:
        if self.enabled:
            self.thread.start()

    def stop(self) -> None:
        if not self.thread.is_alive():
            return
        self.stop_event.set()
        self.thread.join(timeout=5)

    def _run_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                result = self.service.cleanup_expired()
                if result["cleaned"] or result["failed"]:
                    logger.info("cutting download cleanup completed", extra=result)
            except Exception:
                logger.exception("cutting download cleanup cycle failed")
            self.stop_event.wait(self.INTERVAL_SECONDS)
