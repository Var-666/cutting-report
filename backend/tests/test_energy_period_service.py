from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.auth import require_admin
from app.api.energy import router
from app.database.energy import EnergyReportRepository
from app.database.sqlite import SQLiteDatabase
from app.models.domain import to_primitive
from app.services.energy_api import EnergyApiError
from app.services.energy_period_service import EnergyPeriodService, restore_task_energy_records
from test_energy_service import configured, record, GAS, ELECTRIC, MeterStub


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


def service(tmp_path, *, rows=None, now=NOW, **changes):
    cfg = configured(energy_equipment_ids=(), energy_report_electricity_parameter_ids=ELECTRIC,
                     energy_report_max_gap_seconds=1800, **changes)
    repository = EnergyReportRepository(SQLiteDatabase(tmp_path / "energy.sqlite3"))
    repository.initialize()
    client = MeterStub(samples() if rows is None else rows)
    return EnergyPeriodService(cfg, repository, client=client, clock=lambda: now), client


def test_hourly_day_and_persisted_month_without_equipment_or_tasks(tmp_path):
    energy, client = service(tmp_path)
    report = energy.get_day(DAY)
    assert len(report["rows"]) == 24
    assert report["rows"][0]["gas"]["value"] == 4
    assert report["rows"][0]["electricity"]["value"] == 36
    assert report["summary"]["gas"]["value"] == 96
    assert report["summary"]["electricity"]["value"] == 864
    assert report["summary"]["electricity"]["status"] == "measured"
    assert report["summary"]["gas"]["valid_count"] == 24
    assert report["rows"][-1]["end"] == "2026-08-19T00:00:00"
    assert set(client.calls[0][0]) == {GAS, *ELECTRIC}
    assert client.calls[0][1] == START - timedelta(minutes=30)
    assert client.calls[0][2] == START + timedelta(days=1, minutes=30)
    assert energy.get_day(DAY)["from_cache"]
    assert len(client.calls) == 1
    month = energy.get_month("2026-08")
    assert len(month["rows"]) == 31
    assert month["rows"][17]["electricity"]["value"] == 864
    assert month["rows"][0]["electricity"]["value"] is None
    assert month["summary"]["electricity"]["value"] == 864
    assert month["summary"]["electricity"]["status"] == "partial"
    assert month["summary"]["electricity"]["valid_count"] == 1
    assert month["saved_day_count"] == 1
    assert len(client.calls) == 1  # month view does not silently query 31 external days
    again = EnergyPeriodService(energy.settings, energy.repository, client=MeterStub([]), clock=lambda: NOW)
    assert again.get_month("2026-08")["rows"][17]["gas"]["value"] == 96


def test_complete_month_sums_all_days_and_both_channels(tmp_path):
    energy, _ = service(tmp_path)
    for number in range(1, 32):
        stamp = date(2026, 8, number)
        report = energy.calculate_day(stamp, samples(datetime.combine(stamp, datetime.min.time())))
        report.update(updated_at=NOW.isoformat(), saved=True, sync_ok=True)
        energy.repository.save_day(energy.source_key, to_primitive(report))
    result = energy.get_month("2026-08")
    assert result["summary"]["gas"]["value"] == 96 * 31
    assert result["summary"]["electricity"]["value"] == 864 * 31
    assert result["summary"]["electricity"]["status"] == "measured"
    assert result["summary"]["electricity"]["complete_count"] == 31


def test_task_day_uses_report_meter_group_without_equipment_binding(tmp_path):
    energy, client = service(tmp_path)
    start = START + timedelta(hours=8)
    task = record(
        production_time=start,
        production_end_time=start + timedelta(minutes=30),
        work_time=Decimal("1800"),
        equipment_id=None,
    )

    report = energy.get_task_day(DAY, [task])

    assert report["records"][0]["gas_consumption"] == 2
    assert report["records"][0]["electricity_consumption"] == 18
    assert report["summary"]["total_gas_consumption"] == 2
    assert report["summary"]["total_electricity_consumption"] == 18
    assert report["summary"]["gas_record_count"] == 1
    assert report["summary"]["electricity_record_count"] == 1
    assert "不要求维护设备绑定" in report["energy_notice"]
    assert set(client.calls[0][0]) == {GAS, *ELECTRIC}


