from datetime import datetime
from decimal import Decimal

from app.models.domain import EquipmentRecord
from app.services.equipment_service import (
    mock_energy,
    sort_equipment_records,
    summarize_equipment,
)


def record(task_id: str, cut_length: str, work_time: str) -> EquipmentRecord:
    return EquipmentRecord(
        task_id=task_id,
        equipment_id="CUT-01",
        project_code="P001",
        ship_no="P001",
        segment_name="S01",
        cut_length=Decimal(cut_length),
        work_time=Decimal(work_time),
        gas_consumption=None,
        electricity_consumption=None,
        production_time=datetime(2026, 8, 18, 8, 0),
    )


def test_mock_energy_is_stable_and_summary_uses_it() -> None:
    source = [record("task-1", "100", "3600"), record("task-2", "50", "1800")]

    first = mock_energy(source)
    second = mock_energy(source)
    summary = summarize_equipment(first, "mock")

    assert first[0].gas_consumption == second[0].gas_consumption
    assert first[0].electricity_consumption == second[0].electricity_consumption
    assert first[0].gas_consumption > 0
    assert first[0].electricity_consumption > 0
    assert source[0].gas_consumption is None
    assert summary["task_count"] == 2
    assert summary["energy_data_mode"] == "mock"
    assert summary["total_gas_consumption"] == sum(
        row.gas_consumption for row in first
    )


def test_equipment_records_sort_by_visible_start_time_newest_first() -> None:
    later = record("task-later", "50", "1800")
    earlier = record("task-earlier", "50", "1800")
    missing = record("task-missing", "50", "1800")
    later.production_time = datetime(2026, 8, 18, 9, 30)
    earlier.production_time = datetime(2026, 8, 18, 8, 15)
    missing.production_time = None

    ordered = sort_equipment_records([earlier, missing, later])

    assert [row.task_id for row in ordered] == [
        "task-later",
        "task-earlier",
        "task-missing",
    ]
