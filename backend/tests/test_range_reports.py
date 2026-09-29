from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.config import Settings
from app.api.analysis import AnalysisRequest, analyze_report, get_analysis_version
from app.api.leadership import get_leadership_report
from app.api.workshop import list_range_tasks
from app.database.metadata import MetadataRepository
from app.models.api import ReportCreateRequest
from app.models.domain import WorkshopRecord
from app.services.pipeline import ReportPipeline
from app.services.range_report_service import previous_equal_range
from app.services.report_analysis_service import combined_analysis_source_version
from tests.analysis_fakes import model_analysis


def _pipeline(tmp_path: Path) -> tuple[Settings, MetadataRepository, ReportPipeline]:
    vendor_dir = tmp_path / "vendor"
    vendor_dir.mkdir()
    (vendor_dir / "echarts.min.js").write_text(
        "window.echarts={init:function(){return {setOption:function(){},resize:function(){}}}};",
        encoding="utf-8",
    )
    settings = Settings(
        project_root=tmp_path,
        demo_mode=True,
        data_source_mode="demo",
        llm_enabled=True,
        app_version="test",
    )
    settings.ensure_runtime_dirs()
    metadata = MetadataRepository(settings.metadata_db_path)
    metadata.initialize()
    pipeline = ReportPipeline(settings, metadata)
    pipeline.analysis_service.llm.analyze_context = model_analysis
    return settings, metadata, pipeline


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
    request = SimpleNamespace(
        state=SimpleNamespace(request_id="workshop-top-lengths"),
        app=SimpleNamespace(state=SimpleNamespace(
            range_report_service=SimpleNamespace(
                workshop_records=lambda *_: (SimpleNamespace(data_updated_at=None), records)
            )
        )),
    )

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


def test_live_equipment_energy_range_is_bounded_to_31_days(tmp_path: Path) -> None:
    _, _, pipeline = _pipeline(tmp_path)
    with pytest.raises(ValueError, match="31 天"):
        pipeline.range_service.equipment(
            date(2026, 1, 1),
            date(2026, 2, 1),
        )


def test_equipment_analysis_reports_long_range_as_validation_error(tmp_path: Path) -> None:
    _, _, pipeline = _pipeline(tmp_path)
    request = SimpleNamespace(
        state=SimpleNamespace(request_id="analysis-range-limit"),
        app=SimpleNamespace(
            state=SimpleNamespace(
                report_analysis_service=pipeline.analysis_service,
                range_report_service=pipeline.range_service,
                leadership_report_service=pipeline.leadership_service,
                workshop=pipeline.workshop_repository,
                query_service=pipeline.query_service,
                energy_period_service=pipeline.energy_period_service,
            )
        ),
    )

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


def test_analysis_api_returns_503_without_a_model_result(
    tmp_path: Path, monkeypatch
) -> None:
    _, _, pipeline = _pipeline(tmp_path)

    def fail(*_args):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(pipeline.analysis_service.llm, "analyze_context", fail)
    request = SimpleNamespace(
        state=SimpleNamespace(request_id="analysis-model-unavailable"),
        app=SimpleNamespace(
            state=SimpleNamespace(
                report_analysis_service=pipeline.analysis_service,
                leadership_report_service=pipeline.leadership_service,
            )
        ),
    )

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


def test_analysis_version_check_reads_only_sqlite_metadata() -> None:
    calls = []

    class Metadata:
        def get_analysis_source_version(self, *, report_type, report_date):
            calls.append((report_type, report_date))
            return {
                "data_version": f"{'1' * 64}.{'2' * 64}",
                "updated_at": "2026-09-29T10:00:00+08:00",
            }

    request = SimpleNamespace(
        state=SimpleNamespace(request_id="analysis-version"),
        app=SimpleNamespace(state=SimpleNamespace(metadata=Metadata())),
    )

    result = get_analysis_version("leadership", date(2026, 9, 29), request)

    assert calls == [("leadership", "2026-09")]
    assert result["data"]["data_version"] == f"{'1' * 64}.{'2' * 64}"
    assert result["data"]["updated_at"] == "2026-09-29T10:00:00+08:00"


@pytest.mark.parametrize("report_type", ["leadership", "workshop", "equipment"])
def test_analysis_rejects_a_stale_visible_report_version(
    tmp_path: Path, report_type: str
) -> None:
    _, _, pipeline = _pipeline(tmp_path)
    request = SimpleNamespace(
        state=SimpleNamespace(request_id="analysis-stale-version"),
        app=SimpleNamespace(
            state=SimpleNamespace(
                report_analysis_service=pipeline.analysis_service,
                range_report_service=pipeline.range_service,
                leadership_report_service=pipeline.leadership_service,
                workshop=pipeline.workshop_repository,
                query_service=pipeline.query_service,
                energy_period_service=pipeline.energy_period_service,
            )
        ),
    )

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
def test_page_analysis_uses_the_selected_range(
    tmp_path: Path,
    report_type: str,
    expected_fact_label: str,
) -> None:
    _, _, pipeline = _pipeline(tmp_path)
    request = SimpleNamespace(
        state=SimpleNamespace(request_id="analysis-range"),
        app=SimpleNamespace(
            state=SimpleNamespace(
                report_analysis_service=pipeline.analysis_service,
                range_report_service=pipeline.range_service,
                leadership_report_service=pipeline.leadership_service,
                workshop=pipeline.workshop_repository,
                query_service=pipeline.query_service,
                energy_period_service=pipeline.energy_period_service,
            )
        ),
    )

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
    assert facts["F001"]["label"] == expected_fact_label
    assert "2026-08-18 至 2026-08-20" in response["data"]["overview"]


@pytest.mark.parametrize("end_date", [date(2026, 8, 18), date(2026, 8, 20)])
def test_workshop_page_version_matches_analysis_for_same_day_and_range(
    tmp_path: Path, end_date: date
) -> None:
    _, _, pipeline = _pipeline(tmp_path)
    request = SimpleNamespace(
        state=SimpleNamespace(request_id="workshop-visible-version"),
        app=SimpleNamespace(
            state=SimpleNamespace(
                range_report_service=pipeline.range_service,
                report_analysis_service=pipeline.analysis_service,
                workshop=pipeline.workshop_repository,
                query_service=pipeline.query_service,
            )
        ),
    )
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


def test_leadership_page_versions_cover_both_analysis_tables(tmp_path: Path) -> None:
    _, _, pipeline = _pipeline(tmp_path)
    request = SimpleNamespace(
        state=SimpleNamespace(request_id="leadership-visible-version"),
        app=SimpleNamespace(
            state=SimpleNamespace(
                leadership_report_service=pipeline.leadership_service,
                range_report_service=pipeline.range_service,
                report_analysis_service=pipeline.analysis_service,
            )
        ),
    )
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
        ("workshop", "车间生产明细与图表", "A002", 21),
        ("equipment", "区间任务能耗明细", None, 42),
    ],
)
def test_pipeline_exports_the_selected_range(
    tmp_path: Path,
    report_type: str,
    expected_heading: str,
    project_code: str | None,
    expected_tasks: int,
) -> None:
    settings, metadata, pipeline = _pipeline(tmp_path)
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
        assert "工人工时汇总" not in html
        assert "所选区间暂无工人确认数据" not in html
