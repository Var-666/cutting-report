from app.services.query_service import QueryService
from tests.line_fixtures import source_client
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from tests.line_fixtures import cutting_settings, source_client
from app.database.metadata import MetadataRepository
from app.models.domain import ReportFilter
from app.services.llm_service import AnalysisOutputValidationError, LlmRequestTimeout
from app.services.pipeline import ReportPipeline, ReportPipelineCancelled, ReportAnalysisPendingError
from app.core.report_deadline import ReportOperationTimeout
from app.core.request_cancellation import SqlRequestCancelled
from tests.analysis_fakes import model_analysis


def test_unavailable_analysis_log_identifies_the_report_and_reason(tmp_path, caplog):
    from app.services.report_analysis_service import AnalysisUnavailableError
    pipeline = ReportPipeline.__new__(ReportPipeline)
    pipeline.settings = cutting_settings(project_root=tmp_path, llm_enabled=True)
    pipeline._cancellation_check = None
    def unavailable():
        raise AnalysisUnavailableError('模型响应超时')
    with pytest.raises(ReportAnalysisPendingError):
        pipeline._analyze_or_wait(unavailable, report_id='report-to-diagnose', report_type='leadership')
    assert 'report_id=report-to-diagnose report_type=leadership reason=模型响应超时' in caplog.text


SCENARIOS = [(kind, interval) for kind in ("leadership", "workshop", "equipment")
             for interval in (False, True)]
REPORT_DATE = date(2026, 8, 18)


def pipeline_job(state_database, tmp_path, report_type, interval, *, llm_enabled=True):
    settings = cutting_settings(project_root=tmp_path, data_source_mode="demo", demo_mode=True,
                        llm_enabled=llm_enabled)
    settings.ensure_runtime_dirs()
    vendor_dir = tmp_path / "vendor"
    vendor_dir.mkdir(exist_ok=True)
    (vendor_dir / "echarts.min.js").write_text("window.echarts={};")
    metadata = MetadataRepository(state_database, device_id='Cutter01')
    pipeline = ReportPipeline(settings, metadata, query_service=QueryService(settings, client=source_client(settings)))
    now = datetime.now(ZoneInfo(settings.timezone)).isoformat(timespec="seconds")
    if report_type == "workshop" and not interval:
        user = pipeline.workshop_repository.bootstrap_admin(
            username="tester", display_name="测试工人", password="safe-password", now=now,
        )
        records = pipeline.query_service.fetch_report_data(ReportFilter(report_date=REPORT_DATE))
        pipeline.workshop_repository.create_daily_report(
            report_date=REPORT_DATE.isoformat(), source_mode="demo", records=records.workshop_records,
            user=user, now=now, scope_machine_no=settings.report_machine_no,
        )
    metadata.create_job(
        report_id="ai-isolation", report_date=REPORT_DATE.isoformat(),
        project_code=None, ship_no=None, request_id="ai-isolation", created_at=now,
        report_type=report_type,
        period_start=REPORT_DATE.isoformat() if interval else None,
        period_end="2026-08-19" if interval else None,
    )
    return pipeline, metadata.claim_next_job(now)


def intercept_history(monkeypatch, pipeline, report_type, interval, error, *, fail_primary=False):
    calls = []
    if report_type == "leadership" and not interval:
        return calls  # Monthly comparisons come from the same required query.
    if report_type == "leadership":
        owner, method = pipeline.range_service, "leadership"
        get_date = lambda args: args[0]
    elif report_type == "equipment":
        owner, method = pipeline.query_service, "fetch_equipment_data"
        get_date = lambda args: args[0].report_date
    elif interval:
        owner, method = pipeline.range_service, "workshop_records"
        get_date = lambda args: args[0]
    else:
        owner, method = pipeline.workshop_repository, "get_daily_report_records"
        get_date = lambda args: date.fromisoformat(args[0])
    original = getattr(owner, method)

    def query(*args, **kwargs):
        primary = get_date(args) == REPORT_DATE
        if primary == fail_primary:
            calls.append(get_date(args))
            raise error("source unavailable")
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, method, query)
    return calls


