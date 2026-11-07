"""Shared task-energy settings, records and cumulative meter readings."""
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal

from tests.line_fixtures import cutting_settings
from app.models.domain import EquipmentRecord

GAS = "bd3179be-c587-f011-a2e9-000c2974f33c"
ELECTRIC = (
    "2b00e015-d987-f011-a2e9-000c2974f33c",
    "98cbd135-d987-f011-a2e9-000c2974f33c",
    "93fae152-d987-f011-a2e9-000c2974f33c",
)
START = datetime(2026, 8, 18, 8)
END = START + timedelta(minutes=2)


def configured(**changes):
    cfg = cutting_settings(data_source_mode="sqlserver", demo_mode=False,
        energy_api_enabled=True, energy_api_base_url="http://energy.test:10091",
        energy_api_username="test@example.test", energy_api_password="test &+# password",
        energy_equipment_ids=("Cutter01",), energy_gas_parameter_id=GAS,
        energy_electricity_parameter_ids=ELECTRIC, energy_gas_unit="Nm³",
        energy_electricity_unit="kWh", energy_gas_factor="1", energy_electricity_factor="1",
        energy_max_gap_seconds=300, energy_allow_interpolation=True, energy_cache_seconds=60,
        energy_sampling_change_at="")
    return replace(cfg, **changes)


def record(task_id="one", **changes):
    row = EquipmentRecord(task_id=task_id, equipment_id="Cutter01", project_code="P1",
        ship_no="S1", segment_name="D1", cut_length=Decimal("100"), work_time=Decimal("30"),
        gas_consumption=None, electricity_consumption=None, production_time=START,
        production_end_time=END)
    return replace(row, **changes)


def readings():
    return [{"MfId": key, "Mt": (START + timedelta(minutes=i)).isoformat(), "Mv": str(100 + i * rate)}
            for key, rate in zip((GAS, *ELECTRIC), (1, 2, 3, 4)) for i in range(3)]


def sparse_november_readings():
    samples = [("01:15:00", "191592.38"), ("01:16:00", "191593.03"),
               ("01:17:00", "191593.42"), ("01:18:00", "191594.11"),
               ("01:23:00", "191595.52"), ("01:28:00", "191596.8"),
               ("01:29:00", "191596.83"), ("01:41:00", "191600.19")]
    return [{"MfId": GAS, "Mt": f"2026-11-07T{stamp}+08:00", "Mv": value}
            for stamp, value in samples]


class MeterStub:
    def __init__(self, rows=None, error=None):
        self.rows = readings() if rows is None else rows
        self.error = error
        self.calls = []

    def fetch(self, ids, start, end, *, force=False):
        self.calls.append((ids, start, end, force))
        if self.error:
            raise self.error
        return self.rows
