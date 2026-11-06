"""Shared day/month energy setup with an explicit isolated state database."""
from datetime import date, datetime, timedelta

from tests.energy_fixtures import GAS, ELECTRIC, configured, MeterStub
from app.database.energy import EnergyReportRepository
from app.services.energy_period_service import EnergyPeriodService

DAY = date(2026, 8, 18)
START = datetime(2026, 8, 18)
NOW = datetime(2026, 9, 1, 12)


def samples(start=START, *, minutes=15, offset=0, days=1):
    result = []
    # Stable increasing cumulative readings, with samples on both sides of midnight.
    for index in range(-2, (days * 1440) // minutes + 3):
        stamp = start + timedelta(minutes=index * minutes, seconds=offset)
        for key, rate in zip((GAS, *ELECTRIC), (1, 2, 3, 4)):
            result.append({"MfId": key, "Mt": stamp.isoformat(), "Mv": str(100000 + index * rate)})
    return result


def service(tmp_path, state_database, *, rows=None, now=NOW, **changes):
    cfg = configured(energy_equipment_ids=(), energy_report_electricity_parameter_ids=ELECTRIC,
                     energy_report_max_gap_seconds=1800, **changes)
    repository = EnergyReportRepository(state_database)
    client = MeterStub(samples() if rows is None else rows)
    return EnergyPeriodService(cfg, repository, client=client, clock=lambda: now), client
