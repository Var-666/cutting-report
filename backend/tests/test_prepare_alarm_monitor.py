"""The reset command must preserve the configured Cutter01 monitoring window."""
from dataclasses import replace

from app.config import settings
from app.database.alarms import AlarmRepository
from scripts import prepare_alarm_monitor


def test_monitoring_window_preview_and_apply_reset_cursor(monkeypatch, tmp_path) -> None:
    configured = replace(settings, project_root=tmp_path, alarm_monitor_machine_nos=("Cutter01",))
    repository = AlarmRepository(configured.metadata_db_path)
    repository.initialize()
    monkeypatch.setattr(prepare_alarm_monitor, "settings", configured)

    class Source:
        def __init__(self, _settings):
            pass

        def latest_id(self):
            return 43224

    monkeypatch.setattr(prepare_alarm_monitor, "SqlServerAlarmSource", Source)
    monkeypatch.setattr("sys.argv", ["prepare_alarm_monitor.py"])
    assert prepare_alarm_monitor.main() == 0
    assert repository.get_sync_state()["last_success_at"] is None

    monkeypatch.setattr("sys.argv", ["prepare_alarm_monitor.py", "--apply"])
    assert prepare_alarm_monitor.main() == 0
    state = repository.get_sync_state()
    assert state["last_alarm_id"] == 0
    assert state["last_success_at"] is None
    assert state["monitor_start_at"] == "2026-09-29 00:00:00"
    assert repository.list_incidents(None) == []


def test_monitoring_window_reset_requires_cutter01_scope(monkeypatch, tmp_path) -> None:
    configured = replace(settings, project_root=tmp_path, alarm_monitor_machine_nos=("20kw切割机",))
    monkeypatch.setattr(prepare_alarm_monitor, "settings", configured)
    monkeypatch.setattr("sys.argv", ["prepare_alarm_monitor.py", "--apply"])
    try:
        prepare_alarm_monitor.main()
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("错误的设备配置不得推进告警游标")
    assert not configured.metadata_db_path.exists()
