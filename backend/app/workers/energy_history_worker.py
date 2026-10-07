from __future__ import annotations

import logging
import threading
from datetime import date, timedelta

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
        self.repository = service.repository
        self.source_key = service.history_source_key
        self._pending_finish: tuple | None = None
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
        self.repository.recover_history_queue(self.source_key, self._now())
        today = self.service.now().date()
        self.schedule_range(_shift_month(today.replace(day=1), -11), today)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.wake_event.set()
        if self.thread.ident is not None:
            self.thread.join(timeout=5)
        if self.thread.is_alive():
            logger.warning("energy history worker still running after shutdown timeout")

    def schedule_range(self, start_date: date, end_date: date) -> bool:
        if not self.enabled or end_date < start_date:
            return False
        first = start_date.replace(day=1)
        last = min(end_date, self.service.now().date()).replace(day=1)
        months = []
        current = last
        while current >= first:
            months.append(current)
            current = _shift_month(current, -1)
        added = self.repository.enqueue_months(
            self.source_key, [month.isoformat() for month in months], self._now(),
        ) > 0
        if added:
            self.wake_event.set()
        return added

    def _now(self) -> str:
        return self.service.now().isoformat(timespec="seconds")

    def is_syncing_range(self, start_date: date, end_date: date) -> bool:
        return self.repository.history_range_active(
            self.source_key, start_date.replace(day=1).isoformat(),
            end_date.replace(day=1).isoformat(), self._now(),
        )

    def _claim(self) -> dict | None:
        return self.repository.claim_history_month(self.source_key, self._now())

    def _process(self, claim: dict) -> None:
        month = date.fromisoformat(claim["report_month"])
        error_code = None
        try:
            saved = self.service.sync_month(month)
            logger.info("energy history month synchronized",
                        extra={"report_month": month.isoformat(), "saved_days": saved})
            delay = max(60, self.settings.energy_report_snapshot_ttl_seconds)
            if month == self.service.now().date().replace(day=1):
                delay = 60
        except Exception as exc:
            logger.exception("energy history month synchronization failed",
                             extra={"report_month": month.isoformat()})
            error_code = type(exc).__name__[:80]
            delay = min(30 * 2 ** min(max(claim["attempt_count"] - 1, 0), 7), 3600)
        now = self.service.now()
        self._pending_finish = (
            claim, (now + timedelta(seconds=delay)).isoformat(timespec="seconds"),
            now.isoformat(timespec="seconds"), error_code,
        )
        self._flush_pending_finish()

    def _flush_pending_finish(self) -> None:
        if self._pending_finish is None:
            return
        claim, next_attempt, now, error_code = self._pending_finish
        if not self.repository.reschedule_history_month(claim, next_attempt, now, error_code):
            logger.warning("energy history claim no longer owned: %s", claim["report_month"])
        self._pending_finish = None

    def _run_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                self._flush_pending_finish()
                claim = self._claim()
                if claim is not None:
                    self._process(claim)
                    continue
            except Exception:
                # Retry completion writes without repeating the external query.
                # A crash leaves the durable claim for startup recovery.
                logger.exception("energy history queue processing failed")
            self.wake_event.wait(1)
            self.wake_event.clear()
