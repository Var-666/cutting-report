from datetime import date, datetime, timedelta
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from decimal import Decimal
import json
import re
import sqlite3
import time
from pathlib import Path
from threading import Event

import pytest
import httpx

from app.config import Settings
from app.database.metadata import MetadataRepository
from app.models.domain import AnalysisResult, ReportFilter
from app.services.equipment_service import mock_energy, sort_equipment_records
from app.services.leadership_report_service import LeadershipReportService
from app.services.llm_service import (
    AnalysisCapacityError, AnalysisInputTooLarge, LlmRequestTimeout, LlmService,
    _LlmCapacity, background_llm_work, llm_capacity,
)
from app.services.query_service import QueryService
from app.services.report_analysis_service import (
    AnalysisDataVersionMismatch,
    AnalysisUnavailableError,
    ReportAnalysisService,
    _present_analysis,
    analysis_table_source_version,
    combined_analysis_source_version,
    generate_data_version,
    leadership_analysis_source_version,
    workshop_analysis_source_version,
)
from tests.analysis_fakes import model_analysis
from app.services.report_deadline import ReportOperationTimeout, report_deadline
from app.services.request_cancellation import (
    SqlRequestCancelled, bind_request_id, reset_request_id, sql_request_cancellations,
)


def test_analysis_presents_internal_labels_in_plain_language() -> None:
    result = AnalysisResult(
        status="success",
        overview="未标注工程有 887 项任务，环比下降 14.9% [F001][F002]。",
        features="高位生产日有 2 天，未标注设备需核对 [F003]。",
        alerts="工时异常和数据边界需核实 [F003]。",
        suggestions="建议核对未标注工程的原始记录 [F001]。",
        facts=[
            {"id": "F001", "label": "未标注工程", "value": "887 项", "source": "未标注工程共 887 项"},
            {"id": "F002", "label": "任务数环比", "value": "下降 14.9%", "source": "任务数环比下降 14.9%"},
            {"id": "F003", "label": "高位生产日", "value": "2 天", "source": "高位生产日 2 天"},
        ],
    )

    presented = _present_analysis(result)
    visible_text = " ".join(
        [presented.overview, presented.features, presented.alerts, presented.suggestions]
        + [str(value) for fact in presented.facts for value in fact.values()]
    )
    for term in ("未标注工程", "未标注设备", "高位生产日", "工时异常", "数据边界", "环比"):
        assert term not in visible_text
    assert "工程号未填写" in presented.overview
    assert "887 项" in presented.overview
    assert "下降 14.9%" in presented.overview
    assert "[F001][F002]" in presented.overview


def test_missing_project_identifiers_are_described_plainly_in_report_analysis(tmp_path: Path) -> None:
    settings, service = _services(tmp_path)
    selected_date = date(2026, 8, 18)
    bundle = QueryService(settings).fetch_report_data(ReportFilter(report_date=selected_date))
    workshop_row = replace(bundle.workshop_records[0], project_code=None, ship_no=None)
    equipment_row = replace(bundle.equipment_records[0], project_code=None, ship_no=None)

    workshop_result = service.analyze_workshop(selected_date.isoformat(), [workshop_row])
    equipment_result = service.analyze_equipment(selected_date.isoformat(), [equipment_row])
    for result in (workshop_result, equipment_result):
        visible_text = " ".join(
            [result.overview, result.features, result.alerts, result.suggestions]
            + [str(value) for fact in result.facts for value in fact.values()]
        )
        assert "未标注工程" not in visible_text
    assert "工程号未填写" in workshop_result.facts[-1]["value"]


def _services(tmp_path: Path):
    settings = Settings(
        project_root=tmp_path,
        demo_mode=True,
        data_source_mode="demo",
        llm_enabled=True,
    )
    settings.ensure_runtime_dirs()
    metadata = MetadataRepository(settings.metadata_db_path)
    metadata.initialize()
    service = ReportAnalysisService(settings, metadata)
    service.llm.analyze_context = model_analysis
    return settings, service


