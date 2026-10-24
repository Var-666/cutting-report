from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import date, datetime
from time import monotonic
from zoneinfo import ZoneInfo

from app.config import Settings
from app.services.leadership_report_service import LeadershipReportService, _previous_month
from app.services.report_analysis_service import ReportAnalysisService
from app.services.llm_service import background_llm_work
from app.workers.parallel_analysis import ParallelAnalysisTasks
from app.workers.bounded_executor import ExecutionQueueFull


logger = logging.getLogger(__name__)


class LeadershipRefreshWorker:
    """Refresh SQL Server leadership snapshots without blocking HTTP requests."""

    def __init__(
        self,
        settings: Settings,
        service: LeadershipReportService,
        analysis_service: ReportAnalysisService | None = None,
        task_queue=None,
    ) -> None:
        self.settings = settings
        self.service = service
        self.analysis_service = analysis_service
        self.task_queue = task_queue
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self._lock = threading.Lock()
        self._requests: dict[date, bool] = {}
        self._running: set[date] = set()
        self._analysis_pending: deque[date] = deque()
        self._analysis_tasks = ParallelAnalysisTasks() if task_queue is None else None
        self._analysis_wake = threading.Event()
        self.analysis_thread = threading.Thread(target=self._analysis_loop, name="leadership-analysis-worker", daemon=True) if task_queue is None else None
        self._last_reconcile_date: date | None = None
        self._next_current_refresh = 0.0
        self.thread = threading.Thread(
            target=self._run_loop,
            name="leadership-refresh-worker",
            daemon=True,
        ) if task_queue is None else None

    @property
    def enabled(self) -> bool:
        return (
            self.service.snapshots is not None
            and self.settings.effective_data_source_mode == "sqlserver"
        )

    def start(self) -> None:
        if not self.enabled:
            return
        today = self._now().date()
        current_month = today.replace(day=1)
        self.schedule(
            current_month,
            bootstrap=self.service.needs_bootstrap(current_month),
        )
        # The startup bootstrap already covers the reconciliation window.
        self._last_reconcile_date = today
        self.analysis_thread.start()
        self.thread.start()

    def stop(self) -> None:
        deadline = monotonic() + 5
        self.stop_event.set()
        self._analysis_tasks.stop(timeout=0)
        self._analysis_wake.set()
        self.wake_event.set()
        with self._lock:
            self._analysis_pending.clear()
        for thread in (self.thread, self.analysis_thread):
            if thread.is_alive():
                thread.join(timeout=max(0, deadline - monotonic()))
        self._analysis_tasks.stop(timeout=max(0, deadline - monotonic()))

    def schedule(self, report_month: date, *, bootstrap: bool = False) -> bool:
        if not self.enabled:
            return False
        month_start = report_month.replace(day=1)
        if self.task_queue is not None:
            return self.task_queue.request_refresh(self.settings.device_id, month_start.isoformat(), bootstrap=bootstrap)
        with self._lock:
            already_scheduled = month_start in self._requests or month_start in self._running
            if month_start in self._running:
                return False
            self._requests[month_start] = (
                bootstrap or self._requests.get(month_start, False)
            )
        self.wake_event.set()
        return not already_scheduled

    def is_refreshing(self, report_month: date) -> bool:
        month_start = report_month.replace(day=1)
        if self.task_queue is not None:
            return self.task_queue.refresh_active(self.settings.device_id, month_start.isoformat())
        with self._lock:
            return month_start in self._requests or month_start in self._running

    def _run_loop(self) -> None:
        while not self.stop_event.is_set():
            self._schedule_periodic_work()
            request = self._claim()
            if request is None:
                self.wake_event.wait(1)
                self.wake_event.clear()
                continue
            report_month, bootstrap = request
            try:
                self._refresh(report_month, bootstrap)
            except Exception:
                logger.exception(
                    "leadership snapshot refresh failed",
                    extra={"report_month": report_month.isoformat()},
                )
            finally:
                with self._lock:
                    self._running.discard(report_month)

    @background_llm_work()
    def _refresh(self, report_month: date, bootstrap: bool) -> None:
        count = self.service.refresh(report_month, bootstrap=bootstrap)
        logger.info(
            "leadership snapshots refreshed",
            extra={
                "report_month": report_month.isoformat(),
                "bootstrap": bootstrap,
                "snapshot_count": count,
            },
        )
        if (
            self.analysis_service is not None
            and self.settings.llm_enabled
            and report_month == self._now().date().replace(day=1)
        ):
            if self.task_queue is not None:
                self.task_queue.enqueue("analysis_leadership", self.settings.device_id, {"date": report_month.isoformat()})
                return
            with self._lock:
                self._analysis_pending.append(report_month)
            self._analysis_wake.set()

    @background_llm_work()
    def _analysis_loop(self) -> None:
        while not self.stop_event.is_set():
            self._analysis_wake.wait(1)
            self._analysis_wake.clear()
            with self._lock:
                if not self._analysis_pending or self.stop_event.is_set():
                    continue
                report_month = self._analysis_pending.popleft()
                if self._analysis_pending:
                    self._analysis_wake.set()
            try:
                self._analysis_tasks.start(self._analyze, report_month)
            except ExecutionQueueFull:
                logger.warning("periodic leadership analysis capacity full; retaining pending task")
                with self._lock:
                    self._analysis_pending.appendleft(report_month)
                self.stop_event.wait(1)
                self._analysis_wake.set()

    def _analyze(self, report_month: date) -> None:
        self.analysis_service.analyze_leadership(
            self.service.fetch(report_month), force=True, publish_latest=True
        )

    def _claim(self) -> tuple[date, bool] | None:
        with self._lock:
            if not self._requests:
                return None
            report_month = next(iter(self._requests))
            bootstrap = self._requests.pop(report_month)
            self._running.add(report_month)
            return report_month, bootstrap

    def _schedule_periodic_work(self) -> None:
        now = self._now()
        current_month = now.date().replace(day=1)
        now_tick = monotonic()
        if now_tick >= self._next_current_refresh:
            self.schedule(current_month)
            self._next_current_refresh = (
                now_tick + max(30, self.settings.report_background_refresh_seconds)
            )

        reconcile_hour = min(
            23, max(0, self.settings.leadership_report_reconcile_hour)
        )
        if now.hour >= reconcile_hour and self._last_reconcile_date != now.date():
            for offset in range(3):
                self.schedule(_previous_month(current_month, offset))
            self._last_reconcile_date = now.date()

    def _now(self) -> datetime:
        return datetime.now(ZoneInfo(self.settings.timezone))