def capture_facts(monkeypatch, pipeline):
    captured = {}
    original = pipeline.report_service.render

    def render(*, report, **kwargs):
        for key in ("filters", "units", "charts", "leadership", "summary", "data_quality",
                    "attention_items", "workshop_records", "worker_summaries", "equipment_summary",
                    "equipment_records", "energy_notice"):
            if key in report:
                captured[key] = report[key]
        if "leadership" in captured:
            leadership = dict(captured["leadership"])
            if leadership.get("snapshot"):
                # Each independent fetch has its own observation timestamp.
                leadership["snapshot"] = {key: value for key, value in leadership["snapshot"].items()
                                          if key != "data_as_of"}
            captured["leadership"] = leadership
        if "energy_month" in report:
            captured["energy_month_rows"] = report["energy_month"]["rows"]
        return original(report=report, **kwargs)

    monkeypatch.setattr(pipeline.report_service, "render", render)
    return captured


@pytest.mark.parametrize(("report_type", "interval"), SCENARIOS)
@pytest.mark.parametrize("failure", ["disabled", "history", "model_timeout", "model_invalid"])
def test_optional_ai_failure_does_not_discard_complete_factual_report(state_database, baseline_state,
    tmp_path, monkeypatch, report_type, interval, failure,
):
    pipeline, job = pipeline_job(state_database, tmp_path, report_type, interval, llm_enabled=failure != "disabled")
    # Compare every published deterministic section with a successful AI run;
    # a nonzero task count alone would miss lost details or altered totals.
    baseline, baseline_job = pipeline_job(baseline_state[0], tmp_path / "baseline", report_type, interval)
    baseline.analysis_service.llm.analyze_context = model_analysis
    expected_facts = capture_facts(monkeypatch, baseline)
    baseline.run(baseline_job)
    actual_facts = capture_facts(monkeypatch, pipeline)
    historical = []
    if failure in {"disabled", "history"}:
        historical = intercept_history(monkeypatch, pipeline, report_type, interval, OSError)
    model_calls = []

    def model(kind, context):
        model_calls.append(kind)
        if failure == "model_timeout":
            raise LlmRequestTimeout("slow model")
        if failure == "model_invalid":
            raise AnalysisOutputValidationError("invalid model output")
        return model_analysis(kind, context)

    monkeypatch.setattr(pipeline.analysis_service.llm, "analyze_context", model)
    has_optional_history = report_type != "leadership" or interval
    waiting = failure != 'history' or has_optional_history
    if waiting:
        with pytest.raises(ReportAnalysisPendingError):
            pipeline.run(job)
        assert pipeline.metadata.get_job(job['report_id'])['relative_path'] is None
        assert actual_facts == {}
        return
    pipeline.run(job)
    completed = pipeline.metadata.get_job(job["report_id"])
    # The file is rendered by the actual template, rather than faking completion.
    assert completed["status"] == "completed"
    assert completed["task_count"] > 0
    html = (pipeline.settings.reports_dir / completed["relative_path"]).read_text()
    assert completed["ai_status"] == 'success'
    assert "报告结束" in html
    assert actual_facts == expected_facts
    if failure == "disabled":
        assert historical == []
        assert model_calls == []
    if failure == "history" and has_optional_history:
        assert len(historical) == 1
        assert model_calls == []


@pytest.mark.parametrize(("report_type", "interval"), SCENARIOS)
def test_required_source_failure_still_prevents_report_publication(state_database, tmp_path, monkeypatch, report_type, interval):
    pipeline, job = pipeline_job(state_database, tmp_path, report_type, interval)
    if report_type == "leadership" and not interval:
        monkeypatch.setattr(pipeline.leadership_service, "fetch",
                            lambda *args: (_ for _ in ()).throw(OSError("source unavailable")))
    else:
        intercept_history(monkeypatch, pipeline, report_type, interval, OSError, fail_primary=True)
    with pytest.raises(OSError, match="source unavailable"):
        pipeline.run(job)
    assert pipeline.metadata.get_job(job["report_id"])["status"] != "completed"
    assert list(pipeline.settings.reports_dir.rglob("*.html")) == []


@pytest.mark.parametrize("error", [SqlRequestCancelled, ReportOperationTimeout, ReportPipelineCancelled])
def test_control_signals_in_ai_preparation_are_not_degraded(state_database, tmp_path, monkeypatch, error):
    pipeline, job = pipeline_job(state_database, tmp_path, "equipment", False)
    intercept_history(monkeypatch, pipeline, "equipment", False, error)
    with pytest.raises(ReportAnalysisPendingError if error is ReportOperationTimeout else error, match="source unavailable"):
        pipeline.run(job)
    assert pipeline.metadata.get_job(job["report_id"])["status"] != "completed"


