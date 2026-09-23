from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.config import Settings
from app.api.analysis import AnalysisRequest, analyze_report
from app.database.metadata import MetadataRepository
from app.models.api import ReportCreateRequest
from app.services.pipeline import ReportPipeline
from app.services.range_report_service import previous_equal_range


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
        llm_enabled=False,
        app_version="test",
    )
    settings.ensure_runtime_dirs()
    metadata = MetadataRepository(settings.metadata_db_path)
    metadata.initialize()
    return settings, metadata, ReportPipeline(settings, metadata)


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
                data_version="0" * 64,
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
