from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest

from app.services.report_cache_policy import report_ttl, report_refresh_seconds
from tests.line_fixtures import configuration, line_settings


@pytest.mark.parametrize('line_id', ['Cutter01', 'Cutter02', 'welding'])
def test_all_lines_share_current_history_and_refresh_policy(tmp_path, line_id):
    cfg = line_settings(configuration(tmp_path, welding=True), line_id)
    cfg = replace(cfg, leadership_report_cache_seconds=73, leadership_report_history_cache_seconds=119,
                  report_background_refresh_seconds=17)
    today = date(2026, 11, 1)
    assert report_ttl(cfg, '2026-10-31', today=today) == 119
    assert report_ttl(cfg, '2026-11-01', today=today) == 73
    assert report_ttl(cfg, '2027-01-01', today=today) == 73
    assert report_ttl(cfg, today=today) == 73
    assert report_refresh_seconds(cfg) == 30
    cfg = replace(cfg, leadership_report_cache_seconds=0, leadership_report_history_cache_seconds=0)
    assert report_ttl(cfg, today=today) == 300
    assert report_ttl(cfg, '2026-10-31', today=today) == 86400


def test_energy_uses_global_period_ttl_and_retains_cross_day_revalidation(tmp_path):
    from app.services.energy_period_service import EnergyPeriodService
    cfg = replace(line_settings(configuration(tmp_path)), leadership_report_cache_seconds=73,
        leadership_report_history_cache_seconds=119)
    energy = EnergyPeriodService(cfg, None)
    now = datetime(2026, 11, 19, 12)
    cached = {'updated_at': (now - timedelta(seconds=60)).isoformat(), 'is_today': False}
    assert not energy._snapshot_due(date(2026, 11, 19), cached, now)
    assert not energy._snapshot_due(date(2026, 10, 31), cached, now)
    cached['updated_at'] = (now - timedelta(seconds=80)).isoformat()
    assert energy._snapshot_due(date(2026, 11, 19), cached, now)
    assert not energy._snapshot_due(date(2026, 10, 31), cached, now)
    cached['is_today'] = True
    assert energy._snapshot_due(date(2026, 11, 18), cached, now)


def test_energy_source_cache_can_be_disabled_by_the_shared_query_setting(tmp_path, monkeypatch):
    from app.services.energy_api import EnergyApiClient
    cfg = replace(line_settings(configuration(tmp_path)), report_data_cache_seconds=0)
    client = EnergyApiClient(cfg)
    calls = []
    monkeypatch.setattr(client, '_query', lambda *args: calls.append(args) or [{'value': 7}])
    start = datetime(2026, 11, 19)
    assert client.fetch_observed(('meter',), start, start + timedelta(hours=1))[1]
    assert client.fetch_observed(('meter',), start, start + timedelta(hours=1))[1]
    assert len(calls) == 2
