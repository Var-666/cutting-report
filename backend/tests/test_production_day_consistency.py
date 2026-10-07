from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.api.workshop import _workshop_trend
from app.config import settings
from app.database.excel_task import ExcelTaskDataSource
from app.database.workshop import WorkshopRepository
from app.models.domain import QueryBundle, ReportFilter, SummaryBaseRecord, WorkshopRecord, to_primitive
from app.services.query_service import QueryService
from app.services.range_report_service import RangeReportService
from app.services.report_analysis_service import workshop_analysis_source_version


DAY = date(2026, 9, 30)
START = datetime(2026, 9, 29, 23, 30)
END = datetime(2026, 9, 30, 1)


def source_row():
    return dict(task_id="night-shift", project_code="P", ship_no="P", segment_name="S", program_no="N",
                equipment_id="Cutter01", cut_length=Decimal("10"), groove_length=Decimal("1"),
                work_time=Decimal("5400"), production_date=DAY, production_time=START,
                production_end_time=END, source_updated_at=END)


def workshop_record(**changes):
    return WorkshopRecord(task_id="night-shift", project_code="P", ship_no="P", segment_name="S",
                          program_no="N", cut_length=Decimal("10"), groove_length=Decimal("1"),
                          work_time=Decimal("5400"), production_time=START, production_date=DAY, **changes)


@pytest.mark.parametrize("date_value", [DAY, DAY.isoformat(), datetime(2026, 9, 30)])
def test_sqlserver_mapping_retains_business_day_for_all_report_paths(date_value):
    service = QueryService(replace(settings, data_source_mode="sqlserver", report_data_cache_seconds=0))
    row = {**source_row(), "production_date": date_value}
    queries = []

    def fetch(query, parameters):
        queries.append(query)
        assert "source.production_date AS production_date" in query
        return [row]

    service.client = SimpleNamespace(fetch_all=fetch)
    filters = ReportFilter(report_date=DAY)
    bundles = [service.fetch_report_data(filters), service.fetch_summary_data(filters), service.fetch_workshop_data(filters)]
    records = [record for bundle in bundles for group in
               (bundle.summary_records, bundle.workshop_records, bundle.equipment_records) for record in group]
    records += service.fetch_equipment_data(filters).records
    assert len(queries) == 6
    assert all(record.production_date == DAY for record in records)
    assert all(record.production_time == START for record in records)


def test_excel_mapping_preserves_op_date_and_actual_energy_interval():
    raw = dict(TASK_ID="night-shift", PROJECT_NAME="P", MACHINE_NO="Cutter01", OP_DATE="2026-09-30",
               START_TIME=START, END_TIME=END, OP_LENGTH_CUT=10000, OP_LENGTH_GROOVE=1000, WORK_TIME=5400)
    service = QueryService(replace(settings, data_source_mode="excel"))
    service.excel = SimpleNamespace(completed_rows=lambda **kwargs: [raw], normalized=ExcelTaskDataSource.normalized)
    bundle = service._excel_data(ReportFilter(report_date=DAY))
    for record in [*bundle.summary_records, *bundle.workshop_records, *bundle.equipment_records]:
        assert record.production_date == DAY
        assert record.production_time == START
    assert bundle.equipment_records[0].production_end_time == END


def test_leadership_daily_totals_and_workshop_trend_use_business_day_without_start_time():
    records = [SummaryBaseRecord(**{key: value for key, value in source_row().items()
               if key not in {"equipment_id", "production_end_time"}})]
    records.append(replace(records[0], task_id="date-only", production_time=None))
    service = RangeReportService(settings,
        SimpleNamespace(fetch_summary_data=lambda _: QueryBundle(records, [], [])), object(), object())
    report = service.leadership(DAY, DAY)
    assert report.current_month_total.task_count == 2
    assert len(report.daily_records) == 1
    assert report.daily_records[0].period_start == DAY
    assert report.daily_records[0].task_count == 2
    assert report.monthly_records[0].period_start == DAY.replace(day=1)
    trend = _workshop_trend([workshop_record(), replace(workshop_record(), task_id="date-only", production_time=None)])
    assert trend[0]["report_date"] == DAY.isoformat()
    assert trend[0]["task_count"] == 2


def test_equipment_daily_rows_keep_cross_day_task_in_selected_day():
    query = QueryService(replace(settings, data_source_mode="sqlserver", report_data_cache_seconds=0))
    query.client = SimpleNamespace(fetch_all=lambda *args: [source_row()])
    source = query.fetch_equipment_data(ReportFilter(report_date=DAY))
    energy = SimpleNamespace(get_task_range=lambda *args, **kwargs: {
        "records": to_primitive(source.records), "summary": {"energy_data_mode": "missing", "total_work_time": 5400},
        "units": {}, "equipment_scope": ["Cutter01"],
    })
    service = RangeReportService(settings, SimpleNamespace(fetch_equipment_data=lambda *args, **kwargs: source), object(), energy)
    result = service.equipment(DAY, DAY)
    assert result["period_report"]["rows"][0]["report_date"] == DAY.isoformat()
    assert result["period_report"]["rows"][0]["task_count"] == 1
    assert result["period_report"]["total_task_count"] == 1


def test_daily_snapshot_restart_and_legacy_migration_preserve_business_date_and_confirmation(tmp_path):
    repository = WorkshopRepository(tmp_path / "metadata.db")
    repository.initialize()
    user = repository.bootstrap_admin(username="admin", display_name="admin", password="test-password", now=END.isoformat())
    report = repository.create_daily_report(report_date=DAY.isoformat(), source_mode="sqlserver",
        records=[workshop_record()], user=user, now=END.isoformat())
    repository.confirm_task(task_id="night-shift", report_id=report["report_id"], user=user, now=END.isoformat())
    # Older snapshots have no date column value. The parent report's selected
    # business day is authoritative, even when the task started the night before.
    with repository.connect() as connection:
        connection.execute("UPDATE workshop_report_items SET production_date = NULL")
    restarted = WorkshopRepository(repository.database_path)
    restarted.initialize()
    state, records = restarted.get_daily_report_records(DAY.isoformat())
    assert records[0].production_date == DAY
    assert records[0].production_time == START
    assert records[0].confirmed_by == user["display_name"]
    assert state["confirmed_count"] == 1
    assert _workshop_trend(records)[0]["report_date"] == DAY.isoformat()


def test_business_date_correction_invalidates_analysis_version():
    record = workshop_record()
    assert workshop_analysis_source_version(DAY.isoformat(), [record]) != workshop_analysis_source_version(
        DAY.isoformat(), [replace(record, production_date=date(2026, 9, 29))])