def test_disabled_model_does_not_generate_report_data_analysis(tmp_path: Path) -> None:
    settings = Settings(
        project_root=tmp_path,
        demo_mode=True,
        data_source_mode="demo",
        llm_enabled=False,
    )
    settings.ensure_runtime_dirs()
    metadata = MetadataRepository(settings.metadata_db_path)
    metadata.initialize()
    service = ReportAnalysisService(settings, metadata)
    report = LeadershipReportService(settings).fetch(date(2026, 8, 1))

    with pytest.raises(AnalysisUnavailableError, match="未启用"):
        service.analyze_leadership(report)

    with sqlite3.connect(settings.metadata_db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM report_analyses").fetchone()[0] == 0


def test_three_report_types_use_model_results_with_report_facts(tmp_path: Path) -> None:
    settings, service = _services(tmp_path)
    selected_date = date(2026, 8, 18)
    leadership = LeadershipReportService(settings).fetch(selected_date)
    bundle = QueryService(settings).fetch_report_data(
        ReportFilter(report_date=selected_date)
    )

    leadership_analysis = service.analyze_leadership(leadership)
    workshop_analysis = service.analyze_workshop(
        selected_date.isoformat(),
        bundle.workshop_records,
        comparison_records=bundle.workshop_records,
        comparison_date="2026-08-17",
        year_ago_records=bundle.workshop_records,
        year_ago_date="2025-08-18",
    )
    equipment_records = sort_equipment_records(mock_energy(bundle.equipment_records))
    equipment_analysis = service.analyze_equipment(
        selected_date.isoformat(),
        equipment_records,
        energy_data_mode="mock",
        comparison_records=equipment_records,
        comparison_date="2026-08-17",
        year_ago_records=equipment_records,
        year_ago_date="2025-08-18",
    )

    assert leadership_analysis.status == "success"
    assert leadership_analysis.mode == "model"
    assert "2026-08" in leadership_analysis.overview
    assert leadership_analysis.overview != workshop_analysis.overview
    assert not hasattr(equipment_analysis, "limitations")
    assert all(result.facts for result in (
        leadership_analysis,
        workshop_analysis,
        equipment_analysis,
    ))
    for result in (leadership_analysis, workshop_analysis, equipment_analysis):
        assert [fact["id"] for fact in result.facts] == [f"F{index:03}" for index in range(1, 7)]
        assert all(fact.get("id") and fact.get("source") for fact in result.facts)
        assert all("（" not in fact["value"] and "）" not in fact["value"] for fact in result.facts)
        referenced = result.overview + result.features + result.alerts + result.suggestions
        assert set(re.findall(r"\[(F\d{3})\]", referenced)) <= {fact["id"] for fact in result.facts}

        assert result.mode == "model"


def test_same_analysis_input_uses_metadata_cache(tmp_path: Path) -> None:
    settings, service = _services(tmp_path)
    report = LeadershipReportService(settings).fetch(date(2026, 8, 1))

    first = service.analyze_leadership(report)
    second = service.analyze_leadership(report)

    assert first.cached is False
    assert second.cached is True
    assert second.generated_at == first.generated_at
    assert second.overview == first.overview
    assert first.data_version == second.data_version
    assert re.fullmatch(r"[0-9a-f]{64}\.[0-9a-f]{64}", first.data_version or "")


def test_visible_report_version_must_match_analysis_source(tmp_path: Path) -> None:
    settings, service = _services(tmp_path)
    report = LeadershipReportService(settings).fetch(date(2026, 8, 1))
    primary_version = leadership_analysis_source_version(report)
    summary_version = analysis_table_source_version(report.monthly_records)
    visible_version = combined_analysis_source_version(
        primary_version, summary_version
    )

    result = service.analyze_leadership(
        report,
        expected_data_version=visible_version,
    )

    assert result.data_version == visible_version
    with pytest.raises(AnalysisDataVersionMismatch, match="刷新当前报表"):
        service.analyze_leadership(
            report,
            expected_data_version=combined_analysis_source_version(
                primary_version, "0" * 64
            ),
        )

    report_date = date(2026, 8, 18)
    records = QueryService(settings).fetch_report_data(
        ReportFilter(report_date=report_date)
    ).workshop_records
    workshop_version = combined_analysis_source_version(
        workshop_analysis_source_version(report_date.isoformat(), records)
    )
    workshop_result = service.analyze_workshop(
        report_date.isoformat(),
        records,
        expected_data_version=workshop_version,
    )
    assert workshop_result.data_version == workshop_version

    equipment_primary = "1" * 64
    energy_summary = [{"report_date": "2026-08", "task_count": 1}]
    equipment_version = combined_analysis_source_version(
        equipment_primary,
        analysis_table_source_version(energy_summary),
    )
    equipment_result = service.analyze_equipment(
        report_date.isoformat(),
        [],
        energy_summary_rows=energy_summary,
        source_data_version=equipment_primary,
        expected_data_version=equipment_version,
    )
    assert equipment_result.data_version == equipment_version
    with pytest.raises(AnalysisDataVersionMismatch, match="刷新当前报表"):
        service.analyze_equipment(
            report_date.isoformat(),
            [],
            energy_summary_rows=[{"report_date": "2026-08", "task_count": 2}],
            source_data_version=equipment_primary,
            expected_data_version=equipment_version,
        )


def test_data_version_is_stable_for_key_order_and_changes_with_business_data() -> None:
    first = {
        "facts": [{"id": "F001", "value": "10 项"}],
        "summary": {"work_time": 20.04, "cut_length": 30.0},
    }
    reordered = {
        "summary": {"cut_length": 30.0, "work_time": 20.04},
        "facts": [{"value": "10 项", "id": "F001"}],
    }
    changed = {
        "summary": {"cut_length": 31.0, "work_time": 20.04},
        "facts": [{"value": "10 项", "id": "F001"}],
    }

    assert generate_data_version(first) == generate_data_version(reordered)
    assert generate_data_version(first) != generate_data_version(changed)


def test_delayed_cache_write_cannot_replace_a_newer_completed_analysis(tmp_path, monkeypatch):
    _, service = _services(tmp_path)
    now = datetime(2026, 10, 19, 8, tzinfo=service.timezone)
    old_stamp = now.isoformat(timespec='seconds')
    waiting, release = Event(), Event()
    save = service.metadata.save_analysis
    calls = []

    class Clock:
        @staticmethod
        def now(_timezone):
            return now

    def model(*args):
        calls.append(1)
        return AnalysisResult(status='success', mode='model', overview=f'第{len(calls)}次分析',
                              features='特征', alerts='关注', suggestions='建议')

    def delayed_save(**kwargs):
        if kwargs['generated_at'] == old_stamp:
            waiting.set()
            assert release.wait(3)
        save(**kwargs)

    def analyze(force):
        with background_llm_work():
            return service._analyze(report_type='workshop', report_date='2026-10-19',
                                    context={'facts': []}, force=force)

    monkeypatch.setattr('app.services.report_analysis_service.datetime', Clock)
    monkeypatch.setattr(service.llm, 'analyze_context', model)
    monkeypatch.setattr(service.metadata, 'save_analysis', delayed_save)
    with ThreadPoolExecutor(max_workers=2) as executor:
        old = executor.submit(analyze, True)
        try:
            assert waiting.wait(1)
            now += timedelta(seconds=1)
            newer = executor.submit(analyze, True).result(timeout=2)
        finally:
            release.set()
        old.result(timeout=2)
    cached = analyze(False)
    assert cached.generated_at == newer.generated_at
    assert cached.overview == newer.overview
    assert len(calls) == 2, 'both periodic calls must run while subsequent reads reuse the newest completed result'


def test_force_refresh_replaces_same_version_cache(tmp_path: Path, monkeypatch) -> None:
    settings, service = _services(tmp_path)
    report = LeadershipReportService(settings).fetch(date(2026, 8, 1))
    now = datetime(2026, 8, 1, 8, tzinfo=service.timezone)

    class Clock:
        @staticmethod
        def now(_timezone):
            return now

    monkeypatch.setattr('app.services.report_analysis_service.datetime', Clock)

    first = service.analyze_leadership(report)
    now += timedelta(seconds=1)
    forced = service.analyze_leadership(report, force=True)
    now += timedelta(seconds=1)
    cached = service.analyze_leadership(report)

    assert forced.cached is False
    assert forced.data_version == first.data_version
    assert cached.cached is True
    assert cached.data_version == forced.data_version
    assert cached.generated_at == forced.generated_at

    with sqlite3.connect(settings.metadata_db_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(report_analyses)")}
        rows = connection.execute(
            "SELECT data_version FROM report_analyses WHERE report_type = 'leadership'"
        ).fetchall()
    assert "data_version" in columns
    assert rows == [(forced.data_version,)]
    assert service.metadata.get_analysis_source_version(
        report_type="leadership",
        report_date="2026-08",
    ) == {
        "data_version": forced.data_version,
        # Source checks publish their own time, even when the model result is cached.
        "updated_at": now.isoformat(timespec="seconds"),
    }


def test_concurrent_same_version_requests_call_model_once(tmp_path: Path, monkeypatch) -> None:
    settings, service = _services(tmp_path)
    service.settings = replace(settings, llm_enabled=True)
    report = LeadershipReportService(settings).fetch(date(2026, 8, 1))
    calls = 0

    def model(_report_type, context):
        nonlocal calls
        calls += 1
        time.sleep(0.05)
        return model_analysis(_report_type, context)

    monkeypatch.setattr(service.llm, "analyze_context", model)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: service.analyze_leadership(report), range(2)))

    assert calls == 1
    assert sorted(result.cached for result in results) == [False, True]
    assert results[0].data_version == results[1].data_version


