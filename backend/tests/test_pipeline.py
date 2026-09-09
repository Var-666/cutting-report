from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.config import Settings
from app.database.metadata import MetadataRepository
from app.database.workshop import WorkshopRepository
from app.models.domain import ReportFilter
from app.services.pipeline import ReportPipeline


@pytest.mark.parametrize(
    ("report_type", "expected_title", "expected_chart"),
    [
        ("leadership", "切割生产领导月报", "leadership-daily-chart"),
        ("workshop", "切割生产车间日报", "length-chart"),
        ("equipment", "切割生产设备报表", "equipment-detail-chart"),
    ],
)
def test_demo_pipeline_generates_offline_report(
    tmp_path: Path,
    report_type: str,
    expected_title: str,
    expected_chart: str,
) -> None:
    vendor_dir = tmp_path / "vendor"
    vendor_dir.mkdir()
    (vendor_dir / "echarts.min.js").write_text(
        "window.echarts={init:function(){return {setOption:function(){},resize:function(){}}}};",
        encoding="utf-8",
    )
    test_settings = Settings(
        project_root=tmp_path,
        demo_mode=True,
        data_source_mode="demo",
        llm_enabled=False,
        app_version="test",
    )
    test_settings.ensure_runtime_dirs()
    metadata = MetadataRepository(test_settings.metadata_db_path)
    metadata.initialize()
    pipeline = ReportPipeline(test_settings, metadata)
    now = datetime.now(ZoneInfo(test_settings.timezone)).isoformat(timespec="seconds")
    if report_type == "workshop":
        workshop = WorkshopRepository(test_settings.metadata_db_path)
        workshop.initialize()
        user = workshop.bootstrap_admin(
            username="tester",
            display_name="测试工人",
            password="safe-password",
            now=now,
        )
        bundle = pipeline.query_service.fetch_report_data(
            ReportFilter(report_date=date(2026, 8, 18))
        )
        daily_report = workshop.create_daily_report(
            report_date="2026-08-18",
            source_mode="demo",
            records=bundle.workshop_records,
            user=user,
            now=now,
        )
        workshop.confirm_task(
            task_id="20260818-001",
            report_id=daily_report["report_id"],
            user=user,
            now=now,
        )
    metadata.create_job(
        report_id="rpt_test",
        report_date="2026-08-18",
        project_code="A001",
        ship_no=None,
        request_id="req_test",
        created_at=now,
        report_type=report_type,
        parameters={"chart_type": "area"},
    )
    job = metadata.claim_next_job(now)
    assert job is not None

    pipeline.run(job)

    completed = metadata.get_job("rpt_test")
    assert completed is not None
    assert completed["status"] == "completed"
    report_path = test_settings.reports_dir / completed["relative_path"]
    html = report_path.read_text(encoding="utf-8")
    assert expected_title in html
    assert expected_chart in html
    assert "报告结束" in html
    assert "window.echarts" in html
    assert "@page { size:A4 landscape" in html
    assert "window.__REPORT_READY__ = true" in html
    assert "report-printing" in html
    assert '"areaStyle"' in html
    assert "模拟大模型分析" in html
    assert "分析边界" not in html
    assert html.count('class="analysis-fact"') == 6
    if report_type in {"workshop", "equipment"}:
        assert 'class="screen-only"' in html
        assert 'class="print-table-page"' in html
        assert 'class="print-table-chunk"' in html
    if report_type == "workshop":
        assert "工程号" in html
        assert "船号" in html
        assert "✓ 测试工人" in html
        assert "时间" in html
        assert "人" in html
    if report_type == "equipment":
        assert "任务开始" in html
        assert "任务结束" not in html
        assert "数据状态" not in html
        assert "完整全天" not in html
        assert "采样规则切换" not in html
        assert "边界非采样时刻" not in html
        assert "气耗" in html
        assert "电耗" in html
        assert "月度每日能耗汇总" in html
        assert "equipment-month-chart" in html
        assert "≈" not in html
        assert "稳定模拟数据" not in html
        assert "CUT-01" not in html
        assert "设备数量" not in html
        assert "切割长度</small>" not in html
        assert "不进行能耗异常判定" in html
    assert completed["task_count"] > 0
