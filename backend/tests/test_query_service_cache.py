from datetime import date, datetime
from pathlib import Path

from app.config import Settings
from app.models.domain import EquipmentRecord, ReportFilter
from app.services.query_service import EquipmentQueryResult, QueryService


def _service(tmp_path: Path) -> QueryService:
    return QueryService(
        Settings(
            project_root=tmp_path,
            demo_mode=False,
            data_source_mode="sqlserver",
            report_data_cache_seconds=300,
        )
    )


def _equipment_result(task_id: str) -> EquipmentQueryResult:
    return EquipmentQueryResult(
        records=[
            EquipmentRecord(
                task_id=task_id,
                equipment_id="CUT-01",
                project_code="P001",
                ship_no="P001",
                segment_name="S01",
                cut_length=None,
                work_time=None,
                gas_consumption=None,
                electricity_consumption=None,
            )
        ],
        data_updated_at=datetime(2026, 8, 25, 10, 0),
    )


def test_equipment_query_reuses_same_date_and_force_refreshes(
    tmp_path: Path, monkeypatch
) -> None:
    service = _service(tmp_path)
    calls = 0

    def fetch(_filters: ReportFilter) -> EquipmentQueryResult:
        nonlocal calls
        calls += 1
        return _equipment_result(f"task-{calls}")

    monkeypatch.setattr(service, "_sqlserver_equipment_data", fetch)
    filters = ReportFilter(report_date=date(2026, 8, 25))

    first = service.fetch_equipment_data(filters)
    second = service.fetch_equipment_data(filters)
    refreshed = service.fetch_equipment_data(filters, force=True)

    assert calls == 2
    assert second is first
    assert refreshed.records[0].task_id == "task-2"


def test_latest_date_query_is_shared_and_can_be_refreshed(
    tmp_path: Path, monkeypatch
) -> None:
    service = _service(tmp_path)
    calls = 0

    def fetch(_on_or_before: date) -> date:
        nonlocal calls
        calls += 1
        return date(2026, 8, 24 + calls)

    monkeypatch.setattr(service, "_latest_production_date_uncached", fetch)
    selected = date(2026, 8, 25)

    assert service.latest_production_date(selected) == date(2026, 8, 25)
    assert service.latest_production_date(selected) == date(2026, 8, 25)
    assert service.latest_production_date(selected, force=True) == date(2026, 8, 26)
    assert calls == 2


def test_sqlserver_equipment_fetch_executes_only_equipment_query(
    tmp_path: Path, monkeypatch
) -> None:
    service = _service(tmp_path)
    calls: list[str] = []
    parameters_seen: list[tuple] = []
    updated_at = datetime(2026, 8, 25, 12, 0)

    def fetch_all(sql: str, parameters: tuple) -> list[dict]:
        calls.append(sql)
        parameters_seen.append(parameters)
        return [
            {
                "task_id": "task-1",
                "equipment_id": "CUT-01",
                "project_code": "P001",
                "ship_no": "P001",
                "segment_name": "S01",
                "cut_length": "10.125",
                "work_time": "60",
                "production_time": datetime(2026, 8, 25, 8, 0),
                "production_end_time": updated_at,
                "source_updated_at": updated_at,
                "gas_consumption": None,
                "electricity_consumption": None,
                "remark": None,
            }
        ]

    monkeypatch.setattr(service.client, "fetch_all", fetch_all)

    result = service.fetch_equipment_data(
        ReportFilter(report_date=date(2026, 8, 25))
    )

    assert len(calls) == 1
    assert "MACHINE_NO" in calls[0]
    assert parameters_seen[0][0] == "Cutter01"
    assert result.records[0].task_id == "task-1"
    assert result.records[0].production_end_time == updated_at
    assert result.data_updated_at == updated_at


def test_summary_and_workshop_ranges_each_execute_one_cached_query(
    tmp_path: Path, monkeypatch
) -> None:
    service = _service(tmp_path)
    calls: list[str] = []
    updated_at = datetime(2026, 8, 25, 12, 0)

    def fetch_all(sql: str, _parameters: tuple) -> list[dict]:
        calls.append(sql)
        return [
            {
                "task_id": "task-1",
                "project_code": "P001",
                "ship_no": "P001",
                "segment_name": "S01",
                "program_no": "OP-1",
                "cut_length": "10",
                "groove_length": "1",
                "work_time": "60",
                "production_time": datetime(2026, 8, 25, 8, 0),
                "source_updated_at": updated_at,
                "remark": None,
            }
        ]

    monkeypatch.setattr(service.client, "fetch_all", fetch_all)
    filters = ReportFilter(report_date=date(2026, 8, 25))

    summary = service.fetch_summary_data(filters)
    workshop = service.fetch_workshop_data(filters)
    assert service.fetch_summary_data(filters) is summary
    assert service.fetch_workshop_data(filters) is workshop

    assert len(calls) == 2
    assert len(summary.summary_records) == 1
    assert len(workshop.workshop_records) == 1


def test_equipment_cache_and_query_locks_are_bounded(
    tmp_path: Path, monkeypatch
) -> None:
    service = _service(tmp_path)
    monkeypatch.setattr(
        service,
        "_sqlserver_equipment_data",
        lambda _filters: EquipmentQueryResult([]),
    )

    for offset in range(140):
        service.fetch_equipment_data(ReportFilter(report_date=date(2025, 1, 1)))
        service.fetch_equipment_data(
            ReportFilter(
                report_date=date.fromordinal(date(2025, 1, 1).toordinal() + offset)
            )
        )

    assert len(service._equipment_cache) <= 128
    assert len(service._equipment_query_locks) == 32