def test_task_report_can_restore_typed_records_for_analysis(tmp_path):
    energy, _ = service(tmp_path)
    start = START + timedelta(hours=8)
    source = record(
        production_time=start,
        production_end_time=start + timedelta(minutes=30),
        work_time=Decimal("1800"),
        equipment_id=None,
    )

    report = energy.get_task_day(DAY, [source])
    restored = restore_task_energy_records([source], report)

    assert restored[0].gas_consumption == Decimal("2")
    assert restored[0].electricity_consumption == Decimal("18")
    assert restored[0].gas_data_status == "measured"
    assert restored[0].electricity_data_status == "measured"
    assert restored[0].energy_note == report["records"][0]["energy_note"]


def test_overlapping_tasks_are_listed_but_not_double_counted(tmp_path):
    energy, _ = service(tmp_path)
    start = START + timedelta(hours=8)
    rows = [
        record("one", production_time=start, production_end_time=start + timedelta(minutes=30)),
        record("two", production_time=start + timedelta(minutes=15), production_end_time=start + timedelta(minutes=45)),
    ]

    report = energy.get_task_day(DAY, rows)

    assert report["summary"]["task_count"] == 2
    assert report["summary"]["total_electricity_consumption"] is None
    assert all(row["electricity_data_status"] == "overlap" for row in report["records"])
    assert "时间重叠任务" in report["energy_notice"]


def test_force_and_expired_cache_fetch_again(tmp_path):
    energy, client = service(tmp_path)
    energy.get_day(DAY)
    energy.get_day(DAY, force=True)
    assert len(client.calls) == 2
    assert client.calls[-1][-1] is True
    energy.clock = lambda: NOW + timedelta(hours=2)
    energy.get_day(DAY)
    assert len(client.calls) == 3


def test_missing_midnight_never_uses_first_last_available_as_full_day(tmp_path):
    rows = [row for row in samples() if START + timedelta(minutes=15) <= datetime.fromisoformat(row["Mt"]) <= START + timedelta(hours=23, minutes=45)]
    energy, _ = service(tmp_path, rows=rows)
    report = energy.get_day(DAY)
    assert report["rows"][0]["gas"]["value"] is None
    assert report["rows"][-1]["gas"]["value"] is None
    assert report["summary"]["gas"]["value"] == 88
    assert report["summary"]["gas"]["status"] == "partial"
    assert report["summary"]["gas"]["valid_count"] == 22


def test_one_minute_and_fifteen_minute_sampling_are_supported(tmp_path):
    for interval in (1, 15):
        energy, _ = service(tmp_path, rows=samples(minutes=interval))
        report = energy.get_day(DAY, force=True)
        assert report["summary"]["gas"]["complete_count"] == 24
        assert report["summary"]["gas"]["value"] == 1440 / interval


def test_offset_boundaries_interpolate_and_strict_mode_keeps_missing(tmp_path):
    energy, _ = service(tmp_path, rows=samples(offset=20))
    report = energy.get_day(DAY)
    assert report["summary"]["gas"]["value"] == 96
    assert report["summary"]["gas"]["status"] == "interpolated"
    assert report["summary"]["gas"]["estimated_count"] == 24
    energy.settings = replace(energy.settings, energy_allow_interpolation=False)
    report = energy.get_day(DAY)
    assert report["summary"]["gas"]["value"] is None


def test_missing_one_electric_meter_never_sums_remaining_two(tmp_path):
    energy, _ = service(tmp_path, rows=[row for row in samples() if row["MfId"] != ELECTRIC[2]])
    report = energy.get_day(DAY)
    assert report["summary"]["electricity"]["value"] is None
    assert report["summary"]["gas"]["value"] == 96


