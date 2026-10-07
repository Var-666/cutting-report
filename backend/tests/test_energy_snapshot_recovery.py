from datetime import date, datetime, timedelta

from test_energy_period_service import DAY, START, samples, service


def test_saved_partial_day_is_completed_after_snapshot_ttl(tmp_path):
    now = [datetime(2026, 8, 20, 12)]
    incomplete = [row for row in samples() if row["Mt"] < "2026-08-18T12:00:00"]
    energy, client = service(tmp_path, rows=incomplete, now=now[0])
    energy.clock = lambda: now[0]
    partial = energy.get_day(DAY)
    assert partial["summary"]["gas"]["status"] == "partial"
    client.rows = samples()
    now[0] += timedelta(seconds=energy.settings.energy_report_snapshot_ttl_seconds + 1)
    assert energy.sync_month(DAY) == 1
    completed = energy.repository.get_day(energy.day_source_key(DAY), DAY.isoformat())
    assert completed["summary"]["gas"]["value"] == 96
    assert completed["summary"]["gas"]["complete_count"] == 24


def test_month_resync_preserves_known_hours_when_platform_regresses(tmp_path):
    energy, client = service(tmp_path, now=datetime(2026, 8, 20, 12))
    original = energy.get_day(DAY)
    client.rows = [row for row in samples() if row["Mt"] < "2026-08-18T12:00:00"]
    assert energy.sync_month(DAY, force=True) == 0
    saved = energy.repository.get_day(energy.day_source_key(DAY), DAY.isoformat())
    assert saved["updated_at"] == original["updated_at"]
    assert saved["summary"]["gas"]["value"] == 96


def test_previous_today_snapshot_is_finalized_after_midnight_within_ttl(tmp_path):
    now = [START + timedelta(hours=23, minutes=59, seconds=50)]
    energy, client = service(tmp_path, now=now[0])
    energy.clock = lambda: now[0]
    provisional = energy.get_day(DAY)
    assert provisional["is_today"] is True
    assert provisional["summary"]["gas"]["complete_count"] == 23
    now[0] = START + timedelta(days=1, seconds=10)
    finalized = energy.get_day(DAY)
    assert not finalized["from_cache"]
    assert finalized["is_today"] is False
    assert finalized["summary"]["gas"]["complete_count"] == 24
    assert len(client.calls) == 2


def test_historical_complete_snapshot_within_ttl_does_not_refetch(tmp_path):
    energy, client = service(tmp_path, now=datetime(2026, 8, 20, 12))
    energy.get_day(DAY)
    energy.sync_month(date(2026, 8, 1))
    # The monthly request still checks missing dates, but never rewrites the fresh day.
    saved = energy.repository.get_day(energy.day_source_key(DAY), DAY.isoformat())
    assert saved["summary"]["gas"]["value"] == 96
    assert energy._snapshot_due(DAY, saved, energy.now()) is False
