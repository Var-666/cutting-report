from tests.web_fixtures import cutting_request, embedded_app, replace_database_services
from tests.runtime_factories import make_device_runtime, make_pipeline_stub
from app.modules.cutting.reports import CuttingReportProvider
from app.config import Settings
from app.modules.cutting.service import QueryService
from tests.line_fixtures import source_client
from tests.line_fixtures import as_line_services, runtime_line
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from tests.line_fixtures import cutting_settings
from app.modules.cutting.analysis_routes import AnalysisRequest, analyze_report, get_analysis_version
from app.modules.cutting.leadership_routes import get_leadership_report
from app.modules.cutting.workshop_routes import list_range_tasks
from app.modules.reports.repository import MetadataRepository
from app.models.api import ReportCreateRequest
from app.models.domain import WorkshopRecord
from app.modules.cutting.pipeline import ReportPipeline
from app.modules.cutting.range_reports import previous_equal_range
from app.modules.reports.analysis.service import combined_analysis_source_version
from tests.analysis_fakes import model_analysis


def _pipeline(state_database, tmp_path: Path) -> tuple[Settings, MetadataRepository, ReportPipeline]:
    vendor_dir = tmp_path / "vendor"
    vendor_dir.mkdir()
    (vendor_dir / "echarts.min.js").write_text(
        "window.echarts={init:function(){return {setOption:function(){},resize:function(){}}}};",
        encoding="utf-8",
    )
    settings = cutting_settings(
        project_root=tmp_path,
        demo_mode=True,
        data_source_mode="demo",
        llm_enabled=True,
        app_version="test",
    )
    settings.ensure_runtime_dirs()
    metadata = MetadataRepository(state_database, device_id='Cutter01')
    pipeline = ReportPipeline(settings, metadata, query_service=QueryService(settings, client=source_client(settings)))
    pipeline.analysis_service.llm.analyze_context = model_analysis
    return settings, metadata, pipeline


def pipeline_device(pipeline):
    return make_device_runtime(settings=pipeline.settings, pipeline=pipeline,
        leadership_report_service=pipeline.leadership_service,
        report_analysis_service=pipeline.analysis_service, workshop=pipeline.workshop_repository,
        confirmations=pipeline.range_service.confirmations, report_provider=CuttingReportProvider(pipeline.leadership_service),
        leadership_refresh_worker=SimpleNamespace(is_refreshing=lambda _: False))


def test_report_request_normalizes_and_limits_inclusive_period() -> None:
    start = date(2026, 1, 1)
    payload = ReportCreateRequest(
        report_type="workshop",
        period_start=start,
        period_end=start + timedelta(days=365),
    )

    assert payload.report_date == start
    assert payload.period_start == start
    assert payload.period_end == start + timedelta(days=365)

    with pytest.raises(ValidationError, match="最长为 366 天"):
        ReportCreateRequest(
            report_type="workshop",
            period_start=start,
            period_end=start + timedelta(days=366),
        )

    with pytest.raises(ValidationError, match="最长为 31 天"):
        ReportCreateRequest(
            report_type="equipment",
            period_start=start,
            period_end=start + timedelta(days=31),
        )


