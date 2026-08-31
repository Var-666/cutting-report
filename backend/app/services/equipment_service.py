from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from zlib import crc32

from app.models.domain import EquipmentRecord


def sort_equipment_records(records: list[EquipmentRecord]) -> list[EquipmentRecord]:
    return sorted(
        records,
        key=lambda row: (
            row.production_time is None,
            row.production_time.isoformat() if row.production_time else "",
            row.task_id,
        ),
    )


def mock_energy(records: list[EquipmentRecord]) -> list[EquipmentRecord]:
    """Generate stable placeholder energy values until metering data is connected."""
    mocked: list[EquipmentRecord] = []
    for row in records:
        seed = crc32(str(row.task_id).encode("utf-8"))
        cut_length = max(row.cut_length or Decimal("0"), Decimal("0"))
        work_hours = max(row.work_time or Decimal("0"), Decimal("0")) / Decimal(
            "3600"
        )
        gas = (
            cut_length * Decimal("0.018")
            + work_hours * Decimal("5.4")
            + Decimal(seed % 13) / Decimal("20")
        ).quantize(Decimal("0.001"))
        electricity = (
            cut_length * Decimal("0.075")
            + work_hours * Decimal("12.8")
            + Decimal((seed // 13) % 17) / Decimal("20")
        ).quantize(Decimal("0.001"))
        mocked.append(
            replace(
                row,
                gas_consumption=gas,
                electricity_consumption=electricity,
                gas_data_status="mock",
                electricity_data_status="mock",
                energy_note="演示数据，不用于结算或考核",
            )
        )
    return mocked


def summarize_equipment(
    records: list[EquipmentRecord], energy_data_mode: str = "unavailable"
) -> dict:
    gas_values = [row.gas_consumption for row in records if row.gas_consumption is not None]
    electricity_values = [row.electricity_consumption for row in records if row.electricity_consumption is not None]
    return {
        "task_count": len(records),
        "total_work_time": sum(
            (row.work_time for row in records if row.work_time is not None),
            Decimal("0"),
        ),
        "total_gas_consumption": sum(gas_values, Decimal("0")) if gas_values else None,
        "total_electricity_consumption": sum(electricity_values, Decimal("0")) if electricity_values else None,
        "gas_record_count": len(gas_values),
        "electricity_record_count": len(electricity_values),
        "gas_missing_count": len(records) - len(gas_values),
        "electricity_missing_count": len(records) - len(electricity_values),
        "estimated_record_count": sum(
            row.gas_data_status == "interpolated" or row.electricity_data_status == "interpolated"
            for row in records
        ),
        "has_energy_data": any(
            row.gas_consumption is not None
            or row.electricity_consumption is not None
            for row in records
        ),
        "energy_data_mode": energy_data_mode,
    }
