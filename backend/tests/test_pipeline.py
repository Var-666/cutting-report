from datetime import date, datetime
from copy import deepcopy
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from openpyxl import Workbook

from app.config import Settings
from app.database.metadata import MetadataRepository
from app.database.workshop import WorkshopRepository
from app.models.domain import ReportFilter
from app.services.pipeline import ReportPipeline
from tests.analysis_fakes import model_analysis


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
        llm_enabled=True,
        app_version="test",
    )
    test_settings.ensure_runtime_dirs()
    metadata = MetadataRepository(test_settings.metadata_db_path)
    metadata.initialize()
    pipeline = ReportPipeline(test_settings, metadata)
    pipeline.analysis_service.llm.analyze_context = model_analysis
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
    assert "演示数据 · 非生产统计" in html
    assert completed["filename"].startswith("演示数据_")
    assert expected_title in html
    assert expected_chart in html
    assert "报告结束" in html
    assert "window.echarts" in html
    assert "@page { size:A4 landscape" in html
    assert "window.__REPORT_READY__ = true" in html
    assert "report-printing" in html
    assert 'class="report-cover"' in html
    assert 'class="cover-folio"' in html
    assert 'class="report-footer"' in html
    assert "--navy:#123b57" in html
    assert "background:#dceaf0" in html
    assert "tbody tr:nth-child(even)" not in html
    assert '"areaStyle"' in html
    assert "大模型分析" in html
    assert "模拟大模型分析" not in html
    assert "分析边界" not in html
    assert html.count('class="analysis-fact"') == 6
    assert 'class="analysis-card alerts"' in html
    assert 'class="analysis-card suggestions"' in html
    assert "2026年8月18日" in html
    if report_type in {"workshop", "equipment"}:
        assert 'class="screen-only"' in html
        assert 'class="print-table-page"' in html
        assert 'class="print-table-chunk"' in html
    if report_type == "workshop":
        assert "工程号" in html
        assert "船号" in html
        assert "✓ 测试工人" in html
        assert "时间" in html
        assert "待确认" in html
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
        assert "当前关注项需结合现场记录核对" in html
    assert completed["task_count"] > 0


def test_pipeline_completes_deterministic_report_when_ai_is_unavailable(tmp_path: Path) -> None:
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
    metadata.create_job(
        report_id="rpt_ai_unavailable",
        report_date="2026-08-18",
        project_code=None,
        ship_no=None,
        request_id="req_ai_unavailable",
        created_at=now,
        report_type="leadership",
    )
    job = metadata.claim_next_job(now)
    assert job is not None

    pipeline.run(job)

    completed = metadata.get_job("rpt_ai_unavailable")
    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["ai_status"] == "unavailable"
    html = (test_settings.reports_dir / completed["relative_path"]).read_text(
        encoding="utf-8"
    )
    assert "智能分析暂不可用" in html
    assert "确定性报表已生成" in html


@pytest.mark.parametrize("report_type", ["leadership", "equipment"])
def test_range_export_keeps_the_recent_twelve_month_summary(
    tmp_path: Path,
    report_type: str,
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
        llm_enabled=True,
        app_version="test",
    )
    test_settings.ensure_runtime_dirs()
    metadata = MetadataRepository(test_settings.metadata_db_path)
    metadata.initialize()
    pipeline = ReportPipeline(test_settings, metadata)
    pipeline.analysis_service.llm.analyze_context = model_analysis
    now = datetime.now(ZoneInfo(test_settings.timezone)).isoformat(timespec="seconds")
    metadata.create_job(
        report_id=f"rpt_range_{report_type}",
        report_date="2026-08-17",
        period_start="2026-08-17",
        period_end="2026-08-18",
        project_code=None,
        ship_no=None,
        request_id="req_range",
        created_at=now,
        report_type=report_type,
        parameters={
            "chart_type": "bar_line",
            "summary_period_start": "2025-09-01",
            "summary_period_end": "2026-08-18",
        },
    )
    job = metadata.claim_next_job(now)
    assert job is not None

    pipeline.run(job)

    completed = metadata.get_job(f"rpt_range_{report_type}")
    assert completed is not None
    assert completed["status"] == "completed"
    html = (test_settings.reports_dir / completed["relative_path"]).read_text(
        encoding="utf-8"
    )
    assert "2026-08-17 至 2026-08-18" in html
    assert "2025年9月" in html
    if report_type == "leadership":
        assert "近 12 个月按月对比与趋势" in html
    else:
        assert "近 12 个月月度能耗汇总" in html
        assert "<th>序号</th><th>月份</th>" in html
        assert "<td>1</td><td>2025年9月</td>" in html