@pytest.mark.parametrize(('report_type', 'interval'), SCENARIOS)
def test_report_uses_matching_success_before_optional_queries_or_model(state_database, tmp_path, monkeypatch, report_type, interval):
    pipeline, job = pipeline_job(state_database, tmp_path, report_type, interval)
    if report_type == 'leadership' and not interval:
        from pathlib import Path
        pipeline.settings.echarts_vendor_path.write_text((Path(__file__).parents[2] / 'vendor/echarts.min.js').read_text())
    pipeline.analysis_service.llm.analyze_context = model_analysis
    pipeline.run(job)
    original = pipeline.metadata.get_job(job['report_id'])
    original_html = (pipeline.settings.reports_dir / original['relative_path']).read_text()
    monkeypatch.setattr(pipeline.analysis_service.llm, 'analyze_context', lambda *_: pytest.fail('cached report must not wait on a model'))
    intercept_history(monkeypatch, pipeline, report_type, interval, OSError)
    pipeline.metadata.create_job(report_id='cached-report', device_id='Cutter01', report_type=report_type,
        report_date=job['report_date'], project_code=None, ship_no=None, request_id='cached-report',
        created_at=job['created_at'], period_start=job.get('period_start'), period_end=job.get('period_end'))
    retry = pipeline.metadata.claim_next_job(job['created_at'])
    pipeline.run(retry)
    completed = pipeline.metadata.get_job('cached-report')
    assert completed['status'] == 'completed' and completed['ai_status'] == 'success'
    html = (pipeline.settings.reports_dir / completed['relative_path']).read_text()
    assert '沿用同一报表范围的历史成功分析' in html
    assert '智能分析暂不可用' not in html
    assert '本期生产记录已整理' in html and '本期生产记录已整理' in original_html
    assert 'class="analysis-sparkline"' not in html
    if report_type == 'leadership' and not interval:
        from app.services.pdf_service import PdfService
        email = PdfService(operation_timeout_seconds=30).render_email(pipeline.settings.reports_dir / completed['relative_path'])
        assert '沿用同一报表范围的历史成功分析' in email.html
        assert '本期生产记录已整理' in email.html
        assert '智能分析暂不可用' not in email.html


def test_waiting_report_survives_consumer_restart_and_publishes_only_after_model_recovers(state_database, tmp_path):
    from unittest.mock import Mock
    from app.workers.report_worker import ReportWorker
    pipeline, job = pipeline_job(state_database, tmp_path, 'workshop', False)
    metadata = pipeline.metadata
    automation = Mock()
    def unavailable(*_):
        raise LlmRequestTimeout('fixture model outage')
    pipeline.analysis_service.llm.analyze_context = unavailable
    worker = ReportWorker(pipeline.settings, metadata, pipeline=pipeline, automation_service=automation)
    worker._process_report(job, ZoneInfo(pipeline.settings.timezone))
    waiting = metadata.get_job(job['report_id'])
    assert waiting['status'] == 'pending' and waiting['ai_status'] == 'waiting'
    assert list(pipeline.settings.reports_dir.rglob('*.html')) == []
    automation.report_completed.assert_not_called()
    automation.report_failed.assert_not_called()
    with metadata.connect() as connection:
        connection.execute('UPDATE report_jobs SET analysis_next_attempt=0 WHERE report_id=%s', (job['report_id'],))
    pipeline.analysis_service.llm.analyze_context = model_analysis
    restarted = ReportWorker(pipeline.settings, metadata, pipeline=pipeline, automation_service=automation)
    restarted._process_report(metadata.claim_next_job(job['created_at']), ZoneInfo(pipeline.settings.timezone))
    completed = metadata.get_job(job['report_id'])
    assert completed['status'] == 'completed' and completed['ai_status'] == 'success'
    html = (pipeline.settings.reports_dir / completed['relative_path']).read_text()
    assert '本期生产记录已整理' in html and '智能分析暂不可用' not in html
    automation.report_completed.assert_called_once()
    automation.report_failed.assert_not_called()