def test_sample_gap_and_counter_reset_are_not_consumption(tmp_path):
    rows = samples()
    rows = [row for row in rows if not (row["MfId"] == GAS and START + timedelta(minutes=15) <= datetime.fromisoformat(row["Mt"]) <= START + timedelta(minutes=45))]
    for row in rows:
        if row["MfId"] == ELECTRIC[0] and row["Mt"] == START.replace(minute=30).isoformat():
            row["Mv"] = "0"
    energy, _ = service(tmp_path, rows=rows)
    report = energy.get_day(DAY)
    assert report["rows"][0]["gas"]["status"] == "gap"
    assert report["rows"][0]["electricity"]["status"] == "counter_reset"
    assert report["rows"][0]["electricity"]["value"] is None


def test_real_zero_is_not_missing(tmp_path):
    energy, _ = service(tmp_path, rows=[{**row, "Mv": "100"} for row in samples()])
    report = energy.get_day(DAY)
    assert report["summary"]["electricity"]["value"] == 0
    assert report["summary"]["electricity"]["status"] == "measured"


def test_current_day_ignores_unfinished_hours_and_future_dates(tmp_path):
    now = START + timedelta(hours=12, minutes=35)
    energy, client = service(tmp_path, now=now)
    report = energy.get_day(DAY)
    assert report["rows"][11]["gas"]["value"] == 4
    assert report["rows"][12]["gas"]["status"] == "pending"
    assert report["summary"]["gas"]["valid_count"] == 12
    assert report["summary"]["gas"]["status"] == "partial"
    assert client.calls[0][2] == now
    future = energy.get_day(DAY + timedelta(days=1))
    assert all(row["gas"]["status"] == "future" for row in future["rows"])
    assert len(client.calls) == 1
    month = energy.get_month("2026-08")
    assert month["elapsed_day_count"] == 18
    assert month["rows"][18]["is_future"]
    assert month["summary"]["gas"]["expected_count"] == 18


@pytest.mark.parametrize("month,length", [("2024-02", 29), ("2025-02", 28), ("2026-04", 30), ("2026-08", 31)])
def test_full_calendar_month_including_leap_year(tmp_path, month, length):
    energy, client = service(tmp_path)
    report = energy.get_month(month)
    assert len(report["rows"]) == length
    assert report["summary"]["gas"]["value"] is None
    assert not client.calls


def test_api_failure_or_data_regression_preserves_snapshot(tmp_path):
    energy, client = service(tmp_path)
    original = energy.get_day(DAY)
    client.error = EnergyApiError("能管接口连接失败")
    result = energy.get_day(DAY, force=True)
    assert result["summary"] == original["summary"]
    assert not result["sync_ok"]
    assert "上次已保存" in result["warning"]
    client.error, client.rows = None, []
    result = energy.get_day(DAY, force=True)
    assert not result["sync_ok"]
    assert "有效时段减少" in result["warning"]
    assert energy.get_month("2026-08")["rows"][17]["electricity"]["value"] == 864


def test_equal_coverage_does_not_erase_previously_valid_hours(tmp_path):
    # Both queries have 23 good gas hours, but the missing hour has moved.
    def omit_hour(hour):
        return [row for row in samples() if not (row["MfId"] == GAS and
            START + timedelta(hours=hour) < datetime.fromisoformat(row["Mt"]) < START + timedelta(hours=hour + 1))]
    energy, client = service(tmp_path, rows=omit_hour(1))
    original = energy.get_day(DAY)
    assert original["summary"]["gas"]["valid_count"] == 23
    client.rows = omit_hour(2)
    refreshed = energy.get_day(DAY, force=True)
    assert not refreshed["sync_ok"]
    assert refreshed["rows"][2]["gas"]["value"] == 4


