from __future__ import annotations

import logging
import threading
from datetime import date

from app.config import Settings
from app.services.energy_period_service import EnergyPeriodService


logger = logging.getLogger(__name__)


def _shift_month(month_start: date, offset: int) -> date:
    absolute = month_start.year * 12 + month_start.month - 1 + offset
    return date(absolute // 12, absolute % 12 + 1, 1)


class EnergyHistoryWorker:
    """Fill persisted daily energy snapshots outside user-facing requests."""

    def __init__(self, settings: Settings, service: EnergyPeriodService) -> None:
        self.settings = settings
        self.service = service
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self._lock = threading.Lock()
        self._requests: dict[date, None] = {}
        self._running: set[date] = set()
        self.thread = threading.Thread(
            target=self._run_loop,
            name="energy-history-worker",
            daemon=True,
        )

    @property
    def enabled(self) -> bool:
        return (
            self.settings.effective_data_source_mode == "sqlserver"
            and self.settings.energy_api_enabled
        )

    def start(self) -> None:
        if not self.enabled:
            return
        today = self.service.now().date()
        self.schedule_range(_shift_month(today.replace(day=1), -11), today)
        self.thread.start()

    def stop(self) -> None:
        if not self.thread.is_alive():
            return
        self.stop_event.set()
        self.wake_event.set()
        self.thread.join(timeout=5)

    def schedule_range(self, start_date: date, end_date: date) -> bool:
        if not self.enabled or end_date < start_date:
            return False
        first = start_date.replace(day=1)
        last = end_date.replace(day=1)
        months = []
        current = last
        while current >= first:
            months.append(current)
            current = _shift_month(current, -1)
        added = False
        with self._lock:
            for month in months:
                if month not in self._requests and month not in self._running:
                    self._requests[month] = None
                    added = True
        if added:
            self.wake_event.set()
        return added

    def is_syncing_range(self, start_date: date, end_date: date) -> bool:
        first = start_date.replace(day=1)
        last = end_date.replace(day=1)
        with self._lock:
            active = set(self._requests) | self._running
        return any(first <= month <= last for month in active)

    def _claim(self) -> date | None:
        with self._lock:
            if not self._requests:
                return None
            month = next(iter(self._requests))
            self._requests.pop(month)
            self._running.add(month)
            return month

    def _run_loop(self) -> None:
        while not self.stop_event.is_set():
            month = self._claim()
            if month is None:
                self.wake_event.wait(1)
                self.wake_event.clear()
                continue
            try:
                saved = self.service.sync_month(month)
                logger.info(
                    "energy history month synchronized",
                    extra={"report_month": month.isoformat(), "saved_days": saved},
                )
            except Exception:
                logger.exception(
                    "energy history month synchronization failed",
                    extra={"report_month": month.isoformat()},
                )
                # One unavailable account/platform should not immediately emit
                # the same failure for every queued month. A later page request
                # can schedule the range again after the transient backoff.
                with self._lock:
                    self._requests.clear()
            finally:
                with self._lock:
                    self._running.discard(month)
