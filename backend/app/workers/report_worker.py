from __future__ import annotations

import logging
import inspect
import threading
from time import monotonic
from datetime import datetime
from zoneinfo import ZoneInfo
from contextlib import nullcontext
from collections.abc import Callable

from app.core.operations_logging import event, operation_context
from app.core.sql_capacity import sql_identity
from app.config import Settings
from app.database.metadata import MetadataRepository
from app.database.postgres import ExecutionLeaseLost
from app.services.pipeline import ReportPipeline, ReportPipelineCancelled, ReportAnalysisPendingError
from app.services.automation_service import AutomationService
from app.services.llm_service import background_llm_work


logger = logging.getLogger(__name__)


class ReportWorker:
    def __init__(
        self,
        settings: Settings,
        metadata: MetadataRepository,
        pipeline: ReportPipeline | None = None,
        automation_service: AutomationService | None = None,
        pipelines: dict[str, ReportPipeline] | Callable[[], dict[str, ReportPipeline]] | None = None,
        execution_context=None,
    ) -> None:
        self.settings = settings
        self.metadata = metadata
        self.pipeline = pipeline
        self.pipelines = pipelines
        self.execution_context = execution_context or nullcontext
        self.automation_service = automation_service
        self.stop_event = threading.Event()
        self._status_lock = threading.Lock()
        self._progress: dict[str, tuple[str, float]] = {}
        self._queue: dict = {"available": False}
        self._queue_observed_at: float | None = None
        self._next_queue_observation = 0.0
        self.thread = threading.Thread(
            target=self._owned, args=(self._run_loop,), name="report-worker", daemon=True
        )

    def _threads(self) -> tuple[threading.Thread, ...]:
        return (self.thread,)

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
        stage, stamp = progress.get("report", ("starting", now))
        age = max(0, now - stamp)
        threshold = self.settings.worker_stall_timeout_seconds
        if stage == "idle":
            threshold = max(threshold, self.settings.worker_poll_seconds * 2)
        details = {"report": {"stage": stage, "progress_age_seconds": round(age, 1),
                              "stalled": self.thread.is_alive() and age >= threshold}}
        queue["observation_age_seconds"] = round(max(0, now - observed_at), 1) if observed_at is not None else None
        return {
            "report_alive": self.thread.is_alive(),
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
            except ExecutionLeaseLost:
                logger.warning('report Consumer lost its execution lease or task ownership')
                self.stop_event.set()
                break
            except Exception:
                logger.exception("report queue processing failed")
            finally:
                self._checkpoint("report", "idle")
            self.stop_event.wait(self.settings.worker_poll_seconds)

    @background_llm_work()
    def _process_report(self, job: dict, timezone: ZoneInfo) -> None:
        report_id = job["report_id"]
        started = monotonic()
        with operation_context(report_id=report_id, line_id=job.get('device_id'),
                               request_id=job.get('request_id'), related_kind='reports'):
            try:
                event(logger, logging.INFO, "开始生成报表")
                pipelines = self.pipelines() if callable(self.pipelines) else self.pipelines
                pipeline = self.pipeline if pipelines is None else pipelines[job["device_id"]]
                run = pipeline.run
                parameters = inspect.signature(run).parameters
                with sql_identity(job["device_id"]):
                    if "cancellation_check" in parameters:
                        run(job, cancellation_check=self.stop_event.is_set)
                    else:
                        # Keep small test doubles and older integrations compatible while
                        # the concrete pipeline receives cooperative shutdown checkpoints.
                        run(job)
            except ReportAnalysisPendingError as exc:
                while not self.stop_event.is_set():
                    try:
                        self.metadata.defer_analysis(report_id, str(exc))
                        break
                    except ExecutionLeaseLost:
                        raise
                    except Exception:
                        logger.exception('analysis waiting status write failed', extra={'report_id': report_id})
                        self.stop_event.wait(self.settings.worker_poll_seconds)
                else:
                    return
                event(logger, logging.INFO, '报表延期等待智能分析', error_code='ANALYSIS_WAITING', stage='analyzing',
                      duration_ms=(monotonic() - started) * 1000)
                return
            except ReportPipelineCancelled:
                if self.stop_event.is_set():
                    logger.info("report generation cancelled during worker shutdown", extra={"report_id": report_id})
                    return
                raise
            except ExecutionLeaseLost:
                raise
            # Every failed pipeline stage must move the claimed job out of its active status.
            except Exception as exc:  # noqa: BLE001
                stage = getattr(exc, "report_stage", None)
                code, message = {
                    "querying": ("REPORT_QUERY_FAILED", "查询生产数据失败，请联系管理员"),
                    "calculating": ("REPORT_CALCULATION_FAILED", "计算报表指标失败，请联系管理员"),
                    "analyzing": ("REPORT_ANALYSIS_FAILED", "整理智能分析失败，请联系管理员"),
                    "rendering": ("REPORT_ARCHIVE_FAILED", "生成或归档 HTML 失败，请联系管理员"),
                }.get(stage, ("REPORT_GENERATION_FAILED", "报告生成失败，请联系管理员"))
                event(logger, logging.ERROR, '报表生成失败', error_code=code, stage=stage,
                      error_type=type(exc).__name__, duration_ms=(monotonic() - started) * 1000)
                while not self.stop_event.is_set():
                    try:
                        self.metadata.update_status(
                            report_id, "failed", message,
                            finished_at=datetime.now(timezone).isoformat(timespec="seconds"),
                            error_code=code, error_message=message,
                        )
                        break
                    except ExecutionLeaseLost:
                        raise
                    except Exception:
                        logger.exception("report failure status write failed", extra={"report_id": report_id})
                        self.stop_event.wait(self.settings.worker_poll_seconds)
                else:
                    return
                if self.automation_service is not None:
                    try:
                        self.automation_service.report_failed(
                            report_id, message, datetime.now(timezone)
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
            event(logger, logging.INFO, "报表生成完成", duration_ms=(monotonic() - started) * 1000)