def test_page_promotes_queued_background_analysis_without_duplicate_model(tmp_path, monkeypatch, caplog):
    settings, service = _services(tmp_path)
    settings = replace(settings, llm_max_concurrent=4)
    service.settings = settings
    report = LeadershipReportService(settings).fetch(date(2026, 8, 1))
    capacity = _LlmCapacity()
    monkeypatch.setattr("app.services.llm_service._LLM_CAPACITY", capacity)
    started, admitted, finish = Event(), Event(), Event()
    calls = 0
    caplog.set_level("INFO", logger="app.services.analysis_execution")

    def model(report_type, context):
        nonlocal calls
        calls += 1
        started.set()
        with llm_capacity(settings):
            admitted.set()
            assert finish.wait(3)
            return model_analysis(report_type, context)

    def background():
        with background_llm_work():
            return service.analyze_leadership(report)

    monkeypatch.setattr(service.llm, "analyze_context", model)
    # Other automatic work fills the two background slots; two page slots remain.
    with background_llm_work(), llm_capacity(settings), llm_capacity(settings):
        with ThreadPoolExecutor(max_workers=2) as executor:
            producer = executor.submit(background)
            try:
                assert started.wait(1)
                assert not admitted.wait(0.1)
                page = executor.submit(service.analyze_leadership, report)
                assert admitted.wait(1), "page must promote its existing background producer into a free slot"
                assert calls == 1
            finally:
                finish.set()
            assert producer.result(timeout=2).cached is False
            assert page.result(timeout=2).cached is True
    assert capacity._active == capacity._background_active == capacity._foreground_waiting == 0
    assert service._generation_locks == {}
    messages = [record.getMessage() for record in caplog.records]
    assert any("background=True" in message and "promoted=True" in message for message in messages)
    assert any("cache=shared" in message for message in messages)


