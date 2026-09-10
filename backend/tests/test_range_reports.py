from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.config import Settings
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


def test_previous_comparison_uses_an_equal_non_overlapping_period() -> None:
    assert previous_equal_range(date(2026, 8, 18), date(2026, 8, 20)) == (
        date(2026, 8, 15),
        date(2026, 8, 17),
    )


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

