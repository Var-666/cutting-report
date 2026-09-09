from __future__ import annotations

import logging
import threading
from datetime import date, datetime
from time import monotonic
from zoneinfo import ZoneInfo

from app.config import Settings
from app.services.leadership_report_service import LeadershipReportService, _previous_month


logger = logging.getLogger(__name__)


class LeadershipRefreshWorker:
    """Refresh SQL Server leadership snapshots without blocking HTTP requests."""

    def __init__(self, settings: Settings, service: LeadershipReportService) -> None:
        self.settings = settings
        self.service = service
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self._lock = threading.Lock()
        self._requests: dict[date, bool] = {}
        self._running: set[date] = set()
        self._last_reconcile_date: date | None = None
        self._next_current_refresh = 0.0
        self.thread = threading.Thread(
            target=self._run_loop,
            name="leadership-refresh-worker",
            daemon=True,
        )

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
        self.thread.start()

    def stop(self) -> None:
        if not self.thread.is_alive():
            return
        self.stop_event.set()
        self.wake_event.set()
        self.thread.join(timeout=5)

    def schedule(self, report_month: date, *, bootstrap: bool = False) -> bool:
        if not self.enabled:
            return False
        month_start = report_month.replace(day=1)
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
                count = self.service.refresh(report_month, bootstrap=bootstrap)
                logger.info(
                    "leadership snapshots refreshed",
                    extra={
                        "report_month": report_month.isoformat(),
                        "bootstrap": bootstrap,
                        "snapshot_count": count,
                    },
                )
            except Exception:
                logger.exception(
                    "leadership snapshot refresh failed",
                    extra={"report_month": report_month.isoformat()},
                )
            finally:
                with self._lock:
                    self._running.discard(report_month)

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
                now_tick + self.service.current_refresh_seconds
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
