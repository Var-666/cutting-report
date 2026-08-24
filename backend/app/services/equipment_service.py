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
            )
        )
    return mocked


def summarize_equipment(
    records: list[EquipmentRecord], energy_data_mode: str = "unavailable"
) -> dict:
    return {
        "task_count": len(records),
        "total_work_time": sum(
            (row.work_time for row in records if row.work_time is not None),
            Decimal("0"),
        ),
        "total_gas_consumption": sum(
            (
                row.gas_consumption
                for row in records
                if row.gas_consumption is not None
            ),
            Decimal("0"),
        ),
        "total_electricity_consumption": sum(
            (
                row.electricity_consumption
                for row in records
                if row.electricity_consumption is not None
            ),
            Decimal("0"),
        ),
        "has_energy_data": any(
            row.gas_consumption is not None
            or row.electricity_consumption is not None
            for row in records
        ),
        "energy_data_mode": energy_data_mode,
    }