@pytest.mark.parametrize(("body", "stored_period", "effective_period"), [
    ({"report_type": "leadership", "report_date": "2026-11-01"},
     (None, None), ("2026-11-01", "2026-11-30")),
    ({"report_type": "leadership", "report_date": "2026-11-01", "period_start": None, "period_end": None},
     (None, None), ("2026-11-01", "2026-11-30")),
    ({"report_type": "leadership", "report_date": "2026-11-01", "period_start": None},
     (None, None), ("2026-11-01", "2026-11-30")),
    ({"report_type": "leadership", "report_date": "2026-11-01", "period_start": None, "period_end": "2026-11-13"},
     ("2026-11-01", "2026-11-13"), ("2026-11-01", "2026-11-13")),
    ({"report_type": "leadership", "period_start": "2026-11-01", "period_end": "2026-11-13"},
     ("2026-11-01", "2026-11-13"), ("2026-11-01", "2026-11-13")),
    ({"report_type": "leadership", "period_start": "2026-11-01", "period_end": "2026-11-01"},
     ("2026-11-01", "2026-11-01"), ("2026-11-01", "2026-11-01")),
    ({"report_type": "leadership", "period_start": "2026-11-01"},
     ("2026-11-01", "2026-11-01"), ("2026-11-01", "2026-11-01")),
    ({"report_type": "leadership", "report_date": "2026-11-01", "period_end": "2026-11-13"},
     ("2026-11-01", "2026-11-13"), ("2026-11-01", "2026-11-13")),
    ({"report_type": "workshop", "report_date": "2026-11-01"},
     (None, None), ("2026-11-01", "2026-11-01")),
    ({"report_type": "workshop", "report_date": "2026-11-01", "period_start": None, "period_end": None},
     (None, None), ("2026-11-01", "2026-11-01")),
    ({"report_type": "equipment", "report_date": "2026-11-01"},
     (None, None), ("2026-11-01", "2026-11-01")),
    ({"report_type": "equipment", "report_date": "2026-11-01", "period_start": None, "period_end": None},
     (None, None), ("2026-11-01", "2026-11-01")),
])
def test_report_creation_preserves_monthly_and_explicit_range_semantics(body, stored_period, effective_period):
    from fastapi.testclient import TestClient
    from app.modules.reports import routes as reports
    from app.api.auth import require_admin
    from app.modules.reports.generation.common import _job_period

    jobs = []
    app = embedded_app()
    app.include_router(reports.router)
    app.dependency_overrides[require_admin] = lambda: {"role": "admin"}
    replace_database_services(app, lines=as_line_services({"Cutter01": make_device_runtime(
        settings=cutting_settings(),
        metadata=SimpleNamespace(create_job=lambda **job: jobs.append(job))
    )}))

    @app.middleware("http")
    async def request_context(request, call_next):
        request.state.request_id = "report-period-regression"
        return await call_next(request)

    with TestClient(app) as client:
        response = client.post("/api/v1/reports?device_id=Cutter01", json=body)
    assert response.status_code == 202, response.text
    job, = jobs
    assert (job["period_start"], job["period_end"]) == stored_period
    assert tuple(day.isoformat() for day in _job_period(job)) == effective_period


def test_report_creation_replay_is_atomic_and_scoped_to_admin_line_and_payload(state_database):
    from concurrent.futures import ThreadPoolExecutor
    from uuid import uuid4

    from app.modules.reports.routes import create_report

    def request(line):
        metadata = MetadataRepository(state_database, device_id=line)
        settings = cutting_settings(device_id=line)
        device = make_device_runtime(settings=settings, metadata=metadata)
        return cutting_request(device, request_id='idempotency-regression')

    selected = request('Cutter01')
    payload = ReportCreateRequest(report_type='leadership', report_date='2026-11-01', parameters={'chart_type': 'line'})
    key = uuid4()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: create_report(payload, selected, {'user_id': 'admin-1'}, key), range(2)))
    assert results[0] == results[1]
    initial = results[0]['data']['report_id']
    metadata = MetadataRepository(state_database, device_id='Cutter01')
    assert metadata.list_reports(page=1, page_size=10)[1] == 1
    assert metadata.get_job(initial)['status'] == 'pending'

    with pytest.raises(HTTPException) as conflict:
        create_report(payload.model_copy(update={'project_code': 'different'}), selected, {'user_id': 'admin-1'}, key)
    assert conflict.value.status_code == 409
    other = create_report(payload, selected, {'user_id': 'admin-2'}, key)['data']['report_id']
    another_line = create_report(payload, request('Cutter02'), {'user_id': 'admin-1'}, key)['data']['report_id']
    assert len({initial, other, another_line}) == 3


def test_previous_comparison_uses_an_equal_non_overlapping_period() -> None:
    assert previous_equal_range(date(2026, 8, 18), date(2026, 8, 20)) == (
        date(2026, 8, 15),
        date(2026, 8, 17),
    )


