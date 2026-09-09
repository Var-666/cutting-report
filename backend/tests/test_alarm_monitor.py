from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.config import settings
from app.database.alarm_source import SqlServerAlarmSource
from app.database.alarms import AlarmRepository
from app.services.alarm_monitor_service import AlarmMonitorService


TZ = ZoneInfo("Asia/Shanghai")


def alarm(
    source_id: int, *, remove_time: str | None = None, description: str = "切割头高度异常"
) -> dict:
    return {
        "alarm_info_id": source_id,
        "machine_no": "20kw切割机",
        "start_time": "2026-09-09 09:00:00",
        "remove_time": remove_time,
        "alarm_desc": description,
        "alarm_code": 3102,
        "alarm_class": "故障",
        "alarm_type": "CUTTER",
    }


def test_same_open_problem_emits_once_and_real_recurrence_emits_again(tmp_path) -> None:
    repository = AlarmRepository(tmp_path / "metadata.db")
    repository.initialize()
    now = datetime(2026, 9, 9, 9, 0, tzinfo=TZ)

    first = repository.ingest(
        [alarm(1)], now=now, cursor=1, debounce_minutes=10
    )
    assert first["created_incidents"] == 1
    incident = repository.list_unreported_incidents()[0]
    repository.reserve_report(
        proposed_report_id="alarm_report_1",
        emission_key="first",
        machine_no="20kw切割机",
        incident_ids=[incident["incident_id"]],
        now=now.isoformat(),
    )

    # A second source row with the same identity belongs to the already reported
    # open incident, even if the alarm remains active for a long time.
    repository.ingest(
        [alarm(1), alarm(2, description="切割头高度异常：当前值 18.2")],
        now=now + timedelta(hours=3),
        cursor=2,
        debounce_minutes=10,
    )
    assert repository.list_unreported_incidents() == []
    assert len(repository.list_incidents(None)) == 1

    cleared = "2026-09-09 12:05:00"
    repository.ingest(
        [alarm(1, remove_time=cleared), alarm(2, remove_time=cleared)],
        now=now + timedelta(hours=3, minutes=5),
        cursor=2,
        debounce_minutes=10,
    )
    assert repository.list_incidents(None)[0]["status"] == "closed"

    # A recurrence after the debounce window is a genuinely new incident.
    recurrence_time = now + timedelta(hours=3, minutes=20)
    result = repository.ingest(
        [alarm(3)], now=recurrence_time, cursor=3, debounce_minutes=10
    )
    assert result["created_incidents"] == 1
    assert len(repository.list_unreported_incidents()) == 1
    assert len(repository.list_incidents(None)) == 2


def test_recurrence_inside_debounce_window_does_not_emit_again(tmp_path) -> None:
    repository = AlarmRepository(tmp_path / "metadata.db")
    repository.initialize()
    now = datetime(2026, 9, 9, 9, 0, tzinfo=TZ)
    repository.ingest([alarm(1)], now=now, cursor=1, debounce_minutes=10)
    incident = repository.list_unreported_incidents()[0]
    repository.reserve_report(
        proposed_report_id="alarm_report_1",
        emission_key="first",
        machine_no="20kw切割机",
        incident_ids=[incident["incident_id"]],
        now=now.isoformat(),
    )
    repository.ingest(
        [alarm(1, remove_time="2026-09-09 09:01:00")],
        now=now + timedelta(minutes=1),
        cursor=1,
        debounce_minutes=10,
    )
    result = repository.ingest(
        [alarm(2)], now=now + timedelta(minutes=5), cursor=2, debounce_minutes=10
    )
    assert result["created_incidents"] == 0
    assert repository.list_unreported_incidents() == []


def test_monitor_calls_model_and_reporter_only_for_unreported_problem(tmp_path) -> None:
    repository = AlarmRepository(tmp_path / "metadata.db")
    repository.initialize()

    class Source:
        def __init__(self):
            self.open_rows = [alarm(1)]

        def latest_id(self):
            return 1

        def fetch_open(self, _limit):
            rows, self.open_rows = self.open_rows, []
            return rows

        def fetch_new(self, _after_id, _limit):
            return []

        def fetch_by_ids(self, ids):
            return [alarm(value) for value in ids]

    class Analyzer:
        def __init__(self):
            self.calls = 0

        def analyze(self, incidents):
            self.calls += 1
            return {"facts": [], "overall_risk": "warning"}, "model"

    class Reporter:
        def __init__(self):
            self.calls = 0

        def render(self, **_kwargs):
            self.calls += 1
            return {
                "html_relative_path": "alarms/report.html",
                "pdf_relative_path": "alarms/report.pdf",
                "pdf_filename": "20kw切割机_智能告警报告.pdf",
                "file_size": 100,
                "sha256": "abc",
            }

    analyzer = Analyzer()
    reporter = Reporter()
    service = AlarmMonitorService(
        SimpleNamespace(alarm_monitor_batch_size=500, alarm_reopen_debounce_minutes=10),
        repository,
        Source(),
        analyzer,
        reporter,
    )
    now = datetime(2026, 9, 9, 9, 0, tzinfo=TZ)
    first = service.run_once(now)
    second = service.run_once(now + timedelta(minutes=5))

    assert first["report_id"]
    assert second["report_id"] is None
    assert analyzer.calls == 1
    assert reporter.calls == 1


def test_first_start_skips_historical_closed_alarms(tmp_path) -> None:
    repository = AlarmRepository(tmp_path / "metadata.db")
    repository.initialize()

    class Source:
        fetch_new_calls = 0

        def latest_id(self):
            return 900

        def fetch_open(self, _limit):
            return []

        def fetch_new(self, _after_id, _limit):
            self.fetch_new_calls += 1
            return [alarm(899, remove_time="2026-09-08 18:00:00")]

        def fetch_by_ids(self, _ids):
            return []

    source = Source()
    service = AlarmMonitorService(
        SimpleNamespace(alarm_monitor_batch_size=500, alarm_reopen_debounce_minutes=10),
        repository,
        source,
        SimpleNamespace(analyze=lambda _items: (_ for _ in ()).throw(AssertionError())),
        SimpleNamespace(render=lambda **_kwargs: (_ for _ in ()).throw(AssertionError())),
    )

    result = service.run_once(datetime(2026, 9, 9, 9, 0, tzinfo=TZ))

    assert result["report_id"] is None
    assert source.fetch_new_calls == 0
    assert repository.get_cursor() == 900


def test_sql_source_filters_the_actual_cutter_name() -> None:
    calls = []

    class Client:
        def fetch_all(self, sql, parameters):
            calls.append((sql, parameters))
            return []

    configured = replace(settings, alarm_monitor_machine_nos=("20kw切割机",))
    SqlServerAlarmSource(configured, client=Client()).fetch_new(12, 500)

    sql, parameters = calls[0]
    assert "MACHINE_NO" in sql
    assert "ALARM_TYPE" in sql
    assert parameters == (12, "20kw切割机")