@pytest.mark.parametrize("failure, expected, message", [
    (LlmRequestTimeout, AnalysisUnavailableError, "模型响应超时"),
    (ReportOperationTimeout, ReportOperationTimeout, "timeout"),
])
def test_waiters_share_failed_attempt_but_later_retry_can_recover(tmp_path, monkeypatch, failure, expected, message):
    settings, service = _services(tmp_path)
    report = LeadershipReportService(settings).fetch(date(2026, 8, 1))
    started, finish = Event(), Event()
    calls = 0

    def model(report_type, context):
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            assert finish.wait(3)
            raise failure("timeout")
        return model_analysis(report_type, context)

    monkeypatch.setattr(service.llm, "analyze_context", model)
    with ThreadPoolExecutor(max_workers=2) as executor:
        producer = executor.submit(service.analyze_leadership, report)
        try:
            assert started.wait(1)
            page = executor.submit(service.analyze_leadership, report)
            deadline = time.monotonic() + 1
            waiting = False
            while time.monotonic() < deadline:
                with service._generation_guard:
                    waiting = any(state["users"] == 2 for state in service._generation_locks.values())
                if waiting:
                    break
                time.sleep(0.005)
            assert waiting
        finally:
            finish.set()
        for future in (producer, page):
            with pytest.raises(expected, match=message):
                future.result(timeout=2)
    assert calls == 1, "a waiting page must not silently pay for a second full attempt"
    assert service._generation_locks == {}
    assert service.analyze_leadership(report).cached is False
    assert service.analyze_leadership(report).cached is True
    assert calls == 2


def test_cancelled_page_retracts_priority_without_cancelling_shared_background_work(tmp_path, monkeypatch):
    settings, service = _services(tmp_path)
    settings = replace(settings, llm_max_concurrent=4)
    service.settings = settings
    report = LeadershipReportService(settings).fetch(date(2026, 8, 1))
    capacity = _LlmCapacity()
    monkeypatch.setattr("app.services.llm_service._LLM_CAPACITY", capacity)
    started, admitted = Event(), Event()

    def model(report_type, context):
        started.set()
        with llm_capacity(settings):
            admitted.set()
            return model_analysis(report_type, context)

    def background():
        with background_llm_work():
            return service.analyze_leadership(report)

    execution = sql_request_cancellations.activate("page-leaves")

    def page_request():
        token = bind_request_id(execution)
        try:
            return service.analyze_leadership(report)
        finally:
            reset_request_id(token)

    monkeypatch.setattr(service.llm, "analyze_context", model)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            with ExitStack() as bg_slots, ExitStack() as fg_slots:
                with background_llm_work():
                    for _ in range(2):
                        bg_slots.enter_context(llm_capacity(settings))
                for _ in range(2):
                    fg_slots.enter_context(llm_capacity(settings))
                producer = executor.submit(background)
                assert started.wait(1)
                page = executor.submit(page_request)
                with capacity._condition:
                    assert capacity._condition.wait_for(lambda: capacity._foreground_waiting == 1, timeout=1)
                sql_request_cancellations.cancel(execution)
                with pytest.raises(SqlRequestCancelled):
                    page.result(timeout=2)
                with capacity._condition:
                    assert capacity._condition.wait_for(lambda: capacity._foreground_waiting == 0, timeout=1)
                fg_slots.close()
                assert not admitted.wait(0.15), "departed page must not leave background work using page priority"
                assert not producer.done()
            assert producer.result(timeout=2).cached is False
    finally:
        sql_request_cancellations.finish(execution)
    assert admitted.is_set()
    assert capacity._active == capacity._background_active == capacity._foreground_waiting == 0
    assert service._generation_locks == {}