def test_workshop_top_lengths_use_filtered_rows_beyond_the_visible_page() -> None:
    records = [
        WorkshopRecord(
            task_id=f"A{index}",
            project_code="A001",
            ship_no="S001",
            segment_name="P01",
            program_no=f"OP-{index}",
            cut_length=Decimal(index),
            groove_length=None,
            work_time=Decimal("120"),
            production_time=datetime(2026, 8, 18, 12) - timedelta(minutes=index),
        )
        for index in range(1, 13)
    ]
    records.append(
        WorkshopRecord(
            task_id="B1", project_code="B001", ship_no="S002",
            segment_name="P02", program_no="OP-B1",
            cut_length=Decimal("999"), groove_length=None,
            work_time=Decimal("120"),
            production_time=datetime(2026, 8, 18, 13),
        )
    )
    device = make_device_runtime(settings=cutting_settings(),
        pipeline=make_pipeline_stub(range_service=SimpleNamespace(
            workshop_records=lambda *_: (SimpleNamespace(data_updated_at=None), records))),
        report_provider=SimpleNamespace(snapshot_service=None))
    request = cutting_request(device, request_id="workshop-top-lengths")

    data = list_range_tasks(
        request, date(2026, 8, 18), date(2026, 8, 18),
        project_name="A001", confirmation_status="all",
        page=1, page_size=10, _={},
    )["data"]

    assert data["total"] == 12
    assert "A12" not in {row["task_id"] for row in data["items"]}
    assert [row["task_id"] for row in data["top_cut_length_tasks"]] == [
        "A12", "A11", "A10", "A9", "A8"
    ]
    assert data["top_cut_length_tasks"][0]["cut_length_m"] == 12.0


def test_live_equipment_energy_range_is_bounded_to_31_days(state_database, tmp_path: Path) -> None:
    _, _, pipeline = _pipeline(state_database, tmp_path)
    with pytest.raises(ValueError, match="31 天"):
        pipeline.range_service.equipment(
            date(2026, 1, 1),
            date(2026, 2, 1),
        )


def test_equipment_analysis_reports_long_range_as_validation_error(state_database, tmp_path: Path) -> None:
    _, _, pipeline = _pipeline(state_database, tmp_path)
    request = cutting_request(pipeline_device(pipeline), request_id="analysis-range-limit")

    with pytest.raises(HTTPException) as caught:
        analyze_report(
            AnalysisRequest(
                report_type="equipment",
                report_date=date(2026, 1, 1),
                period_start=date(2026, 1, 1),
                period_end=date(2026, 2, 1),
            ),
            request,
        )

    assert caught.value.status_code == 422
    assert "31 天" in caught.value.detail


def test_analysis_api_returns_503_without_a_model_result(state_database,
    tmp_path: Path, monkeypatch
) -> None:
    _, _, pipeline = _pipeline(state_database, tmp_path)

    def fail(*_args):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(pipeline.analysis_service.llm, "analyze_context", fail)
    request = cutting_request(pipeline_device(pipeline), request_id="analysis-model-unavailable")

    with pytest.raises(HTTPException) as caught:
        analyze_report(
            AnalysisRequest(
                report_type="leadership",
                report_date=date(2026, 8, 1),
            ),
            request,
        )

    assert caught.value.status_code == 503
    assert "大模型智能分析暂不可用" in caught.value.detail
    assert caught.value.headers['Retry-After'] == '60'


def test_analysis_version_check_reads_only_sqlite_metadata() -> None:
    calls = []

    class Metadata:
        def get_analysis_source_version(self, *, report_type, report_date):
            calls.append((report_type, report_date))
            return {
                "data_version": f"{'1' * 64}.{'2' * 64}",
                "updated_at": "2026-09-29T10:00:00+08:00",
            }

    device = make_device_runtime(settings=cutting_settings(), metadata=Metadata())
    request = cutting_request(device, request_id="analysis-version")

    result = get_analysis_version("leadership", date(2026, 9, 29), request)

    assert calls == [("leadership", "2026-09")]
    assert result["data"]["data_version"] == f"{'1' * 64}.{'2' * 64}"
    assert result["data"]["updated_at"] == "2026-09-29T10:00:00+08:00"


@pytest.mark.parametrize("report_type", ["leadership", "workshop", "equipment"])
def test_analysis_rejects_a_stale_visible_report_version(state_database,
    tmp_path: Path, report_type: str
) -> None:
    _, _, pipeline = _pipeline(state_database, tmp_path)
    request = cutting_request(pipeline_device(pipeline), request_id="analysis-stale-version")

    with pytest.raises(HTTPException) as caught:
        analyze_report(
            AnalysisRequest(
                report_type=report_type,
                report_date=date(2026, 8, 1),
                period_start=date(2026, 8, 1) if report_type == "workshop" else None,
                period_end=date(2026, 8, 1) if report_type == "workshop" else None,
                data_version=combined_analysis_source_version("0" * 64),
            ),
            request,
        )

    assert caught.value.status_code == 409
    assert "刷新当前报表" in caught.value.detail