@pytest.fixture
def report_flow(tmp_path, monkeypatch):
    """Use real Excel queries and HTML output to protect period/format semantics."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "TASK"
    sheet.append(["TASK_ID", "PROJECT_NAME", "MACHINE_NO", "STATUS", "OP_DATE",
                  "START_TIME", "END_TIME", "WORK_TIME", "OP_LENGTH_CUT", "OP_LENGTH_GROOVE",
                  "OP_PROGRAM", "PROJECT_CODE", "REMARK", "SEGMENT_NAME"])
    for task_id, stamp, machine, status in (
        (1, date(2023, 2, 28), "Cutter01", 2),
        (2, date(2024, 1, 31), "Cutter01", 2),
        (3, date(2024, 2, 1), "Cutter01", 2),
        (4, date(2024, 2, 29), "Cutter01", 2),
        (5, date(2024, 3, 1), "Cutter01", 2),
        (6, date(2024, 2, 29), "Cutter02", 2),
        (7, date(2024, 2, 29), "Cutter01", 1),
    ):
        # The Feb 1 task started the previous day; OP_DATE owns the report day.
        start = datetime(2024, 1, 31, 23, 50) if task_id == 3 else datetime.combine(stamp, datetime.min.time())
        end = datetime.combine(stamp, datetime.min.time()).replace(hour=1)
        sheet.append([task_id, "PJT-A", machine, status, stamp.isoformat(), start, end, 3600, 1200, 200,
                      "P-01", None, None, "S-01"])
    workbook.save(tmp_path / "TASK.xlsx")
    workbook.close()
    vendor_dir = tmp_path / "vendor"
    vendor_dir.mkdir()
    (vendor_dir / "echarts.min.js").write_text("window.echarts={};")
    settings = Settings(project_root=tmp_path, data_source_mode="excel", demo_mode=False,
                        task_excel_file="TASK.xlsx", report_machine_no="Cutter01",
                        energy_report_equipment_ids=("Cutter01",), energy_api_enabled=False,
                        llm_enabled=True, app_version="test")
    settings.ensure_runtime_dirs()
    metadata = MetadataRepository(settings.metadata_db_path)
    metadata.initialize()
    pipeline = ReportPipeline(settings, metadata)
    pipeline.analysis_service.llm.analyze_context = model_analysis
    reports, analyses = {}, []
    original_render = pipeline.report_service.render

    def render(*, report_id, report, **kwargs):
        reports[report_id] = deepcopy(report)
        return original_render(report_id=report_id, report=report, **kwargs)

    monkeypatch.setattr(pipeline.report_service, "render", render)
    for method in ("analyze_equipment", "analyze_leadership"):
        original = getattr(pipeline.analysis_service, method)

        def analyze(*args, _original=original, **kwargs):
            analyses.append((args, kwargs))
            return _original(*args, **kwargs)

        monkeypatch.setattr(pipeline.analysis_service, method, analyze)

    def run(report_id, report_type, report_date="2024-02-29", **kwargs):
        now = datetime.now(ZoneInfo(settings.timezone)).isoformat(timespec="seconds")
        metadata.create_job(report_id=report_id, report_type=report_type, report_date=report_date,
                            project_code=None, ship_no=None, request_id=report_id, created_at=now, **kwargs)
        job = metadata.claim_next_job(now)
        assert job is not None
        pipeline.run(job)
        completed = metadata.get_job(report_id)
        assert completed["status"] == "completed"
        assert completed["ai_status"] == "success"
        html = (settings.reports_dir / completed["relative_path"]).read_text()
        assert "报告结束" in html
        return reports[report_id], html

    return pipeline, run, analyses


def test_equipment_daily_and_single_day_range_share_facts_but_keep_summary_formats(report_flow, monkeypatch):
    pipeline, run, analyses = report_flow
    queries = []
    original = pipeline.query_service.fetch_equipment_data

    def fetch(filters, **kwargs):
        queries.append((filters.start_date, filters.end_date))
        return original(filters, **kwargs)

    monkeypatch.setattr(pipeline.query_service, "fetch_equipment_data", fetch)
    daily, daily_html = run("daily", "equipment", parameters={"report_month": "2024-01"})
    interval, interval_html = run("interval", "equipment", period_start="2024-02-29", period_end="2024-02-29")
    for key in ("equipment_records", "equipment_summary", "units", "energy_notice"):
        assert daily[key] == interval[key]
    assert daily["charts"]["equipment_detail"] == interval["charts"]["equipment_detail"]
    assert [row["task_id"] for row in daily["equipment_records"]] == ["4"]
    assert queries.count((date(2024, 2, 29), date(2024, 2, 29))) == 2  # one primary query per report
    assert daily["filters"]["period_start"] == daily["filters"]["period_end"] == "2024-02-29"
    assert daily["energy_month"]["report_month"] == "2024-01"
    assert len(daily["energy_month"]["rows"]) == 31
    assert len(interval["energy_month"]["rows"]) == 12
    assert interval["energy_month"]["period_start"] == "2023-03-01"
    assert interval["energy_month"]["period_end"] == "2024-02-29"
    assert "月度每日能耗汇总" in daily_html
    assert "近 12 个月月度能耗汇总" in interval_html
    assert analyses[0][1]["comparison_date"] == "2024-02-28"
    assert analyses[0][1]["year_ago_date"] == "2023-02-28"
    assert analyses[1][1]["comparison_date"] == "2024-02-28"
    assert analyses[1][1]["year_ago_date"] is None


def test_leadership_month_and_full_month_range_keep_calendar_and_comparison_semantics(report_flow):
    _, run, analyses = report_flow
    monthly, monthly_html = run("monthly", "leadership", report_date="2024-02-15")
    interval, _ = run("interval", "leadership", period_start="2024-02-01", period_end="2024-02-29")
    for report in (monthly, interval):
        assert report["meta"]["period_start"] == report["filters"]["period_start"] == "2024-02-01"
        assert report["meta"]["period_end"] == report["filters"]["period_end"] == "2024-02-29"
        assert report["leadership"]["period_start"] == "2024-02-01"
        assert report["leadership"]["period_end"] == "2024-02-29"
        assert report["leadership"]["current_month_total"]["task_count"] == 2
        assert report["leadership"]["current_month_total"]["cut_length_m"] == 2.4
        assert [row["period_start"] for row in report["leadership"]["daily_records"]] == ["2024-02-29", "2024-02-01"]
    assert monthly["leadership"]["monthly_records"] == interval["leadership"]["monthly_records"]
    assert len(monthly["leadership"]["monthly_records"]) == 12
    assert "当月明细与趋势" in monthly_html
    legacy_context = analyses[0][0][0]
    assert legacy_context.period_start is None  # retain the monthly AI label/cache key
    assert legacy_context.comparison_start == date(2024, 1, 1)
    assert legacy_context.comparison_end == date(2024, 1, 31)
    assert legacy_context.year_ago_total.task_count == 1
    range_context = analyses[1][0][0]
    assert range_context.comparison_start == date(2024, 1, 3)
    assert range_context.comparison_end == date(2024, 1, 31)


@pytest.mark.parametrize(("selected", "last_day"), [
    ("2023-02-15", "2023-02-28"), ("2024-02-15", "2024-02-29"),
    ("2024-04-15", "2024-04-30"), ("2024-12-15", "2024-12-31"),
])
def test_legacy_leadership_month_has_one_consistent_calendar_period(report_flow, selected, last_day):
    _, run, _ = report_flow
    report, _ = run("calendar", "leadership", report_date=selected)
    first_day = selected[:8] + "01"
    for section in ("meta", "filters", "leadership"):
        assert report[section]["period_start"] == first_day
        assert report[section]["period_end"] == last_day


def test_invalid_legacy_equipment_month_is_rejected_before_source_queries(report_flow, monkeypatch):
    pipeline, run, _ = report_flow

    def unexpected_query(*args, **kwargs):
        pytest.fail("An invalid summary month must not start external queries")

    monkeypatch.setattr(pipeline.query_service, "fetch_equipment_data", unexpected_query)
    with pytest.raises(ValueError, match="导出月份无效"):
        run("invalid-month", "equipment", parameters={"report_month": "2024-13"})
    assert list(pipeline.settings.reports_dir.rglob("*.html")) == []


@pytest.mark.parametrize("interval", [False, True])
def test_required_equipment_summary_failure_prevents_both_formats_from_publishing(report_flow, monkeypatch, interval):
    pipeline, run, _ = report_flow

    def failed_summary(*args):
        raise OSError("summary unavailable")

    method = "get_monthly_range" if interval else "get_month"
    monkeypatch.setattr(pipeline.energy_period_service, method, failed_summary)
    period = {"period_start": "2024-02-29", "period_end": "2024-02-29"} if interval else {}
    with pytest.raises(OSError, match="summary unavailable"):
        run("failed-summary", "equipment", **period)
    assert pipeline.metadata.get_job("failed-summary")["status"] != "completed"
    assert list(pipeline.settings.reports_dir.rglob("*.html")) == []
