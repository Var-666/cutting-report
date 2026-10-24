import sqlite3
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.database.alarms import AlarmRepository
from app.database.retention import RetentionRepository
from app.services.alarm_monitor_service import AlarmMonitorService
from test_alarm_monitor import alarm


NOW = datetime(2026, 9, 30, 9, tzinfo=ZoneInfo("Asia/Shanghai"))
START = "2026-09-01 00:00:00"


def monitor(repository, source, batch_size=100, retention_days=30):
    return AlarmMonitorService(SimpleNamespace(alarm_retention_days=retention_days, alarm_monitor_batch_size=batch_size,
        alarm_monitor_start_at=START, alarm_reopen_debounce_minutes=10), repository, source,
        SimpleNamespace(analyze=lambda _: {"facts": [], "overall_risk": "warning"}),
        SimpleNamespace(render=lambda **_: dict(html_relative_path="alarms/a.html",
            pdf_relative_path="alarms/a.pdf", pdf_filename="a.pdf", file_size=1, sha256="a")))


def test_expired_open_alarm_updates_then_stays_deleted_after_history_recheck(tmp_path):
    repository = AlarmRepository(tmp_path / "metadata.db", source_name="production")
    repository.initialize()
    retention = RetentionRepository(repository.database)
    retention.initialize()
    now = NOW + timedelta(days=40)
    row = alarm(1, start_time="2026-09-01 09:00:00")

    class Source:
        def latest_id(self): return 1
        def fetch_new(self, after, *_args): return [row] if after < 1 else []
        def fetch_recent(self, *_args): return [row]
        def fetch_by_ids(self, ids): return [row] if 1 in ids else []

    service = monitor(repository, Source())
    assert service.run_once(now)["report_status"] == "completed"
    assert repository.list_incidents("open")
    row["remove_time"] = now.strftime("%Y-%m-%d %H:%M:%S")
    assert service.run_once(now)["report_status"] == "idle"
    assert repository.list_incidents("closed"), "removal updates must reach old open alarms"
    result = retention.cleanup_expired_records(report_cutoff=None, analysis_cutoff=None,
        alarm_cutoff=(now - timedelta(days=30)).isoformat(), dispatch_cutoff=None,
        audit_cutoff=None, queued_at=now.isoformat(), batch_size=100)
    assert result["alarm_incidents"] == result["alarm_reports"] == 1
    assert service.run_once(now)["report_status"] == "idle"
    assert repository.list_incidents(None) == []
    assert repository.list_reports() == []
    assert repository.get_cursor() == 1, "discarded history still advances the scan boundary"


def test_disabled_retention_still_imports_old_resolved_history(tmp_path):
    repository = AlarmRepository(tmp_path / "metadata.db", source_name="production")
    repository.initialize()
    row = alarm(1, remove_time="2026-09-01 10:00:00", start_time="2026-09-01 09:00:00")

    class Source:
        def latest_id(self): return 1
        def fetch_new(self, after, *_args): return [row] if after < 1 else []
        def fetch_recent(self, *_args): return [row]
        def fetch_by_ids(self, _ids): return []

    assert monitor(repository, Source(), retention_days=0).run_once(NOW + timedelta(days=40))["report_status"] == "completed"
    assert len(repository.list_incidents("closed")) == 1


def test_open_recheck_reaches_page_after_2000_even_after_restart(tmp_path):
    repository = AlarmRepository(tmp_path / "metadata.db", source_name="production")
    repository.initialize()
    repository.configure_monitoring_window(START)
    repository.ingest([alarm(i) for i in range(1, 2002)], now=NOW, cursor=2001, debounce_minutes=10)

    class Source:
        def latest_id(self):
            return 2001

        def fetch_new(self, *args):
            return []

        def fetch_recent(self, *args):
            return []

        def fetch_by_ids(self, ids):
            return [alarm(2001, remove_time="2026-09-30 08:00:00")] if 2001 in ids else []

    monitor(repository, Source()).run_once(NOW)
    assert repository.get_sync_state()["open_recheck_after_id"] == 2000
    restarted = AlarmRepository(repository.database_path, source_name="production")
    restarted.initialize()
    monitor(restarted, Source()).run_once(NOW)
    with restarted.connect() as connection:
        event = connection.execute("SELECT is_open FROM alarm_source_events WHERE source_alarm_id = 2001").fetchone()
    assert event[0] == 0, "old unresolved events must not starve later removal updates"
    assert restarted.get_sync_state()["open_recheck_after_id"] == 0


def test_late_old_alarm_is_found_without_advancing_incremental_cursor(tmp_path):
    repository = AlarmRepository(tmp_path / "metadata.db", source_name="production")
    repository.initialize()
    repository.configure_monitoring_window(START)
    rows = [alarm(i, remove_time="2026-09-10 10:00:00") for i in (1, 2, 100)]
    repository.ingest(rows, now=NOW, cursor=100, debounce_minutes=10)

    class Source:
        def latest_id(self):
            return 100

        def fetch_new(self, *args):
            return []

        def fetch_by_ids(self, *args):
            return []

        def fetch_recent(self, after, through, start, end, limit):
            return [r for r in rows if after < r["alarm_info_id"] <= through
                    and start <= r["start_time"] <= end][:limit]

    source = Source()
    monitor(repository, source, 1).run_once(NOW)
    assert repository.get_sync_state()["history_recheck_after_id"] == 1
    rows.insert(2, alarm(3, remove_time="2026-09-10 10:00:00"))
    restarted = AlarmRepository(repository.database_path, source_name="production")
    restarted.initialize()
    service = monitor(restarted, source, 1)
    service.run_once(NOW)
    service.run_once(NOW)
    assert restarted.get_cursor() == 100
    with restarted.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM alarm_source_events WHERE source_alarm_id = 3").fetchone()[0] == 1
    assert restarted.get_sync_state()["history_recheck_after_id"] == 3


def test_existing_sync_table_migrates_and_window_reset_resets_all_scan_positions(tmp_path):
    path = tmp_path / "metadata.db"
    with sqlite3.connect(path) as connection:
        connection.execute("""CREATE TABLE alarm_sync_state (
            source_name TEXT PRIMARY KEY, last_alarm_id INTEGER NOT NULL DEFAULT 0,
            last_success_at TEXT, lease_owner TEXT, lease_until TEXT)""")
        connection.execute("INSERT INTO alarm_sync_state(source_name,last_alarm_id) VALUES ('production',42)")
    repository = AlarmRepository(path, source_name="production")
    repository.initialize()
    repository.initialize()
    state = repository.get_sync_state()
    assert state["last_alarm_id"] == 42
    assert state["open_recheck_after_id"] == state["history_recheck_after_id"] == 0
    repository.configure_monitoring_window(START)
    repository.ingest([], now=NOW, cursor=42, debounce_minutes=10,
                      open_recheck_after_id=30, history_recheck_after_id=20)
    assert repository.configure_monitoring_window("2026-09-29 00:00:00")
    state = repository.get_sync_state()
    assert state["last_alarm_id"] == state["open_recheck_after_id"] == state["history_recheck_after_id"] == 0