@pytest.mark.parametrize(
    ("report_type", "expected_fact_label"),
    [
        ("leadership", "区间任务数"),
        ("workshop", "任务总数"),
        ("equipment", "任务总数"),
    ],
)
def test_page_analysis_uses_the_selected_range(state_database,
    tmp_path: Path,
    report_type: str,
    expected_fact_label: str,
) -> None:
    _, _, pipeline = _pipeline(state_database, tmp_path)
    request = cutting_request(pipeline_device(pipeline), request_id="analysis-range")

    response = analyze_report(
        AnalysisRequest(
            report_type=report_type,
            report_date=date(2026, 8, 18),
            period_start=date(2026, 8, 18),
            period_end=date(2026, 8, 20),
        ),
        request,
    )

    facts = {fact["id"]: fact for fact in response["data"]["facts"]}
    from app.models.report_contracts import ReportAnalysis
    assert ReportAnalysis.model_validate(response['data']).model_dump(mode='json', exclude_unset=True) == response['data']
    assert facts["F001"]["label"] == expected_fact_label
    assert "2026-08-18 至 2026-08-20" in response["data"]["overview"]


@pytest.mark.parametrize("end_date", [date(2026, 8, 18), date(2026, 8, 20)])
def test_workshop_page_version_matches_analysis_for_same_day_and_range(state_database,
    tmp_path: Path, end_date: date
) -> None:
    _, _, pipeline = _pipeline(state_database, tmp_path)
    request = cutting_request(pipeline_device(pipeline), request_id="workshop-visible-version")
    start_date = date(2026, 8, 18)
    page = list_range_tasks(
        request,
        start_date,
        end_date,
        project_name=None,
        confirmation_status="all",
        page=1,
        page_size=10,
        _={},
    )
    result = analyze_report(
        AnalysisRequest(
            report_type="workshop",
            report_date=start_date,
            period_start=start_date,
            period_end=end_date,
            data_version=combined_analysis_source_version(
                page["data"]["analysis_source_version"]
            ),
        ),
        request,
    )
    assert result["data"]["data_version"] == combined_analysis_source_version(
        page["data"]["analysis_source_version"]
    )


def test_leadership_page_versions_cover_both_analysis_tables(state_database, tmp_path: Path) -> None:
    _, _, pipeline = _pipeline(state_database, tmp_path)
    request = cutting_request(pipeline_device(pipeline), request_id="leadership-visible-version")
    page = get_leadership_report(
        request,
        report_month="2026-08",
        start_date=None,
        end_date=None,
        refresh=False,
    )["data"]
    visible_version = combined_analysis_source_version(
        page["analysis_source_version"],
        page["analysis_summary_source_version"],
    )

    result = analyze_report(
        AnalysisRequest(
            report_type="leadership",
            report_date=date(2026, 8, 1),
            summary_period_start=date(2025, 9, 1),
            summary_period_end=date(2026, 8, 1),
            data_version=visible_version,
        ),
        request,
    )

    assert result["data"]["data_version"] == visible_version


@pytest.mark.parametrize(
    ("report_type", "expected_heading", "project_code", "expected_tasks"),
    [
        ("leadership", "区间每日明细与趋势", None, 42),
        ("workshop", "车间生产明细与趋势", "A002", 21),
        ("equipment", "区间任务能耗明细", None, 42),
    ],
)
def test_pipeline_exports_the_selected_range(state_database,
    tmp_path: Path,
    report_type: str,
    expected_heading: str,
    project_code: str | None,
    expected_tasks: int,
) -> None:
    settings, metadata, pipeline = _pipeline(state_database, tmp_path)
    now = datetime.now(ZoneInfo(settings.timezone)).isoformat(timespec="seconds")
    metadata.create_job(
        report_id="rpt_range",
        report_date="2026-08-18",
        period_start="2026-08-18",
        period_end="2026-08-20",
        project_code=project_code,
        ship_no=None,
        request_id="req_range",
        created_at=now,
        report_type=report_type,
        parameters={"chart_type": "bar_line"},
    )

    job = metadata.claim_next_job(now)
    assert job is not None
    pipeline.run(job)

    completed = metadata.get_job("rpt_range")
    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["period_start"] == "2026-08-18"
    assert completed["period_end"] == "2026-08-20"
    assert completed["task_count"] == expected_tasks
    assert "2026-08-18_至_2026-08-20" in completed["filename"]
    html = (settings.reports_dir / completed["relative_path"]).read_text(
        encoding="utf-8"
    )
    assert "2026-08-18 至 2026-08-20" in html
    assert expected_heading in html
    if report_type == "workshop":
        assert ">工人工时汇总</h2>" not in html
        assert "所选区间暂无工人确认数据" not in html


