from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from time import monotonic
from zoneinfo import ZoneInfo

from app.config import Settings
from app.models.domain import ReportFilter
from app.services.energy_period_service import EnergyPeriodService, restore_task_energy_records
from app.services.query_service import QueryService
from app.services.llm_service import background_llm_work
from app.services.range_report_service import RangeReportService
from app.services.report_analysis_service import (
    ReportAnalysisService,
    equipment_analysis_source_version,
)
from app.workers.parallel_analysis import ParallelAnalysisTasks


logger = logging.getLogger(__name__)


def _year_ago(value: date) -> date:
    try:
        return value.replace(year=value.year - 1)
    except ValueError:
        return value.replace(year=value.year - 1, day=28)


def _recent_twelve_month_start(end: date) -> date:
    absolute_month = end.year * 12 + end.month - 1 - 11
    return date(absolute_month // 12, absolute_month % 12 + 1, 1)


@dataclass
class CurrentReportRefreshWorker:
    settings: Settings
    query_service: QueryService
    range_service: RangeReportService
    energy_service: EnergyPeriodService
    analysis_service: ReportAnalysisService

    def __post_init__(self) -> None:
        self.stop_event = threading.Event()
        self._analysis_lock = threading.Lock()
        self._analysis_wake = threading.Event()
        self._analysis_pending: deque[tuple[str, tuple]] = deque()
        self._analysis_tasks = ParallelAnalysisTasks()
        self.analysis_thread = threading.Thread(
            target=self._analysis_loop, name="current-report-analysis-worker", daemon=True
        )
        self.thread = threading.Thread(
            target=self._run_loop, name="current-report-refresh-worker", daemon=True
        )

    @property
    def enabled(self) -> bool:
        return self.settings.effective_data_source_mode == "sqlserver"

    def start(self) -> None:
        if self.enabled:
            self.analysis_thread.start()
            self.thread.start()

    def stop(self) -> None:
        deadline = monotonic() + 5
        self.stop_event.set()
        self._analysis_tasks.stop(timeout=0)
        self._analysis_wake.set()
        with self._analysis_lock:
            self._analysis_pending.clear()
        for thread in (self.thread, self.analysis_thread):
            if thread.is_alive():
                thread.join(timeout=max(0, deadline - monotonic()))
        self._analysis_tasks.stop(timeout=max(0, deadline - monotonic()))

    def _run_loop(self) -> None:
        interval = max(30, self.settings.report_background_refresh_seconds)
        timezone = ZoneInfo(self.settings.timezone)
        while not self.stop_event.is_set():
            started = monotonic()
            try:
                self.run_once(datetime.now(timezone).date())
            except Exception:
                logger.exception("current report background refresh failed")
            self.stop_event.wait(max(1, interval - (monotonic() - started)))

    @background_llm_work()
    def run_once(self, today: date) -> None:
        latest = self.query_service.latest_production_date(today, force=True) or today
        for name, refresh in (
            ("workshop", self._refresh_workshop),
            ("equipment", self._refresh_equipment),
        ):
            if self.stop_event.is_set():
                break
            try:
                refresh(latest)
            except Exception:
                logger.exception("%s background refresh failed", name)

    def _refresh_workshop(self, selected: date) -> None:
        self.query_service.fetch_workshop_data(ReportFilter(report_date=selected), force=True)
        self._schedule_analysis("workshop", (selected,))

    def _analyze_workshop(self, selected: date) -> None:
        _, records = self.range_service.workshop_records(selected, selected)
        previous = selected - timedelta(days=1)
        year_ago = _year_ago(selected)
        _, previous_records = self.range_service.workshop_records(previous, previous)
        _, year_ago_records = self.range_service.workshop_records(year_ago, year_ago)
        self.analysis_service.analyze_workshop(
            selected.isoformat(),
            records,
            comparison_records=previous_records,
            comparison_date=previous.isoformat(),
            year_ago_records=year_ago_records,
            year_ago_date=year_ago.isoformat(),
            force=True,
            publish_latest=True,
        )

    def _refresh_equipment(self, selected: date) -> None:
        equipment = self.query_service.fetch_equipment_data(
            ReportFilter(report_date=selected), force=True
        )
        task_report = self.energy_service.get_task_day(
            selected, equipment.records, force=True
        )
        try:
            self.energy_service.get_day(selected, force=True)
        except Exception:
            logger.exception("equipment daily energy snapshot refresh failed")
        self._schedule_analysis("equipment", (selected, equipment.records, task_report))

    def _analyze_equipment(self, selected: date, source_records: list, task_report: dict) -> None:
        records = restore_task_energy_records(source_records, task_report)
        previous = selected - timedelta(days=1)
        year_ago = _year_ago(selected)
        previous_records = self.query_service.fetch_equipment_data(
            ReportFilter(report_date=previous)
        ).records
        year_ago_records = self.query_service.fetch_equipment_data(
            ReportFilter(report_date=year_ago)
        ).records
        try:
            energy_summary_rows = self.energy_service.get_monthly_range(
                _recent_twelve_month_start(selected), selected
            )["rows"]
        except Exception:
            logger.exception("equipment monthly summary refresh failed")
            energy_summary_rows = []
        self.analysis_service.analyze_equipment(
            selected.isoformat(),
            records,
            energy_data_mode=task_report["summary"].get("energy_data_mode") or "unavailable",
            energy_units=task_report["units"],
            energy_notice=task_report.get("energy_notice") or "",
            energy_summary_rows=energy_summary_rows,
            comparison_records=self.energy_service.filter_task_records(previous_records),
            comparison_date=previous.isoformat(),
            year_ago_records=self.energy_service.filter_task_records(year_ago_records),
            year_ago_date=year_ago.isoformat(),
            source_data_version=equipment_analysis_source_version(task_report),
            force=True,
            publish_latest=True,
        )

    def _schedule_analysis(self, report_type: str, args: tuple) -> None:
        if not self.settings.llm_enabled:
            return
        with self._analysis_lock:
            if self.stop_event.is_set():
                return
            self._analysis_pending.append((report_type, args))
        self._analysis_wake.set()

    @background_llm_work()
    def _analysis_loop(self) -> None:
        handlers = {"workshop": self._analyze_workshop, "equipment": self._analyze_equipment}
        while not self.stop_event.is_set():
            self._analysis_wake.wait(1)
            self._analysis_wake.clear()
            with self._analysis_lock:
                if not self._analysis_pending or self.stop_event.is_set():
                    continue
                report_type, args = self._analysis_pending.popleft()
                if self._analysis_pending:
                    self._analysis_wake.set()
            self._analysis_tasks.start(handlers[report_type], *args)
