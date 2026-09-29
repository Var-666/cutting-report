from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.config import settings
from app.database.alarm_source import SqlServerAlarmSource
from app.database.alarms import AlarmRepository
from app.services.alarm_analysis_service import AlarmAnalysisService
from app.services.alarm_monitor_service import AlarmMonitorService


TZ = ZoneInfo("Asia/Shanghai")


def alarm(
    source_id: int,
    *,
    remove_time: str | None = None,
    description: str = "切割头高度异常",
    start_time: str = "2026-09-09 09:00:00",
    alarm_code: int = 3102,
) -> dict:
    return {
        "alarm_info_id": source_id,
        "machine_no": "20kw切割机",
        "start_time": start_time,
        "remove_time": remove_time,
        "alarm_desc": description,
        "alarm_code": alarm_code,
        "alarm_class": "故障",
        "alarm_type": "CUTTER",
    }


def incident() -> dict:
    return {
        "incident_id": "incident-1",
        "machine_no": "20kw切割机",
        "alarm_type": "CUTTER",
        "alarm_code": "3102",
        "alarm_class": "故障",
        "alarm_desc": "切割头高度异常",
        "severity": "high",
        "status": "open",
        "first_start_time": "2026-09-09 09:00:00",
        "first_seen_at": "2026-09-09 09:00:00",
        "latest_remove_time": None,
    }


def test_alarm_analysis_requires_a_model_result(monkeypatch) -> None:
    with pytest.raises(RuntimeError, match="未启用"):
        AlarmAnalysisService(replace(settings, llm_enabled=False)).analyze([incident()])

    class FailingClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            raise RuntimeError("model unavailable")

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr("app.services.alarm_analysis_service.httpx.Client", FailingClient)
    with pytest.raises(RuntimeError, match="model unavailable"):
        AlarmAnalysisService(replace(settings, llm_enabled=True)).analyze([incident()])


def test_alarm_analysis_normalizes_one_model_limitation_into_a_list(monkeypatch) -> None:
    model_result = {
        "overall_risk": "high",
        "summary": "切割头高度异常需要核查。[E001]",
        "findings": [
            {
                "title": "切割头高度异常",
                "severity": "high",
                "analysis": "设备报告高度故障。[E001]",
                "possible_causes": ["高度传感器或机械位置异常"],
                "confidence": 0.6,
                "evidence_ids": ["E001"],
            }
        ],
        "actions": [
            {
                "priority": 1,
                "action": "按现场规程检查切割头。[E001]",
                "evidence_ids": ["E001"],
            }
        ],
        "limitations": "仅依据数据库告警记录，未包含现场检修结果。",
    }

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {"message": {"content": json.dumps(model_result)}}
                ]
            }

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr("app.services.alarm_analysis_service.httpx.Client", Client)
    monkeypatch.setattr(
        "app.services.alarm_analysis_service.record_llm_call",
        lambda *_args: None,
    )

    result = AlarmAnalysisService(
        replace(
            settings,
            llm_enabled=True,
            llm_base_url="http://model.local/v1",
            llm_model="internal-model",
        )
    ).analyze([incident()])

    assert result["limitations"] == ["仅依据数据库告警记录，未包含现场检修结果。"]
    invalid = {**result, "limitations": {"note": "无法确定的结构"}}
    with pytest.raises(ValueError, match="告警分析边界格式错误"):
        AlarmAnalysisService._validate(invalid, result["facts"])


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
            self.new_rows = []

        def latest_id(self):
            return max([1, *(int(row["alarm_info_id"]) for row in self.new_rows)])

        def fetch_new(self, _after_id, _through_id, _limit):
            rows, self.new_rows = self.new_rows, []
            return rows

        def fetch_by_ids(self, ids):
            return [alarm(value) for value in ids]

    class Analyzer:
        def __init__(self):
            self.calls = 0

        def analyze(self, incidents):
            self.calls += 1
            return {"facts": [], "overall_risk": "warning"}

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
    source = Source()
    service = AlarmMonitorService(
        SimpleNamespace(
            alarm_monitor_batch_size=500,
            alarm_monitor_start_at="2026-09-09 00:00:00",
            alarm_reopen_debounce_minutes=10,
        ),
        repository,
        source,
        analyzer,
        reporter,
    )
    now = datetime(2026, 9, 9, 9, 0, tzinfo=TZ)
    bootstrap = service.run_once(now)
    source.new_rows = [alarm(2)]
    first = service.run_once(now + timedelta(minutes=5))
    second = service.run_once(now + timedelta(minutes=10))

    assert bootstrap["report_id"] is None
    assert first["report_id"]
    assert second["report_id"] is None
    assert analyzer.calls == 1
    assert reporter.calls == 1


