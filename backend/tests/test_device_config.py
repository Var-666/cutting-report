import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from app.config import Settings, _environment, settings_for_line
from app.device_config import load_line_catalog
from tests.line_fixtures import configuration


@pytest.mark.parametrize('key,field,default,override', [
    ('REPORT_BACKGROUND_REFRESH_SECONDS', 'report_background_refresh_seconds', 300, 450),
    ('LLM_MAX_CONCURRENT', 'llm_max_concurrent', 10, 4),
    ('LLM_TIMEOUT_SECONDS', 'llm_timeout', 180, 90),
])
def test_global_defaults_and_explicit_environment_override(key, field, default, override):
    token = _environment.set({})
    try:
        assert getattr(Settings(), field) == default
        _environment.set({key: str(override)})
        assert getattr(Settings(), field) == override
    finally:
        _environment.reset(token)


def test_catalog_preserves_confirmed_meters_machine_and_alarm_start(tmp_path):
    cfg = configuration(tmp_path)
    catalog = load_line_catalog(cfg.production_lines_json)
    one, two = (catalog.get(key) for key in ('Cutter01', 'Cutter02'))
    assert one.device_id == one.id == one.machine_no == 'Cutter01'
    assert str(one.energy.gas_parameter_id) == 'bd3179be-c587-f011-a2e9-000c2974f33c'
    assert one.alarm.start_at == '2026-09-29 00:00:00'
    assert two.alarm.start_at == '2026-10-12 00:00:00' and two.energy is None
    derived = settings_for_line(replace(cfg, report_machine_no='wrong', energy_connected=True, energy_api_enabled=True), two)
    assert derived.device_id == 'Cutter02' and derived.report_machine_no == 'Cutter02'
    assert not derived.energy_api_enabled and not derived.energy_connected
    assert derived.production_data_source == two.source_key


@pytest.mark.parametrize('fault', ['duplicate', 'type', 'source', 'overlap', 'meter', 'alarm', 'default', 'equipment'])
def test_invalid_catalog_fails_explicitly(tmp_path, fault):
    value = json.loads(configuration(tmp_path).production_lines_json)
    one, two = value['lines'][:2]
    if fault == 'duplicate':
        two['id'] = one['id'].swapcase()
    elif fault == 'type':
        one['type'] = 'unknown'
    elif fault == 'source':
        one['source_key'] = ''
    elif fault == 'overlap':
        two['machine_no'] = one['machine_no']
    elif fault == 'meter':
        one['energy']['electricity_parameter_ids'].append(one['energy']['gas_parameter_id'])
    elif fault == 'alarm':
        one['alarm']['start_at'] = 'not-a-time'
    elif fault == 'default':
        value['default_line_id'] = 'missing'
    else:
        one['device_id'] = None
    with pytest.raises(ValueError, match='产线配置无效'):
        load_line_catalog(json.dumps(value))


def test_missing_or_broken_configuration_never_uses_legacy_default():
    for value in ('', '{}', 'invalid'):
        with pytest.raises(ValueError):
            load_line_catalog(value)
    assert Settings().device_id == ''
