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


def test_recent_recheck_does_not_skip_incremental_backlog(tmp_path):
    repository = AlarmRepository(tmp_path / "metadata.db")
    repository.initialize()
    now = datetime(2026, 9, 30, 9, 0, tzinfo=TZ)
    rows = [alarm(i, start_time="2026-09-28 09:00:00") for i in range(1, 22)]
    rows.append(alarm(22, start_time="2026-09-30 08:00:00"))

    class Source:
        def latest_id(self):
            return 22

        def fetch_new(self, after, through, limit):
            return [r for r in rows if after < r["alarm_info_id"] <= through][:limit]

        def fetch_recent(self, after, through, start, end, limit):
            return [r for r in rows if after < r["alarm_info_id"] <= through
                    and start <= r["start_time"] <= end][:limit]

        def fetch_by_ids(self, ids):
            return [r for r in rows if r["alarm_info_id"] in ids]

    service = AlarmMonitorService(SimpleNamespace(alarm_retention_days=30, alarm_monitor_batch_size=1,
        alarm_monitor_start_at="2026-09-01 00:00:00", alarm_reopen_debounce_minutes=10),
        repository, Source(), SimpleNamespace(analyze=lambda _: {"facts": [], "overall_risk": "warning"}),
        SimpleNamespace(render=lambda **_: dict(html_relative_path="alarms/a.html",
            pdf_relative_path="alarms/a.pdf", pdf_filename="a.pdf", file_size=1, sha256="a")))
    service.run_once(now)
    assert repository.get_cursor() == 20  # 22 is supplemental; 21 is still unscanned.
    restarted = AlarmRepository(repository.database_path)
    restarted.initialize()
    service.repository = restarted
    service.run_once(now + timedelta(minutes=5))
    assert restarted.get_cursor() == 22
    with restarted.connect() as connection:
        ids = {r[0] for r in connection.execute("SELECT source_alarm_id FROM alarm_source_events")}
    assert ids == set(range(1, 23))
    assert len(restarted.list_reports()) == 1


def test_alarm_events_and_scan_boundary_rollback_and_retry_together(tmp_path, monkeypatch):
    repository = AlarmRepository(tmp_path / "metadata.db")
    repository.initialize()
    now = datetime(2026, 9, 30, 9, 0, tzinfo=TZ)
    arguments = dict(now=now, debounce_minutes=10)
    repository.ingest([], cursor=10, open_recheck_after_id=5,
                      history_recheck_after_id=6, **arguments)
    from app.database import alarms as alarm_module
    original_hash = alarm_module._source_hash

    def fail_second(record):
        if record["alarm_info_id"] == 50:
            raise RuntimeError("injected failure after first event")
        return original_hash(record)

    monkeypatch.setattr(alarm_module, "_source_hash", fail_second)
    with pytest.raises(RuntimeError, match="injected"):
        repository.ingest([alarm(11), alarm(50)], cursor=11,
                          open_recheck_after_id=11, history_recheck_after_id=50, **arguments)
    assert repository.get_cursor() == 10
    assert repository.get_sync_state()["open_recheck_after_id"] == 5
    assert repository.get_sync_state()["history_recheck_after_id"] == 6
    with repository.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM alarm_source_events").fetchone()[0] == 0
    monkeypatch.setattr(alarm_module, "_source_hash", original_hash)
    repository.ingest([alarm(11), alarm(50)], cursor=11, **arguments)
    repeated = repository.ingest([alarm(11), alarm(50)], cursor=10, **arguments)
    assert repeated["last_alarm_id"] == repository.get_cursor() == 11
    with repository.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM alarm_source_events").fetchone()[0] == 2


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