@pytest.mark.parametrize("stop", ["timeout", "cancel", "report_deadline"])
def test_same_version_wait_can_stop_without_releasing_active_generation(tmp_path, monkeypatch, stop):
    settings, service = _services(tmp_path)
    service.settings = replace(settings, llm_queue_timeout_seconds=1)
    report = LeadershipReportService(settings).fetch(date(2026, 8, 1))
    entered, release = Event(), Event()
    calls = 0

    def model(report_type, context):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            assert release.wait(4)
        return model_analysis(report_type, context)

    monkeypatch.setattr(service.llm, "analyze_context", model)
    execution = sql_request_cancellations.activate("same-version")

    def waiting_request():
        token = bind_request_id(execution)
        try:
            if stop == "report_deadline":
                with report_deadline(0.05):
                    return service.analyze_leadership(report)
            return service.analyze_leadership(report)
        finally:
            reset_request_id(token)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(service.analyze_leadership, report)
        try:
            assert entered.wait(1)
            waiter = executor.submit(waiting_request)
            if stop == "cancel":
                deadline = time.monotonic() + 1
                while time.monotonic() < deadline:
                    with service._generation_guard:
                        waiting = any(state["users"] == 2 for state in service._generation_locks.values())
                    if waiting:
                        break
                    time.sleep(0.005)
                assert waiting
                sql_request_cancellations.cancel(execution)
            expected = {
                "timeout": AnalysisUnavailableError,
                "cancel": SqlRequestCancelled,
                "report_deadline": ReportOperationTimeout,
            }[stop]
            with pytest.raises(expected):
                waiter.result(timeout=2)
            assert calls == 1
            assert not first.done()
            with service._generation_guard:
                assert [state["users"] for state in service._generation_locks.values()] == [1]
        finally:
            release.set()
            first.result(timeout=2)
            sql_request_cancellations.finish(execution)

    assert service._generation_locks == {}
    assert service.analyze_leadership(report).cached is True
    assert service.analyze_leadership(report, force=True).cached is False
    assert calls == 2


@pytest.mark.parametrize("error", [SqlRequestCancelled, ReportOperationTimeout])
def test_model_cancellation_and_report_deadline_are_not_ai_unavailability(tmp_path, monkeypatch, error):
    settings, service = _services(tmp_path)
    report = LeadershipReportService(settings).fetch(date(2026, 8, 1))

    def interrupted(*args):
        raise error("stopped")

    monkeypatch.setattr(service.llm, "analyze_context", interrupted)
    with pytest.raises(error, match="stopped"):
        service.analyze_leadership(report)
    assert service._generation_locks == {}
    monkeypatch.setattr(service.llm, "analyze_context", model_analysis)
    assert service.analyze_leadership(report).cached is False


def test_versions_change_when_previously_unwatched_report_values_change(tmp_path: Path) -> None:
    settings, service = _services(tmp_path)
    selected_date = date(2026, 8, 18)
    report = LeadershipReportService(settings).fetch(selected_date)
    bundle = QueryService(settings).fetch_report_data(
        ReportFilter(report_date=selected_date)
    )

    leadership_before = service.analyze_leadership(report)
    leadership_after = service.analyze_leadership(
        replace(
            report,
            current_month_total=replace(
                report.current_month_total,
                cut_length_m=report.current_month_total.cut_length_m + 1,
            ),
        )
    )

    workshop_before = service.analyze_workshop(
        selected_date.isoformat(), bundle.workshop_records
    )
    workshop_after = service.analyze_workshop(
        selected_date.isoformat(),
        [
            replace(
                bundle.workshop_records[0],
                project_code="VERSION-CHANGED-PROJECT",
            ),
            *bundle.workshop_records[1:],
        ],
    )

    equipment_records = sort_equipment_records(mock_energy(bundle.equipment_records))
    equipment_before = service.analyze_equipment(
        selected_date.isoformat(), equipment_records, energy_data_mode="mock"
    )
    equipment_after = service.analyze_equipment(
        selected_date.isoformat(),
        [
            replace(
                equipment_records[0],
                electricity_consumption=(
                    equipment_records[0].electricity_consumption or 0
                ) + 1,
            ),
            *equipment_records[1:],
        ],
        energy_data_mode="mock",
    )

    assert leadership_before.data_version != leadership_after.data_version
    assert workshop_before.data_version != workshop_after.data_version
    assert equipment_before.data_version != equipment_after.data_version