@pytest.mark.parametrize('committed', [False, True])
def test_archive_is_removed_only_after_confirmed_rollback(state_database, tmp_path, monkeypatch, committed):
    from app.database.unit_of_work import UnitOfWork

    settings, metadata, pipeline = _pipeline(state_database, tmp_path)
    now = datetime.now(ZoneInfo(settings.timezone))
    metadata.create_job(report_id='archive-commit', report_type='leadership', report_date=now.date().isoformat(),
                        project_code=None, ship_no=None, request_id='archive', created_at=now.isoformat())
    job = metadata.claim_next_job(now.isoformat())
    archive = settings.reports_dir / 'archive-commit.html'

    def render(**_kwargs):
        archive.write_text('<p>报告结束</p>', encoding='utf-8')
        return dict(relative_path=archive.name, filename=archive.name, sha256='test-digest', file_size=archive.stat().st_size)

    original_exit = UnitOfWork.__exit__
    def finish(self, *_args):
        error = OSError('database commit reply lost')
        original_exit(self, None, None, None) if committed else original_exit(self, type(error), error, None)
        raise error

    monkeypatch.setattr(pipeline.report_service, 'render', render)
    monkeypatch.setattr(UnitOfWork, '__exit__', finish)
    def publish():
        pipeline._render_and_complete(job=job, report={}, generated_at=now, task_count=1, analysis_status='success')
    if committed:
        publish()
        assert metadata.get_job(job['report_id'])['status'] == 'completed' and archive.exists()
    else:
        with pytest.raises(OSError, match='commit reply'):
            publish()
        assert metadata.get_job(job['report_id'])['relative_path'] is None and not archive.exists()


def test_workshop_task_link_filters_real_id_within_original_period(state_database, tmp_path):
    settings, metadata, pipeline = _pipeline(state_database, tmp_path)
    _, records = pipeline.range_service.workshop_records(date(2026, 8, 18), date(2026, 8, 20))
    assert records
    from app.modules.cutting.workshop import WorkshopService
    service = WorkshopService(None, None, settings, range_report_service=pipeline.range_service)
    query = dict(start_date=date(2026, 8, 18), end_date=date(2026, 8, 20), project_name=None,
        confirmation_status='all', page=1, page_size=10, task_id=records[0].task_id)
    linked = service.list_range_tasks(**query)
    assert linked['total'] == 1
    assert linked['items'][0]['task_id'] == records[0].task_id
    assert service.list_range_tasks(**(query | {'task_id': 'not-in-scope'}))['total'] == 0
    assert service.list_range_tasks(**(query | {'start_date': date(2026, 8, 21), 'end_date': date(2026, 8, 22)}))['total'] == 0


@pytest.mark.parametrize('kind', ['leadership', 'workshop', 'equipment'])
def test_html_export_displays_dynamic_brief_and_keeps_backend_evidence(state_database, tmp_path, kind):
    from app.models.domain import AnalysisResult
    from app.modules.reports.analysis.brief_validation import validate_brief
    settings, metadata, pipeline = _pipeline(state_database, tmp_path)
    def model(report_type, context):
        candidate = context['candidates'][0]
        brief = validate_brief(dict(headline='测试数据生产简报', summary='根据本期真实统计口径解释生产记录。',
            key_metric_ids=candidate['evidence_ids'][:2], findings=[dict(candidate_id=candidate['id'], title='测试发现', detail='关联记录可供核对。',
                evidence_ids=candidate['evidence_ids'])], actions=[]), context)
        return AnalysisResult(status='success', overview=brief['summary'], features='', alerts='', suggestions='', brief=brief)
    pipeline.analysis_service.llm.analyze_context = model
    now = datetime.now(ZoneInfo(settings.timezone)).isoformat(timespec='seconds')
    metadata.create_job(report_id='brief-export', report_type=kind, report_date='2026-08-18',
        period_start='2026-08-18', period_end='2026-08-20', project_code=None, ship_no=None,
        request_id='brief-export-test', created_at=now, parameters={'chart_type': 'bar_line'})
    pipeline.run(metadata.claim_next_job(now))
    saved = metadata.get_job('brief-export')
    assert saved['status'] == 'completed', saved
    html = (settings.reports_dir / saved['relative_path']).read_text()
    assert '测试数据生产简报' in html and '测试发现' in html
    assert '分析依据' in html
    assert 'analysis-card overview' not in html
    assert '<h3>建议跟进</h3>' not in html