def test_first_start_backfills_only_the_configured_monitoring_window(tmp_path) -> None:
    repository = AlarmRepository(tmp_path / "metadata.db")
    repository.initialize()

    class Source:
        fetch_new_calls = 0

        def latest_id(self):
            return 900

        def fetch_new(self, _after_id, _through_id, _limit):
            self.fetch_new_calls += 1
            return [alarm(899, start_time="2026-09-29 08:00:00")]

        def fetch_by_ids(self, _ids):
            return []

    source = Source()
    service = AlarmMonitorService(
        SimpleNamespace(
            alarm_monitor_batch_size=500,
            alarm_monitor_start_at="2026-09-29 00:00:00",
            alarm_reopen_debounce_minutes=10,
        ),
        repository,
        source,
        SimpleNamespace(analyze=lambda _items: {"overall_risk": "high"}),
        SimpleNamespace(
            render=lambda **_kwargs: {
                "html_relative_path": "alarms/report.html",
                "pdf_relative_path": "alarms/report.pdf",
                "pdf_filename": "Cutter01_告警报告.pdf",
                "file_size": 100,
                "sha256": "abc",
            }
        ),
    )

    result = service.run_once(datetime(2026, 9, 29, 9, 0, tzinfo=TZ))

    assert result["report_id"]
    assert source.fetch_new_calls == 1
    assert repository.get_cursor() == 900
    assert repository.get_sync_state()["monitor_start_at"] == "2026-09-29 00:00:00"
    assert len(repository.list_incidents(None)) == 1


def test_sql_source_filters_the_actual_cutter_name() -> None:
    calls = []

    class Client:
        def fetch_all(self, sql, parameters):
            calls.append((sql, parameters))
            return []

    configured = replace(settings, alarm_monitor_machine_nos=("Cutter01",))
    SqlServerAlarmSource(configured, client=Client()).fetch_new(12, 1000, 500)

    sql, parameters = calls[0]
    assert "MACHINE_NO" in sql
    assert "ALARM_TYPE" in sql
    assert "TRY_CONVERT" in sql
    assert parameters == (12, 1000, "2026-09-29 00:00:00", "Cutter01")


def test_incident_page_excludes_alarms_before_start_and_keeps_full_counts(tmp_path) -> None:
    repository = AlarmRepository(tmp_path / "metadata.db")
    repository.initialize()
    cutoff = "2026-09-29 00:00:00"
    now = datetime(2026, 9, 29, 10, 0, tzinfo=TZ)
    repository.ingest(
        [alarm(1, start_time="2026-09-28 23:59:59", alarm_code=1)],
        now=now,
        cursor=1,
        debounce_minutes=10,
    )
    repository.ingest(
        [
            alarm(
                source_id,
                start_time=f"2026-09-29 09:{source_id:02d}:00",
                alarm_code=source_id,
                description="急停" if source_id == 2 else "切割头高度异常",
            )
            for source_id in range(2, 19)
        ],
        now=now,
        cursor=18,
        debounce_minutes=10,
        monitor_start_at=cutoff,
    )

    first_page = repository.page_incidents(
        monitor_start_at=cutoff,
        status=None,
        severity=None,
        page=1,
        page_size=5,
    )
    critical_page = repository.page_incidents(
        monitor_start_at=cutoff,
        status=None,
        severity="critical",
        page=1,
        page_size=5,
    )

    assert len(first_page["items"]) == 5
    assert first_page["total"] == 17
    assert first_page["all_total"] == 17
    assert first_page["page_count"] == 4
    assert first_page["open_count"] == 17
    assert first_page["severity_counts"] == {
        "critical": 1,
        "high": 16,
        "warning": 0,
    }
    assert critical_page["total"] == 1
    assert critical_page["all_total"] == 17


def test_window_resync_detaches_eligible_source_row_from_older_incident(tmp_path) -> None:
    repository = AlarmRepository(tmp_path / "metadata.db")
    repository.initialize()
    cutoff = "2026-09-29 00:00:00"
    now = datetime(2026, 9, 29, 10, 0, tzinfo=TZ)
    older = alarm(1, start_time="2026-09-28 23:55:00")
    eligible = alarm(2, start_time="2026-09-29 00:05:00")
    repository.ingest(
        [older, eligible],
        now=now,
        cursor=2,
        debounce_minutes=10,
    )
    assert len(repository.list_incidents(None)) == 1

    repository.ingest(
        [eligible],
        now=now,
        cursor=2,
        debounce_minutes=10,
        monitor_start_at=cutoff,
    )
    page = repository.page_incidents(
        monitor_start_at=cutoff,
        status=None,
        severity=None,
        page=1,
        page_size=15,
    )

    assert page["total"] == 1
    assert page["items"][0]["first_start_time"] == "2026-09-29 00:05:00"