def test_alarm_analysis_keeps_rule_fields_deterministic_and_normalizes_limitations(
    monkeypatch,
) -> None:
    model_result = {
        "overall_risk": "warning",
        "summary": "切割头高度异常需要核查。[E001]",
        "findings": [
            {
                "title": "切割头高度异常",
                "severity": "warning",
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
        "limitations": {
            "data": ["仅依据数据库告警记录。"],
            "missing": "未包含现场检修结果。",
        },
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
        lambda *_args, **_kwargs: None,
    )

    result = AlarmAnalysisService(
        replace(
            settings,
            llm_enabled=True,
            llm_base_url="http://model.local/v1",
            llm_model="internal-model",
        )
    ).analyze([incident()])

    assert result["overall_risk"] == "high"
    assert result["findings"][0]["severity"] == "high"
    assert result["limitations"] == [
        "仅依据数据库告警记录。",
        "未包含现场检修结果。",
    ]
    invalid = {**result, "limitations": {"note": "无法确定的结构"}}
    with pytest.raises(ValueError, match="告警分析边界格式错误"):
        AlarmAnalysisService._validate(invalid, result["facts"])


def test_alarm_analysis_adds_rule_fields_when_model_omits_them(monkeypatch) -> None:
    model_result = {
        "summary": "切割头高度异常需要核查。[E001]",
        "findings": [
            {
                "title": "切割头高度异常",
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
        "limitations": None,
    }

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": json.dumps(model_result)}}]}

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
        "app.services.alarm_analysis_service.record_llm_call", lambda *_args, **_kwargs: None
    )

    result = AlarmAnalysisService(
        replace(
            settings,
            llm_enabled=True,
            llm_base_url="http://model.local/v1",
            llm_model="internal-model",
        )
    ).analyze([incident()])

    assert result["overall_risk"] == "high"
    assert result["findings"][0]["severity"] == "high"
    assert result["limitations"] == []


def test_alarm_analysis_repairs_summary_from_validated_item_evidence() -> None:
    facts = AlarmAnalysisService._facts([incident()])
    result = {
        "summary": "切割头高度异常需要核查。",
        "findings": [
            {
                "title": "切割头高度异常",
                "analysis": "设备报告高度故障。",
                "possible_causes": ["高度传感器或机械位置异常"],
                "confidence": 0.6,
                "evidence_ids": ["E001"],
            }
        ],
        "actions": [
            {
                "priority": 1,
                "action": "按现场规程检查切割头。",
                "evidence_ids": ["E001"],
            }
        ],
        "limitations": [],
    }

    AlarmAnalysisService._validate(result, facts)

    assert result["summary"] == "切割头高度异常需要核查。（依据 [E001]）"

    invalid = {**result, "summary": "切割头高度异常需要核查。[E999]"}
    with pytest.raises(ValueError, match="告警分析引用了不存在的证据"):
        AlarmAnalysisService._validate(invalid, facts)


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


def test_failed_report_remains_visible_after_incidents_are_released_for_retry(
    tmp_path,
) -> None:
    repository = AlarmRepository(tmp_path / "metadata.db")
    repository.initialize()
    cutoff = "2026-09-29 00:00:00"
    now = datetime(2026, 9, 29, 9, 0, tzinfo=TZ)
    repository.ingest(
        [alarm(1, start_time="2026-09-29 08:00:00")],
        now=now,
        cursor=1,
        debounce_minutes=10,
        monitor_start_at=cutoff,
    )
    current = repository.list_unreported_incidents(monitor_start_at=cutoff)[0]
    repository.reserve_report(
        proposed_report_id="failed-report",
        emission_key="failed-emission",
        machine_no="20kw切割机",
        incident_ids=[current["incident_id"]],
        now=now.isoformat(timespec="seconds"),
    )

    repository.fail_report("failed-report", "模型输出格式错误")

    reports = repository.list_reports(monitor_start_at=cutoff)
    assert repository.list_unreported_incidents(monitor_start_at=cutoff)
    assert len(reports) == 1
    assert reports[0]["status"] == "failed"
    assert reports[0]["error_message"] == "模型输出格式错误"


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

        def fetch_recent(self, *_args):
            return []

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
            alarm_retention_days=30,
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

        def fetch_recent(self, *_args):
            return []

        def fetch_by_ids(self, _ids):
            return []

    source = Source()
    service = AlarmMonitorService(
        SimpleNamespace(
            alarm_monitor_batch_size=500,
            alarm_retention_days=30,
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


def test_sql_source_rechecks_the_bounded_recent_window() -> None:
    calls = []

    class Client:
        def fetch_all(self, sql, parameters):
            calls.append((sql, parameters))
            return []

    configured = replace(settings, alarm_monitor_machine_nos=("Cutter01",))
    SqlServerAlarmSource(configured, client=Client()).fetch_recent(
        20,
        1000,
        "2026-09-29 09:00:00",
        "2026-09-30 09:00:00",
        500,
    )

    sql, parameters = calls[0]
    assert "ALARM_INFO_ID > ?" in sql
    assert "ALARM_INFO_ID <= ?" in sql
    assert sql.count("TRY_CONVERT") == 2
    assert parameters == (
        20,
        1000,
        "2026-09-29 09:00:00",
        "2026-09-30 09:00:00",
        "Cutter01",
    )


def test_recent_recheck_captures_late_rows_once_and_applies_delayed_updates(
    tmp_path,
) -> None:
    repository = AlarmRepository(tmp_path / "metadata.db")
    repository.initialize()
    now = datetime(2026, 9, 30, 9, 0, tzinfo=TZ)
    repository.configure_monitoring_window("2026-09-29 00:00:00")
    repository.ingest(
        [],
        now=now - timedelta(minutes=5),
        cursor=100,
        debounce_minutes=10,
        monitor_start_at="2026-09-29 00:00:00",
    )

    class Source:
        def __init__(self):
            self.row = alarm(50, start_time="2026-09-30 02:41:26")
            self.recent_calls = []

        def latest_id(self):
            return 100

        def fetch_new(self, after_id, _through_id, _limit):
            assert after_id == 100
            return []

        def fetch_recent(self, after_id, through_id, start_at, end_at, limit):
            self.recent_calls.append((after_id, through_id, start_at, end_at, limit))
            return [dict(self.row)] if after_id < 50 else []

        def fetch_by_ids(self, ids):
            return [dict(self.row)] if 50 in ids else []

    class Analyzer:
        def __init__(self):
            self.calls = 0

        def analyze(self, _incidents):
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
                "pdf_filename": "Cutter01_告警报告.pdf",
                "file_size": 100,
                "sha256": "abc",
            }

    source = Source()
    analyzer = Analyzer()
    reporter = Reporter()
    service = AlarmMonitorService(
        SimpleNamespace(
            alarm_monitor_batch_size=500,
            alarm_retention_days=30,
            alarm_monitor_start_at="2026-09-29 00:00:00",
            alarm_reopen_debounce_minutes=10,
        ),
        repository,
        source,
        analyzer,
        reporter,
    )

    first = service.run_once(now)
    source.row["remove_time"] = "2026-09-30 09:03:00"
    second = service.run_once(now + timedelta(minutes=5))
    third = service.run_once(now + timedelta(minutes=10))

    incidents = repository.list_incidents(None)
    reports = repository.list_reports()
    assert first["report_status"] == "completed"
    assert second["report_status"] == "idle"
    assert third["report_status"] == "idle"
    assert source.recent_calls[0] == (
        0,
        100,
        "2026-09-29 09:00:00",
        "2026-09-30 09:00:00",
        500,
    )
    assert len(incidents) == 1
    assert incidents[0]["first_start_time"] == "2026-09-30 02:41:26"
    assert incidents[0]["first_seen_at"] == now.isoformat(timespec="seconds")
    assert incidents[0]["last_seen_at"] == (now + timedelta(minutes=5)).isoformat(
        timespec="seconds"
    )
    assert incidents[0]["status"] == "closed"
    assert len(reports) == 1
    assert reports[0]["status"] == "completed"
    assert analyzer.calls == 1
    assert reporter.calls == 1


def test_report_failure_does_not_mark_successful_alarm_sync_as_failed(tmp_path) -> None:
    repository = AlarmRepository(tmp_path / "metadata.db")
    repository.initialize()
    now = datetime(2026, 9, 30, 9, 0, tzinfo=TZ)

    class Source:
        def __init__(self):
            self.new_rows = [alarm(1, start_time="2026-09-30 08:55:00")]

        def latest_id(self):
            return max([1, *(int(row["alarm_info_id"]) for row in self.new_rows)])

        def fetch_new(self, _after_id, _through_id, _limit):
            rows, self.new_rows = self.new_rows, []
            return rows

        def fetch_recent(self, *_args):
            return []

        def fetch_by_ids(self, _ids):
            return []

    source = Source()
    service = AlarmMonitorService(
        SimpleNamespace(
            alarm_monitor_batch_size=500,
            alarm_retention_days=30,
            alarm_monitor_start_at="2026-09-29 00:00:00",
            alarm_reopen_debounce_minutes=10,
        ),
        repository,
        source,
        SimpleNamespace(
            analyze=lambda _incidents: (_ for _ in ()).throw(
                RuntimeError("模型服务不可用")
            )
        ),
        SimpleNamespace(render=lambda **_kwargs: {}),
    )

    first = service.run_once(now)
    source.new_rows = [
        alarm(
            2,
            start_time="2026-09-30 09:04:00",
            alarm_code=3103,
            description="伺服器故障",
        )
    ]
    second = service.run_once(now + timedelta(minutes=5))

    assert first["status"] == "completed"
    assert first["sync_status"] == "completed"
    assert first["report_status"] == "failed"
    assert first["report_error"] == "模型服务不可用"
    assert second["sync_status"] == "completed"
    assert second["report_status"] == "failed"
    assert len(repository.list_incidents(None)) == 2
    assert repository.get_sync_state()["last_success_at"] == (
        now + timedelta(minutes=5)
    ).isoformat(
        timespec="seconds"
    )
    assert {report["status"] for report in repository.list_reports()} == {"failed"}


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


def test_incident_page_exposes_report_state_without_a_recent_report_limit(tmp_path, monkeypatch) -> None:
    from app.api import alarms as alarm_api

    repository = AlarmRepository(tmp_path / "metadata.db")
    repository.initialize()
    cutoff = "2026-09-29 00:00:00"
    now = datetime(2026, 10, 7, 9, 0, tzinfo=TZ)
    repository.ingest(
        [alarm(index, start_time="2026-10-07 08:00:00", alarm_code=index) for index in range(1, 7)],
        now=now, cursor=6, debounce_minutes=10,
    )
    incidents = {item["alarm_code"]: item for item in repository.list_incidents(None)}
    for code, report_id in [("1", "old-completed"), ("2", "generating"), ("3", "legacy-failed"), ("4", "no-file")]:
        repository.reserve_report(
            proposed_report_id=report_id, emission_key=report_id, machine_no="Cutter01",
            incident_ids=[incidents[code]["incident_id"]], now="2026-10-01T09:00:00+08:00",
        )
    repository.complete_report(
        "old-completed", analysis={"summary": "older report"},
        html_relative_path="alarms/old.html", pdf_relative_path="alarms/old.pdf",
        pdf_filename="old.pdf", file_size=100, sha256="sha", model_status="model",
        completed_at="2026-10-01T09:01:00+08:00",
    )
    with repository.connect() as connection:
        # Cover legacy/inconsistent associations without changing failure recovery.
        connection.execute("UPDATE alarm_reports SET status = 'failed', error_message = '模型服务不可用' WHERE report_id = 'legacy-failed'")
        connection.execute("UPDATE alarm_reports SET status = 'completed' WHERE report_id = 'no-file'")
        connection.execute("UPDATE alarm_incidents SET pdf_report_id = 'missing' WHERE incident_id = ?", (incidents["5"]["incident_id"],))
    for index in range(51):
        repository.reserve_report(
            proposed_report_id=f"new-{index}", emission_key=f"new-{index}", machine_no="Cutter01",
            incident_ids=[f"other-{index}"], now=f"2026-10-07T08:{index:02d}:00+08:00",
        )
    assert "old-completed" not in {item["report_id"] for item in repository.list_reports(50)}
    monkeypatch.setattr(alarm_api, "settings", SimpleNamespace(alarm_monitor_start_at=cutoff))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(alarm_repository=repository)), state=SimpleNamespace(request_id="test"))
    result = alarm_api.page_alarm_incidents(request, page=1, page_size=15)["data"]
    by_code = {item["alarm_code"]: item for item in result["items"]}
    assert result["total"] == result["all_total"] == result["open_count"] == 6
    assert by_code["1"]["report_status"] == "completed"
    assert by_code["1"]["report_view_url"] == "/api/v1/alarms/reports/old-completed/view"
    assert by_code["1"]["status"] == "open", "report completion cannot resolve the equipment alarm"
    assert by_code["2"]["report_status"] == "generating"
    assert by_code["3"]["report_status"] == "failed"
    assert by_code["3"]["report_error_message"] == "模型服务不可用"
    assert by_code["4"]["report_status"] == "completed"
    assert by_code["5"]["report_status"] is None
    assert by_code["5"]["pdf_report_id"] == "missing"
    assert by_code["6"]["pdf_report_id"] is None
    assert all(by_code[code]["report_view_url"] is None for code in ["2", "3", "4", "5", "6"])
    assert all("report_has_html" not in item for item in result["items"])


def test_alarm_report_page_returns_ten_items_and_full_counts(tmp_path) -> None:
    repository = AlarmRepository(tmp_path / "metadata.db")
    repository.initialize()
    for index in range(23):
        report_id = f"report-{index:02d}"
        created_at = f"2026-10-06T08:{index:02d}:00+08:00"
        repository.reserve_report(
            proposed_report_id=report_id,
            emission_key=f"emission-{index:02d}",
            machine_no="Cutter01",
            incident_ids=[f"incident-{index:02d}"],
            now=created_at,
        )
        if index % 2 == 0:
            repository.complete_report(
                report_id,
                analysis={"summary": f"report {index}"},
                html_relative_path=f"alarms/{report_id}.html",
                pdf_relative_path=f"alarms/{report_id}.pdf",
                pdf_filename=f"{report_id}.pdf",
                file_size=100,
                sha256=f"sha-{index}",
                model_status="completed",
                completed_at=created_at,
            )

    second_page = repository.page_reports(
        monitor_start_at=None,
        page=2,
        page_size=10,
    )
    last_page = repository.page_reports(
        monitor_start_at=None,
        page=99,
        page_size=10,
    )

    assert second_page["total"] == 23
    assert second_page["completed_count"] == 12
    assert second_page["page_count"] == 3
    assert second_page["page"] == 2
    assert len(second_page["items"]) == 10
    assert second_page["items"][0]["report_id"] == "report-12"
    assert second_page["items"][-1]["report_id"] == "report-03"
    assert last_page["page"] == 3
    assert len(last_page["items"]) == 3


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
