from __future__ import annotations

import logging
import threading
from time import monotonic
from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import Settings
from app.database.metadata import MetadataRepository
from app.services.pipeline import ReportPipeline
from app.services.automation_service import AutomationService
from app.services.automation_runtime import AutomationRuntime


logger = logging.getLogger(__name__)


class ReportWorker:
    def __init__(
        self,
        settings: Settings,
        metadata: MetadataRepository,
        pipeline: ReportPipeline,
        automation_service: AutomationService | None = None,
        runtime: AutomationRuntime | None = None,
    ) -> None:
        self.settings = settings
        self.metadata = metadata
        self.pipeline = pipeline
        self.automation_service = automation_service
        self.runtime = runtime or AutomationRuntime(settings)
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
        next_automation_scan = 0.0
        seen_revision = -1
        while not self.stop_event.is_set():
            runtime_settings, revision = self.runtime.snapshot()
            if revision != seen_revision:
                next_automation_scan = 0.0
                seen_revision = revision
            now = datetime.now(timezone)
            if (
                self.automation_service is not None
                and monotonic() >= next_automation_scan
            ):
                try:
                    self.automation_service.process_due_schedules(now)
                except Exception:
                    logger.exception("automation schedule scan failed")
                next_automation_scan = monotonic() + runtime_settings.automation_scan_seconds

            job = self.metadata.claim_next_job(now.isoformat(timespec="seconds"))
            if job is None:
                if self.automation_service is not None:
                    try:
                        if self.automation_service.process_next_email(now):
                            continue
                    except Exception:
                        logger.exception("automation email processing failed")
                self.stop_event.wait(self.settings.worker_poll_seconds)
                continue
            report_id = job["report_id"]
            try:
                logger.info("report generation started", extra={"report_id": report_id})
                self.pipeline.run(job)
                if self.automation_service is not None:
                    self.automation_service.report_completed(
                        report_id, datetime.now(timezone)
                    )
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
                if self.automation_service is not None:
                    self.automation_service.report_failed(
                        report_id, str(exc)[:500], datetime.now(timezone)
                    )
