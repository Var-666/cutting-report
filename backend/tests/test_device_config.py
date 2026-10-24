from __future__ import annotations

import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from app.device_config import load_device_catalog


ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "backend/app/configuration/devices.json"
LEGACY_KEYS = (
    "REPORT_MACHINE_NO", "ENERGY_GAS_PARAMETER_ID", "ENERGY_REPORT_EQUIPMENT_IDS",
    "ENERGY_REPORT_ELECTRICITY_PARAMETER_IDS", "ALARM_MONITOR_MACHINE_NOS", "ALARM_MONITOR_START_AT", "ENERGY_EQUIPMENT_IDS", "ENERGY_ELECTRICITY_PARAMETER_IDS",
)


def payload():
    return json.loads(PROFILE.read_text(encoding="utf-8"))


def save(tmp_path, value):
    path = tmp_path / "devices.json"
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def settings_from(monkeypatch, path, **overrides):
    # Isolate configuration loading from deployment secrets and other tests.
    monkeypatch.setattr("app.configuration_sources.load_environment",
                        lambda _root, process: (process, dict.fromkeys(process, "process"), []))
    for key in LEGACY_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DEVICE_CONFIG_PATH", str(path))
    for key, value in overrides.items():
        monkeypatch.setenv(key, value)
    spec = importlib.util.spec_from_file_location("_device_config_test_settings", ROOT / "backend/app/config.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(("override", "expected"), [(None, 300), (450, 450)])
def test_analysis_refresh_defaults_to_five_minutes_and_preserves_deployment_override(monkeypatch, override, expected):
    monkeypatch.delenv("REPORT_BACKGROUND_REFRESH_SECONDS", raising=False)
    overrides = {} if override is None else {"REPORT_BACKGROUND_REFRESH_SECONDS": str(override)}
    cfg = settings_from(monkeypatch, PROFILE, **overrides).settings
    assert cfg.report_background_refresh_seconds == expected
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "\nREPORT_BACKGROUND_REFRESH_SECONDS=300\n" in example


@pytest.mark.parametrize(("override", "expected"), [(None, 10), (4, 4), (10, 10)])
def test_model_capacity_defaults_to_ten_and_preserves_deployment_override(monkeypatch, override, expected):
    monkeypatch.delenv("LLM_MAX_CONCURRENT", raising=False)
    overrides = {} if override is None else {"LLM_MAX_CONCURRENT": str(override)}
    cfg = settings_from(monkeypatch, PROFILE, **overrides).settings
    assert cfg.llm_max_concurrent == expected
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "\nLLM_MAX_CONCURRENT=10\n" in example


@pytest.mark.parametrize(("override", "expected"), [(None, 180), (90, 90), (180, 180)])
def test_model_response_timeout_defaults_to_three_minutes_and_preserves_deployment_override(monkeypatch, override, expected):
    monkeypatch.delenv("LLM_TIMEOUT_SECONDS", raising=False)
    overrides = {} if override is None else {"LLM_TIMEOUT_SECONDS": str(override)}
    cfg = settings_from(monkeypatch, PROFILE, **overrides).settings
    assert cfg.llm_timeout == expected
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "\nLLM_TIMEOUT_SECONDS=180\n" in example


def test_cutter01_profile_preserves_confirmed_identity_meters_and_alarm_window(monkeypatch, tmp_path):
    # Device configuration must survive startup from a different working folder.
    monkeypatch.chdir(tmp_path)
    module = settings_from(monkeypatch, PROFILE.relative_to(ROOT),
                           ENERGY_API_ENABLED="false", ALARM_MONITOR_ENABLED="false")
    cfg = module.settings
    device = module.DEVICE_CATALOG.default_device
    assert [(line.id, line.name) for line in module.DEVICE_CATALOG.production_lines] == [("Cutter01", "20KW切割机"), ("Cutter02", "30KW切割机")]
    assert [(item.id, item.name) for item in module.DEVICE_CATALOG.devices] == [("Cutter01", "20KW切割机"), ("Cutter02", "30KW切割机")]
    assert device.data_source == "production"
    assert cfg.report_machine_no == "Cutter01"
    assert cfg.energy_report_equipment_ids == ("Cutter01",)
    assert cfg.energy_gas_parameter_id == "bd3179be-c587-f011-a2e9-000c2974f33c"
    assert cfg.energy_report_electricity_parameter_ids == (
        "2b00e015-d987-f011-a2e9-000c2974f33c", "98cbd135-d987-f011-a2e9-000c2974f33c",
        "93fae152-d987-f011-a2e9-000c2974f33c",
    )
    assert cfg.alarm_monitor_machine_nos == ("Cutter01",)
    assert cfg.alarm_monitor_start_at == "2026-09-29 00:00:00"
    assert not cfg.energy_api_enabled and not cfg.alarm_monitor_enabled


def test_json_values_feed_runtime_and_conflicting_legacy_overrides_stop_startup(monkeypatch, tmp_path):
    value = payload()
    device = value["devices"][0]
    device["machine_no"] = "FixtureDevice"
    device["energy"]["equipment_ids"] = ["FixtureDevice"]
    device["energy"]["gas_parameter_id"] = "00000000-0000-4000-8000-000000000001"
    device["energy"]["electricity_parameter_ids"] = ["00000000-0000-4000-8000-000000000002"]
    device["alarm"]["start_at"] = "2026-10-01 00:00:00"
    path = save(tmp_path, value)
    cfg = settings_from(monkeypatch, path).settings
    assert cfg.report_machine_no == "FixtureDevice"
    assert cfg.energy_report_equipment_ids == ("FixtureDevice",)
    assert cfg.energy_gas_parameter_id == device["energy"]["gas_parameter_id"]
    assert cfg.energy_report_electricity_parameter_ids == tuple(device["energy"]["electricity_parameter_ids"])
    assert cfg.alarm_monitor_machine_nos == ("FixtureDevice",)
    assert cfg.alarm_monitor_start_at == "2026-10-01 00:00:00"
    with pytest.raises(ValueError, match="旧设备环境配置与设备目录冲突") as error:
        settings_from(monkeypatch, path, REPORT_MACHINE_NO="ExistingDevice",
            ENERGY_REPORT_EQUIPMENT_IDS="ExistingDevice", ENERGY_GAS_PARAMETER_ID="existing-gas",
            ENERGY_REPORT_ELECTRICITY_PARAMETER_IDS="existing-electricity",
            ALARM_MONITOR_MACHINE_NOS="ExistingDevice", ALARM_MONITOR_START_AT="2026-09-29 00:00:00")
    assert "REPORT_MACHINE_NO" in str(error.value) and "ExistingDevice" in str(error.value)
    cfg = settings_from(monkeypatch, path, REPORT_MACHINE_NO="FixtureDevice").settings
    assert cfg.report_machine_no == "FixtureDevice"



@pytest.mark.parametrize("fault", [
    "default", "line_reference", "duplicate_device", "duplicate_line", "gas_id",
    "duplicate_meter", "empty_meter", "wrong_scope", "alarm_date", "unknown_field", "version", "shared_scope", "shared_meter",
])
def test_invalid_profile_cannot_silently_select_a_device_or_wrong_meter(tmp_path, fault):
    value = payload()
    device = value["devices"][0]
    if fault == "default": value["default_device_id"] = "missing"
    if fault == "line_reference": device["production_line_id"] = "missing"
    if fault == "duplicate_device": value["devices"].append({**deepcopy(device), "id": "cutter01"})
    if fault == "duplicate_line": value["production_lines"].append({"id": "cutter01", "name": "重复"})
    if fault == "gas_id": device["energy"]["gas_parameter_id"] = "invalid"
    if fault == "duplicate_meter": device["energy"]["electricity_parameter_ids"].append(device["energy"]["gas_parameter_id"])
    if fault == "empty_meter": device["energy"]["electricity_parameter_ids"] = []
    if fault == "wrong_scope": device["energy"]["equipment_ids"] = ["Cutter02"]
    if fault == "alarm_date": device["alarm"]["start_at"] = "2026-02-30 00:00:00"
    if fault == "unknown_field": device["password"] = "must-not-appear-in-error"
    if fault == "version": value["schema_version"] = 99
    if fault == "shared_scope": value["devices"][1]["machine_no"] = device["machine_no"]
    if fault == "shared_meter": value["devices"][1]["energy"] = {**deepcopy(device["energy"]), "equipment_ids": ["Cutter02"]}
    with pytest.raises(ValueError, match="设备配置文件校验失败") as raised:
        load_device_catalog(save(tmp_path, value))
    assert "must-not-appear-in-error" not in str(raised.value)


def test_missing_or_broken_json_fails_instead_of_falling_back_to_cutter01(tmp_path):
    path = tmp_path / "devices.json"
    with pytest.raises(ValueError, match="无法读取设备配置文件"):
        load_device_catalog(path)
    path.write_text("{broken json", encoding="utf-8")
    with pytest.raises(ValueError, match="设备配置文件校验失败"):
        load_device_catalog(path)


def test_device_config_is_included_in_the_existing_source_package():
    from scripts.build_docker_package import collect_files
    files = collect_files(ROOT)
    assert files["backend/app/configuration/devices.json"] == PROFILE.read_bytes()
    assert "backend/app/device_config.py" in files


def test_every_device_resolves_catalog_fields_even_when_base_has_legacy_values(monkeypatch):
    module = settings_from(monkeypatch, PROFILE)
    base = module.Settings(report_machine_no="old", alarm_monitor_machine_nos=("old",),
        energy_gas_parameter_id="old", energy_equipment_ids=("old",),
        alarm_monitor_start_at="2000-01-01 00:00:00")
    for device in module.DEVICE_CATALOG.devices:
        cfg = module.settings_for_device(base, device.id)
        assert cfg.report_machine_no == device.machine_no
        assert cfg.production_line_id == device.production_line_id
        assert cfg.production_data_source == device.data_source
        assert cfg.alarm_monitor_machine_nos == (device.machine_no,)
        assert cfg.alarm_monitor_start_at == device.alarm.start_at
        assert cfg.energy_gas_parameter_id == (str(device.energy.gas_parameter_id) if device.energy else "")
        assert not cfg.energy_equipment_ids


def test_energy_diagnostic_rejects_device_without_meters_before_query(monkeypatch):
    from scripts import check_energy
    monkeypatch.setattr("sys.argv", ["check_energy.py", "--device-id", "Cutter02", "--date", "2026-10-24"])
    monkeypatch.setattr(check_energy, "EnergyApiClient", lambda _: pytest.fail("must not query another device's meters"))
    with pytest.raises(SystemExit) as exc:
        check_energy.main()
    assert exc.value.code == 2