def test_empty_reports_keep_six_facts_without_inventing_comparisons(tmp_path):
    settings, service = _services(tmp_path)
    report = LeadershipReportService(settings).fetch(date(2026, 8, 1))
    empty_total = replace(report.current_month_total, task_count=0)
    report = replace(
        report,
        current_month_total=empty_total,
        monthly_records=[],
        daily_records=[],
        year_ago_total=None,
        comparison_total=None,
        comparison_start=None,
        comparison_end=None,
        project_distribution=[],
    )
    results = [
        service.analyze_leadership(report),
        service.analyze_workshop("2026-08-01", []),
        service.analyze_equipment("2026-08-01", []),
    ]
    for result in results:
        assert len(result.facts) == 6
        references = set(re.findall(r"\[(F\d{3})\]", result.overview + result.features + result.alerts + result.suggestions))
        assert references <= {fact["id"] for fact in result.facts}
    leader_facts = {fact["id"]: fact["value"] for fact in results[0].facts}
    assert leader_facts["F004"].startswith("无法比较")
    assert leader_facts["F005"].startswith("无法比较")
    assert leader_facts["F006"].startswith("暂无工程数据")
    assert results[1].facts[3]["value"] == "暂无任务"
    assert results[2].facts[2]["value"].startswith("缺失")


def test_energy_quality_counts_a_task_once_even_with_multiple_issues(tmp_path):
    settings, service = _services(tmp_path)
    bundle = QueryService(settings).fetch_report_data(ReportFilter(report_date=date(2026, 8, 18)))
    row = replace(bundle.equipment_records[0], gas_consumption=None, electricity_consumption=None, gas_data_status="overlap", electricity_data_status="gap")
    result = service.analyze_equipment("2026-08-18", [row])
    quality = result.facts[-1]
    assert quality["id"] == "F006"
    assert quality["value"] == "1 项"
    assert "仪表记录时间重叠 1 项" in quality["source"]
    assert "仪表记录不完整 1 项" in quality["source"]


def test_partial_task_energy_facts_keep_actual_meter_source(tmp_path):
    settings, service = _services(tmp_path)
    bundle = QueryService(settings).fetch_report_data(ReportFilter(report_date=date(2026, 8, 18)))
    measured = replace(
        bundle.equipment_records[0],
        gas_consumption=Decimal("12.5"),
        electricity_consumption=Decimal("30.0"),
        gas_data_status="interpolated",
        electricity_data_status="measured",
    )
    missing = replace(
        bundle.equipment_records[0],
        task_id="missing-energy-task",
        gas_consumption=None,
        electricity_consumption=None,
        gas_data_status="gap",
        electricity_data_status="gap",
    )

    result = service.analyze_equipment(
        "2026-08-18", [measured, missing],
        energy_data_mode="partial",
        energy_units={"gas": "Nm³", "electricity": "kWh"},
    )
    facts = {fact["id"]: fact for fact in result.facts}
    for fact_id in ("F003", "F004"):
        assert "数据来源 实际仪表数据（仅汇总有记录的任务）" in facts[fact_id]["source"]
        assert "有" in facts[fact_id]["source"] and "记录 1 项，共 2 项" in facts[fact_id]["source"]
        assert "来源 未知" not in facts[fact_id]["source"]
    assert facts["F003"]["value"] == "12.5 Nm³"
    assert facts["F004"]["value"] == "30.0 kWh"
    assert "根据前后读数估算 1 项" in facts["F003"]["source"]