def test_source_changes_do_not_reuse_old_meter_results(tmp_path):
    energy, _ = service(tmp_path)
    energy.get_day(DAY)
    for key, value in [("energy_api_username", "another@example.test"), ("energy_gas_factor", "2"), ("energy_electricity_unit", "")]:
        other = EnergyPeriodService(replace(energy.settings, **{key: value}), energy.repository, clock=lambda: NOW)
        assert other.source_key != energy.source_key
        assert other.get_month("2026-08")["saved_day_count"] == 0


def test_disabled_or_missing_login_never_fetches_or_invents_data(tmp_path):
    energy, client = service(tmp_path, energy_api_enabled=False)
    result = energy.get_day(DAY)
    assert result["summary"]["gas"]["value"] is None
    assert not result["saved"]
    assert not client.calls
    energy.settings = replace(energy.settings, energy_api_enabled=True, energy_api_password="")
    assert "账号或密码" in energy.get_day(DAY)["warning"]
    assert not client.calls


@pytest.mark.parametrize("changes", [
    {"energy_report_electricity_parameter_ids": ()},
    {"energy_report_electricity_parameter_ids": (ELECTRIC[0], ELECTRIC[0])},
    {"energy_gas_parameter_id": "bad"}, {"energy_report_max_gap_seconds": 0},
    {"energy_electricity_factor": "NaN"},
])
def test_bad_config_is_reported_without_network(tmp_path, changes):
    energy, client = service(tmp_path)
    energy.settings = replace(energy.settings, **changes)
    report = energy.get_day(DAY)
    assert not report["sync_ok"]
    assert report["configuration_error"]
    assert not client.calls


def test_empty_legacy_electricity_unit_uses_confirmed_kwh(tmp_path):
    energy, _ = service(tmp_path, energy_electricity_unit="")
    report = energy.get_day(DAY)
    assert report["units"]["electricity"] == "kWh"
    assert report["summary"]["electricity"]["value"] == 864
    assert "不标作 kWh" not in report["notice"]


def test_snapshots_do_not_contain_credentials(tmp_path):
    energy, _ = service(tmp_path)
    energy.get_day(DAY)
    with energy.repository.connect() as conn:
        raw = conn.execute("SELECT report_json FROM energy_daily_snapshots").fetchone()[0]
    assert energy.settings.energy_api_username not in raw
    assert energy.settings.energy_api_password not in raw
    assert "AuthId" not in raw


def test_day_month_routes_and_bad_month(tmp_path):
    energy, _ = service(tmp_path)
    app = FastAPI()
    app.state.energy_period_service = energy
    start = START + timedelta(hours=8)
    app.state.query_service = SimpleNamespace(fetch_equipment_data=lambda *_args, **_kwargs: SimpleNamespace(
        records=[record(production_time=start, production_end_time=start + timedelta(minutes=30),
                        work_time=Decimal("1800"), equipment_id=None)],
        data_updated_at=start + timedelta(minutes=30),
    ))
    app.dependency_overrides[require_admin] = lambda: {"role": "admin"}
    app.include_router(router)

    @app.middleware("http")
    async def request_id(request, call_next):
        request.state.request_id = "period-test"
        return await call_next(request)

    client = TestClient(app)
    result = client.get("/api/v1/energy-report/day?report_date=2026-08-18&refresh=true")
    assert result.status_code == 200
    assert result.json()["data"]["summary"]["gas"]["value"] == 96
    result = client.get("/api/v1/energy-report/month?report_month=2026-08")
    assert result.status_code == 200
    assert result.json()["data"]["saved_day_count"] == 1
    result = client.get("/api/v1/energy-report/tasks?report_date=2026-08-18")
    assert result.status_code == 200
    assert result.json()["data"]["records"][0]["electricity_consumption"] == 18
    assert result.json()["data"]["summary"]["task_count"] == 1
    for value in ("2026-13", "2026-1", "0000-01", "invalid"):
        assert client.get("/api/v1/energy-report/month", params={"report_month": value}).status_code == 422
    assert client.get("/api/v1/energy-report/day?report_date=9999-12-31").status_code == 422
