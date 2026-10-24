from __future__ import annotations

import logging
import inspect
import threading
from time import monotonic
from datetime import datetime
from zoneinfo import ZoneInfo
from contextlib import nullcontext
from collections.abc import Callable

from app.config import Settings
from app.database.metadata import MetadataRepository
from app.services.pipeline import ReportPipeline, ReportPipelineCancelled
from app.services.automation_service import AutomationService
from app.services.automation_runtime import AutomationRuntime
from app.services.llm_service import background_llm_work


logger = logging.getLogger(__name__)


class ReportWorker:
    def __init__(
        self,
        settings: Settings,
        metadata: MetadataRepository,
        pipeline: ReportPipeline | None = None,
        automation_service: AutomationService | None = None,
        runtime: AutomationRuntime | None = None,
        pipelines: dict[str, ReportPipeline] | Callable[[], dict[str, ReportPipeline]] | None = None,
        execution_context=None,
    ) -> None:
        self.settings = settings
        self.metadata = metadata
        self.pipeline = pipeline
        self.pipelines = pipelines
        self.execution_context = execution_context or nullcontext
        self.automation_service = automation_service
        self.runtime = runtime or AutomationRuntime(settings)
        self.stop_event = threading.Event()
        self._status_lock = threading.Lock()
        self._progress: dict[str, tuple[str, float]] = {}
        self._queue: dict = {"available": False}
        self._queue_observed_at: float | None = None
        self._next_queue_observation = 0.0
        self.thread = threading.Thread(
            target=self._owned, args=(self._run_loop,), name="report-worker", daemon=True
        )

        self.automation_thread = (
            threading.Thread(target=self._owned, args=(self._automation_loop,), name="automation-worker", daemon=True)
            if automation_service is not None else None
        )

        self.email_thread = (
            threading.Thread(target=self._owned, args=(self._email_loop,), name="email-worker", daemon=True)
            if automation_service is not None else None
        )

    def _threads(self) -> tuple[threading.Thread, ...]:
        return tuple(thread for thread in (
            self.thread, self.automation_thread, self.email_thread,
        ) if thread is not None)

    def start(self) -> None:
        try:
            with self.execution_context():
                self.metadata.recover_interrupted_jobs()
            for thread in self._threads():
                thread.start()
        except Exception:
            self.stop()
            raise

    def _owned(self, handler) -> None:
        with self.execution_context():
            handler()

    def stop(self) -> None:
        self.stop_event.set()
        deadline = monotonic() + 5
        for thread in self._threads():
            if thread.ident is not None:
                thread.join(timeout=max(0, deadline - monotonic()))
        remaining = [thread.name for thread in self._threads() if thread.is_alive()]
        if remaining:
            logger.warning("background workers still running after shutdown timeout: %s", remaining)

    def _checkpoint(self, worker: str, stage: str) -> None:
        with self._status_lock:
            self._progress[worker] = (stage, monotonic())

    def _observe_queue(self) -> None:
        if monotonic() < self._next_queue_observation:
            return
        self._next_queue_observation = monotonic() + 5
        try:
            observation = {"available": True, **self.metadata.queue_observation()}
        except Exception:
            logger.exception("report queue observation failed")
            observation = {"available": False}
        with self._status_lock:
            self._queue = observation
            self._queue_observed_at = monotonic()

    def status(self) -> dict:
        now = monotonic()
        with self._status_lock:
            progress = dict(self._progress)
            queue = dict(self._queue)
            observed_at = self._queue_observed_at
        details = {}
        for name, thread in (("report", self.thread), ("automation", self.automation_thread),
                             ("email", self.email_thread)):
            if thread is None:
                continue
            stage, stamp = progress.get(name, ("starting", now))
            age = max(0, now - stamp)
            threshold = self.settings.worker_stall_timeout_seconds
            if stage == "idle":
                threshold = max(threshold, self.settings.worker_poll_seconds * 2)
            details[name] = {"stage": stage, "progress_age_seconds": round(age, 1),
                             "stalled": thread.is_alive() and age >= threshold}
        queue["observation_age_seconds"] = round(max(0, now - observed_at), 1) if observed_at is not None else None
        return {
            "report_alive": self.thread.is_alive(),
            "automation_alive": self.automation_thread.is_alive() if self.automation_thread else None,
            "email_alive": self.email_thread.is_alive() if self.email_thread else None,
            "progress": details,
            "queue": queue,
            "stalled_workers": [name for name, detail in details.items() if detail["stalled"]],
        }

    def _run_loop(self) -> None:
        timezone = ZoneInfo(self.settings.timezone)
        while not self.stop_event.is_set():
            try:
                self._checkpoint("report", "queue-observation")
                self._observe_queue()
                self._checkpoint("report", "claiming")
                job = self.metadata.claim_next_job(
                    datetime.now(timezone).isoformat(timespec="seconds")
                )
                if job is not None:
                    self._checkpoint("report", "generating")
                    self._process_report(job, timezone)
                    continue
            except Exception:
                logger.exception("report queue processing failed")
            finally:
                self._checkpoint("report", "idle")
            self.stop_event.wait(self.settings.worker_poll_seconds)

    @background_llm_work()
    def _process_report(self, job: dict, timezone: ZoneInfo) -> None:
        report_id = job["report_id"]
        try:
            logger.info("report generation started", extra={"report_id": report_id})
            pipelines = self.pipelines() if callable(self.pipelines) else self.pipelines
            pipeline = self.pipeline if pipelines is None else pipelines[job["device_id"]]
            run = pipeline.run
            parameters = inspect.signature(run).parameters
            if "cancellation_check" in parameters:
                run(job, cancellation_check=self.stop_event.is_set)
            else:
                # Keep small test doubles and older integrations compatible while
                # the concrete pipeline receives cooperative shutdown checkpoints.
                run(job)
        except ReportPipelineCancelled:
            if self.stop_event.is_set():
                logger.info("report generation cancelled during worker shutdown", extra={"report_id": report_id})
                return
            raise
        except Exception as exc:
            logger.exception("report generation failed", extra={"report_id": report_id})
            try:
                self.metadata.update_status(
                    report_id, "failed", "报告生成失败，请联系管理员",
                    finished_at=datetime.now(timezone).isoformat(timespec="seconds"),
                    error_code="REPORT_GENERATION_FAILED", error_message=str(exc)[:500],
                )
            except Exception:
                logger.exception("report failure status write failed", extra={"report_id": report_id})
            if self.automation_service is not None:
                try:
                    self.automation_service.report_failed(
                        report_id, str(exc)[:500], datetime.now(timezone)
                    )
                except Exception:
                    logger.exception("automation failure callback failed", extra={"report_id": report_id})
            return

        # The pipeline already committed the completed artifact. Delivery failure
        # must not rewrite it as failed; reconciliation will retry the outbox.
        if self.automation_service is not None:
            try:
                self.automation_service.report_completed(report_id, datetime.now(timezone))
            except Exception:
                logger.exception("automation completion callback failed", extra={"report_id": report_id})
        logger.info("report generation completed", extra={"report_id": report_id})

    def _automation_loop(self) -> None:
        timezone = ZoneInfo(self.settings.timezone)
        next_scan = 0.0
        seen_revision = -1
        while not self.stop_event.is_set():
            try:
                self._checkpoint("automation", "configuration")
                runtime_settings, revision = self.runtime.snapshot()
                if revision != seen_revision:
                    next_scan = 0.0
                    seen_revision = revision
                now = datetime.now(timezone)
                if monotonic() >= next_scan:
                    try:
                        self._checkpoint("automation", "recovering")
                        self.automation_service.reconcile_runs(now)
                    except Exception:
                        logger.exception("automation recovery scan failed")
                    try:
                        self._checkpoint("automation", "scheduling")
                        self.automation_service.process_due_schedules(now)
                    except Exception:
                        logger.exception("automation schedule scan failed")
                    next_scan = monotonic() + runtime_settings.automation_scan_seconds
            except Exception:
                logger.exception("automation scheduler processing failed")
            finally:
                self._checkpoint("automation", "idle")
            self.stop_event.wait(self.settings.worker_poll_seconds)

    def _email_loop(self) -> None:
        timezone = ZoneInfo(self.settings.timezone)
        while not self.stop_event.is_set():
            try:
                self._checkpoint("email", "delivery")
                if self.automation_service.process_next_email(datetime.now(timezone)):
                    continue
            except Exception:
                logger.exception("email queue processing failed")
            finally:
                self._checkpoint("email", "idle")
            self.stop_event.wait(self.settings.worker_poll_seconds)
