from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.workers.current_report_refresh_worker import CurrentReportRefreshWorker
from app.workers.leadership_refresh_worker import LeadershipRefreshWorker


TODAY = date(2026, 9, 28)


def test_current_reports_refresh_without_a_browser_and_force_each_analysis() -> None:
    calls = []

    class Query:
        def latest_production_date(self, day, *, force=False):
            calls.append(("latest", day, force))
            return TODAY

        def fetch_workshop_data(self, filters, *, force=False):
            calls.append(("workshop_query", filters.report_date, force))

        def fetch_equipment_data(self, filters, *, force=False):
            calls.append(("equipment_query", filters.report_date, force))
            return SimpleNamespace(records=[])

    class Range:
        def workshop_records(self, start, end):
            calls.append(("workshop_records", start, end))
            return None, []

    class Energy:
        def get_task_day(self, selected, records, *, force=False):
            calls.append(("task_energy", selected, force))
            return {
                "report_date": selected.isoformat(), "records": [],
                "summary": {"energy_data_mode": "unavailable"}, "units": {},
            }

        def get_day(self, selected, *, force=False):
            calls.append(("energy_day", selected, force))

        def filter_task_records(self, records):
            return records

    class Analysis:
        def analyze_workshop(self, report_date, records, **kwargs):
            calls.append(("workshop_analysis", report_date, kwargs["force"]))

        def analyze_equipment(self, report_date, records, **kwargs):
            calls.append(("equipment_analysis", report_date, kwargs["force"]))

    worker = CurrentReportRefreshWorker(
        SimpleNamespace(llm_enabled=True, effective_data_source_mode="sqlserver"),
        Query(), Range(), Energy(), Analysis(),
    )
    worker.run_once(TODAY)
    worker.run_once(TODAY)

    assert calls.count(("workshop_query", TODAY, True)) == 2
    assert calls.count(("equipment_query", TODAY, True)) == 2
    assert calls.count(("task_energy", TODAY, True)) == 2
    assert calls.count(("energy_day", TODAY, True)) == 2
    assert calls.count(("workshop_analysis", TODAY.isoformat(), True)) == 2
    assert calls.count(("equipment_analysis", TODAY.isoformat(), True)) == 2


def test_leadership_refresh_forces_current_month_analysis_even_if_unchanged() -> None:
    calls = []
    month = TODAY.replace(day=1)

    class Leadership:
        def refresh(self, selected, *, bootstrap=False):
            calls.append(("refresh", selected, bootstrap))
            return 1

        def fetch(self, selected):
            calls.append(("fetch", selected))
            return "report"

    class Analysis:
        def analyze_leadership(self, report, *, force=False):
            calls.append(("analysis", report, force))

    worker = LeadershipRefreshWorker(
        SimpleNamespace(llm_enabled=True), Leadership(), Analysis()
    )
    worker._now = lambda: datetime(2026, 9, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    worker._refresh(month, False)
    worker._refresh(month, False)

    assert calls.count(("analysis", "report", True)) == 2
    assert calls.count(("refresh", month, False)) == 2


def test_daily_energy_snapshot_failure_does_not_skip_task_analysis() -> None:
    calls = []

    class Query:
        def fetch_equipment_data(self, _filters, *, force=False):
            return SimpleNamespace(records=[])

    class Energy:
        def get_task_day(self, selected, records, *, force=False):
            return {"report_date": selected.isoformat(), "records": [],
                    "summary": {}, "units": {}}

        def get_day(self, selected, *, force=False):
            raise RuntimeError("meter unavailable")

        def filter_task_records(self, records):
            return records

    class Analysis:
        def analyze_equipment(self, report_date, records, **kwargs):
            calls.append((report_date, kwargs["force"]))

    worker = CurrentReportRefreshWorker(
        SimpleNamespace(llm_enabled=True), Query(), None, Energy(), Analysis(),
    )
    worker._refresh_equipment(TODAY)
    assert calls == [(TODAY.isoformat(), True)]