def test_partial_energy_does_not_claim_a_missing_channel_has_meter_data(tmp_path):
    settings, service = _services(tmp_path)
    bundle = QueryService(settings).fetch_report_data(ReportFilter(report_date=date(2026, 8, 18)))
    row = replace(
        bundle.equipment_records[0],
        gas_consumption=Decimal("12.5"),
        electricity_consumption=None,
        gas_data_status="measured",
        electricity_data_status="gap",
    )

    result = service.analyze_equipment(
        "2026-08-18", [row], energy_data_mode="partial",
        energy_units={"gas": "Nm³", "electricity": "kWh"},
    )
    facts = {fact["id"]: fact for fact in result.facts}
    assert "数据来源 实际仪表数据" in facts["F003"]["source"]
    assert "仅汇总有记录的任务" not in facts["F003"]["source"]
    assert facts["F004"]["value"] == "缺失"
    assert "无有效电耗记录" in facts["F004"]["source"]
    assert "数据来源 实际仪表数据" not in facts["F004"]["source"]


def test_model_receives_six_facts_and_two_complete_report_tables(tmp_path, monkeypatch):
    settings, service = _services(tmp_path)
    report = LeadershipReportService(settings).fetch(date(2026, 8, 1))
    bundle = QueryService(settings).fetch_report_data(
        ReportFilter(report_date=date(2026, 8, 18))
    )
    workshop_records = [
        replace(
            bundle.workshop_records[0],
            confirmed_by_id="worker-internal-id",
            confirmed_by="张三",
            confirmed_by_company_no="W001",
        ),
        *bundle.workshop_records[1:],
    ]
    equipment_records = sort_equipment_records(mock_energy(bundle.equipment_records))
    energy_summary = [
        {
            "report_date": "2026-08",
            "task_count": len(equipment_records),
            "work_time_seconds": 120,
            "electricity": {"value": 30.0, "status": "measured"},
            "gas": {"value": 12.5, "status": "measured"},
        }
    ]
    contexts = {}

    def model(report_type, context):
        contexts[report_type] = context
        return model_analysis(report_type, context)

    monkeypatch.setattr(service.llm, "analyze_context", model)
    results = {
        "leadership": service.analyze_leadership(report),
        "workshop": service.analyze_workshop(
            "2026-08-18", workshop_records
        ),
        "equipment": service.analyze_equipment(
            "2026-08-18",
            equipment_records,
            energy_data_mode="mock",
            energy_summary_rows=energy_summary,
        ),
    }

    assert [table["name"] for table in contexts["leadership"]["tables"]] == [
        "区间每日明细",
        "近12个月月度汇总",
    ]
    assert contexts["leadership"]["tables"][0]["row_count"] == len(report.daily_records)
    assert contexts["leadership"]["tables"][1]["row_count"] == len(report.monthly_records)

    workshop_tables = contexts["workshop"]["tables"]
    assert [table["name"] for table in workshop_tables] == ["任务明细", "工人工时汇总"]
    assert workshop_tables[0]["row_count"] == len(workshop_records)
    assert workshop_tables[0]["rows"][0]["confirmed_by"] == "张三"
    assert "confirmed_by_id" not in workshop_tables[0]["rows"][0]
    assert workshop_tables[1]["rows"][0]["company_no"] == "W001"
    assert "worker_id" not in workshop_tables[1]["rows"][0]

    equipment_tables = contexts["equipment"]["tables"]
    assert [table["name"] for table in equipment_tables] == [
        "任务能耗明细",
        "近12个月月度能耗汇总",
    ]
    assert equipment_tables[0]["row_count"] == len(equipment_records)
    assert equipment_tables[1]["rows"] == energy_summary
    assert "energy_details" not in equipment_tables[0]["rows"][0]

    for report_type, result in results.items():
        assert len(contexts[report_type]["facts"]) == len(result.facts) == 6
        assert all(table["row_count"] == len(table["rows"]) for table in contexts[report_type]["tables"])
        assert result.mode == "model"

def test_failed_model_call_is_not_cached_and_next_request_retries(tmp_path, monkeypatch):
    settings, service = _services(tmp_path)
    report = LeadershipReportService(settings).fetch(date(2026, 8, 1))
    calls = 0

    def model(_report_type, context):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("model temporarily unavailable")
        return model_analysis(_report_type, context)

    monkeypatch.setattr(service.llm, "analyze_context", model)
    with pytest.raises(AnalysisUnavailableError, match="暂不可用"):
        service.analyze_leadership(report)

    with sqlite3.connect(settings.metadata_db_path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM report_analyses WHERE report_type = 'leadership'"
        ).fetchone()[0]
    assert count == 0
    assert service.metadata.get_analysis_source_version(
        report_type="leadership",
        report_date="2026-08",
    )["data_version"] == combined_analysis_source_version(
        leadership_analysis_source_version(report),
        analysis_table_source_version(report.monthly_records),
    )

    recovered = service.analyze_leadership(report)
    assert recovered.mode == "model"
    assert recovered.cached is False
    assert calls == 2


