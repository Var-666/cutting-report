from __future__ import annotations

import logging
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import Settings
from app.database.metadata import MetadataRepository
from app.services.pipeline import ReportPipeline


logger = logging.getLogger(__name__)


class ReportWorker:
    def __init__(
        self,
        settings: Settings,
        metadata: MetadataRepository,
        pipeline: ReportPipeline,
    ) -> None:
        self.settings = settings
        self.metadata = metadata
        self.pipeline = pipeline
        self.stop_event = threading.Event()
        self.thread = threading.Thread(
            target=self._run_loop, name="report-worker", daemon=True
        )

    def start(self) -> None:
        self.metadata.recover_interrupted_jobs()
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=5)

    def _run_loop(self) -> None:
        timezone = ZoneInfo(self.settings.timezone)
        while not self.stop_event.is_set():
            job = self.metadata.claim_next_job(datetime.now(timezone).isoformat(timespec="seconds"))
            if job is None:
                self.stop_event.wait(self.settings.worker_poll_seconds)
                continue
            report_id = job["report_id"]
            try:
                logger.info("report generation started", extra={"report_id": report_id})
                self.pipeline.run(job)
                logger.info("report generation completed", extra={"report_id": report_id})
            except Exception as exc:
                logger.exception("report generation failed", extra={"report_id": report_id})
                self.metadata.update_status(
                    report_id,
                    "failed",
                    "报告生成失败，请联系管理员",
                    finished_at=datetime.now(timezone).isoformat(timespec="seconds"),
                    error_code="REPORT_GENERATION_FAILED",
                    error_message=str(exc)[:500],
                )