@pytest.mark.parametrize('error, message', [
    (AnalysisCapacityError('智能分析排队超时，请稍后重试。'), '排队超时'),
    (LlmRequestTimeout('deadline'), '模型响应超时'),
    (httpx.ReadTimeout('read'), '模型响应超时'),
    (httpx.ConnectError('connect'), '无法连接模型接口'),
    *[(httpx.HTTPStatusError('provider error', request=httpx.Request('POST', 'http://llm.local'),
                            response=httpx.Response(status)), message)
      for status, message in [(429, '限流'), (401, '认证失败'), (403, '认证失败'), (503, 'HTTP 503')]],
])
def test_model_failure_explains_recovery_action_and_does_not_poison_cache(tmp_path, monkeypatch, caplog, error, message):
    settings, service = _services(tmp_path)
    report = LeadershipReportService(settings).fetch(date(2026, 8, 1))

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(service.llm, 'analyze_context', fail)
    with pytest.raises(AnalysisUnavailableError, match=message):
        service.analyze_leadership(report)
    assert 'device=Cutter01' in caplog.text
    assert 'report_type=leadership' in caplog.text
    monkeypatch.setattr(service.llm, 'analyze_context', model_analysis)
    assert service.analyze_leadership(report).cached is False
    assert service.analyze_leadership(report).cached is True


def test_oversized_report_explains_input_problem_instead_of_model_outage(tmp_path, monkeypatch):
    settings, service = _services(tmp_path)
    report = LeadershipReportService(settings).fetch(date(2026, 8, 1))

    def fail(*args, **kwargs):
        raise AnalysisInputTooLarge('智能分析输入超过配置容量，请缩小报表范围后重试。')

    monkeypatch.setattr(service.llm, 'analyze_context', fail)
    with pytest.raises(AnalysisUnavailableError, match='缩小报表范围'):
        service.analyze_leadership(report)


@pytest.mark.parametrize('report_type', ['leadership', 'workshop', 'equipment'])
def test_response_header_timeout_is_explained_and_all_report_types_can_recover(tmp_path, monkeypatch, report_type):
    settings, service = _services(tmp_path)
    service.llm = LlmService(replace(settings, llm_api_key='test-key', llm_base_url='http://llm.test', llm_timeout=90))
    original_client = httpx.Client
    attempts = []

    def respond(request):
        attempts.append(request)
        assert request.extensions['timeout']['read'] > 30
        if len(attempts) == 1:
            # The deployed failure occurs before any response headers arrive.
            raise httpx.ReadTimeout('The read operation timed out', request=request)
        content = {field: '连接恢复后的分析。' for field in ('overview', 'features', 'alerts', 'suggestions')}
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(content)}}]})

    transport = httpx.MockTransport(respond)
    monkeypatch.setattr('app.services.llm_service.httpx.Client',
                        lambda **kwargs: original_client(transport=transport, **kwargs))
    parameters = dict(report_type=report_type, report_date='2026-10-13',
                      context={'report_date': '2026-10-13', 'facts': [], 'tables': []}, force=False)
    with pytest.raises(AnalysisUnavailableError, match='模型响应超时'):
        service._analyze(**parameters)
    assert service._analyze(**parameters).cached is False
    assert service._analyze(**parameters).cached is True
    assert len(attempts) == 2


def test_model_output_is_used_without_content_rejection(tmp_path, monkeypatch):
    settings, service = _services(tmp_path)
    model_settings = replace(
        settings,
        llm_enabled=True,
        llm_api_key="test-key",
        llm_base_url="http://llm.test",
    )
    service.settings = model_settings
    service.llm = LlmService(model_settings)

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            content = {
                "overview": "本期完成 999 项任务 [F001]。",
                "features": "任务趋势需要关注 [F004]。",
                "alerts": "任务数据存在风险 [F001]。",
                "suggestions": "建议复核任务记录 [F001]。",
            }
            return {"choices": [{"message": {"content": json.dumps(content)}}]}

    calls = 0

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, *_args, **_kwargs):
            nonlocal calls
            calls += 1
            return FakeResponse()

    monkeypatch.setattr("app.services.llm_service.httpx.Client", FakeClient)
    report = LeadershipReportService(settings).fetch(date(2026, 8, 1))

    result = service.analyze_leadership(report)
    assert result.overview == "本期完成 999 项任务 [F001]。"
    assert result.mode == "model"
    assert calls == 1

    with sqlite3.connect(settings.metadata_db_path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM report_analyses WHERE report_type = 'leadership'"
        ).fetchone()[0]
    assert count == 1
