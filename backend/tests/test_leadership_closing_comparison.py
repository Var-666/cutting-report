from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.models.domain import AnalysisResult
from app.services.chart_service import ChartService
from app.services.energy_period_service import EnergyPeriodService
from app.services.leadership_report_service import LeadershipReportService
from app.services.query_service import QueryService
from app.services.range_report_service import RangeReportService
from app.services.report_closing_comparison import closing_comparison
from app.services.report_generation.leadership import prepare_report
from app.services.report_generation.equipment import prepare_report as prepare_equipment
from app.services.report_generation.workshop import prepare_report as prepare_workshop
from app.services.report_service import ReportService
from app.services.statistics_service import StatisticsService
from tests.line_fixtures import cutting_settings


ZONE = ZoneInfo("Asia/Shanghai")


def test_week_and_month_exclude_the_previous_calendar_evening(tmp_path, monkeypatch):
    cfg = cutting_settings(project_root=tmp_path, data_source_mode="sqlserver")
    query = QueryService(cfg)
    stamps = ["2026-10-31 23:59:59", "2026-11-01 00:00:00", "2026-11-15 23:59:59",
              "2026-11-16 00:00:00", "2026-11-17 16:59:59", "2026-11-17 17:00:00",
              "2026-11-18 16:59:59", "2026-11-18 17:00:00"]
    monkeypatch.setattr(query, "fetch_summary_data", lambda *args, **kwargs: SimpleNamespace(summary_records=[
        SimpleNamespace(source_updated_at=stamp, cut_length=10, groove_length=1, work_time=60) for stamp in stamps
    ]))
    columns = RangeReportService(cfg, query, None, None).cutting_closing_comparison(
        date(2026, 11, 18), date(2026, 11, 18), datetime(2026, 11, 18, 18, tzinfo=ZONE), project_code="P1"
    )
    today, week, month = columns
    assert [column["totals"]["task_count"] for column in columns] == [2, 4, 6]
    assert [column["totals"]["cut_length_m"] for column in columns] == [20, 40, 60]
    assert today["start"] == datetime(2026, 11, 17, 17, tzinfo=ZONE)
    assert week["start"] == datetime(2026, 11, 16, tzinfo=ZONE)
    assert month["start"] == datetime(2026, 11, 1, tzinfo=ZONE)
    assert week["comparison_start"] == datetime(2026, 11, 9, tzinfo=ZONE)
    assert month["comparison_start"] == datetime(2026, 10, 1, tzinfo=ZONE)
    assert week["comparison_end"] == datetime(2026, 11, 11, 17, tzinfo=ZONE)
    assert month["comparison_end"] == datetime(2026, 10, 18, 17, tzinfo=ZONE)


def test_cutting_cutoff_uses_actual_end_time_and_half_open_intervals(tmp_path, monkeypatch):
    cfg = cutting_settings(project_root=tmp_path)
    query = QueryService(cfg)
    ranges = RangeReportService(cfg, query, None, None)
    ends = [
        datetime(2026, 11, 16, 16, 59, 59, tzinfo=ZONE),
        datetime(2026, 11, 16, 17, 0, tzinfo=ZONE),
        datetime(2026, 11, 17, 16, 59, 59, tzinfo=ZONE),
        datetime(2026, 11, 17, 17, 0, tzinfo=ZONE),
        None,
    ]
    bundle = SimpleNamespace(summary_records=[
        SimpleNamespace(production_time=end - timedelta(minutes=1) if end else None,
                        cut_length=20, groove_length=0, work_time=60) for end in ends
    ], data_updated_at=None)
    monkeypatch.setattr(query, "_demo_data", lambda _: bundle)

    columns = ranges.cutting_closing_comparison(
        date(2026, 11, 1), date(2026, 11, 30), datetime.fromisoformat("2026-11-17T09:00:00+00:00")
    )
    today, week, month = columns
    assert [column["label"] for column in columns] == ["今日", "本周累计", "本月累计"]
    assert today["totals"] == dict(task_count=2, cut_length_m=40, groove_length_m=0, work_time_seconds=120)
    assert today["changes"]["task_count"] == "+100.0%"
    assert week["totals"]["task_count"] == month["totals"]["task_count"] == 3
    assert week["start"] == datetime(2026, 11, 16, 0, tzinfo=ZONE)
    assert week["comparison_start"] == datetime(2026, 11, 9, 0, tzinfo=ZONE)
    assert month["start"] == datetime(2026, 11, 1, 0, tzinfo=ZONE)
    assert month["comparison_start"] == datetime(2026, 10, 1, 0, tzinfo=ZONE)
    assert week["comparison_end"] == datetime(2026, 11, 10, 17, tzinfo=ZONE)
    assert month["comparison_end"] == datetime(2026, 10, 17, 17, tzinfo=ZONE)
    assert today["end"] == datetime(2026, 11, 17, 17, tzinfo=ZONE)
    before_cutoff = datetime(2026, 11, 17, 16, 59, tzinfo=ZONE)
    assert ranges.cutting_closing_comparison(before_cutoff.date(), before_cutoff.date(), before_cutoff) == []
    yesterday = ranges.cutting_closing_comparison(date(2026, 11, 1), date(2026, 11, 30), before_cutoff)[0]
    assert yesterday["label"] == "当日"
    assert yesterday["end"] == datetime(2026, 11, 16, 17, tzinfo=ZONE)


def test_cutting_daily_comparison_queries_one_device_and_compares_equal_month_lengths(tmp_path):
    cfg = cutting_settings(project_root=tmp_path, data_source_mode="sqlserver")
    calls = []

    def fetch_all(sql, params):
        calls.append(params)
        return [dict(bucket_start=datetime.combine(day, datetime.min.time()), task_count=count, cut_length_m=count * 20, groove_length_m=0,
                     work_time_seconds=count * 60) for day, count in [
            (date(2026, 2, 28), 4), (date(2026, 3, 28), 6), (date(2026, 3, 30), 1), (date(2026, 3, 31), 5),
        ]]

    query = QueryService(cfg, client=SimpleNamespace(fetch_all=fetch_all))
    ranges = RangeReportService(cfg, query, None, None)
    today, week, month = ranges.cutting_closing_comparison(
        date(2026, 3, 1), date(2026, 3, 31), datetime(2026, 4, 3, 18, tzinfo=ZONE)
    )
    assert calls == [(cfg.report_machine_no, datetime(2026, 2, 1), datetime(2026, 3, 31, 17))]
    assert today["label"] == "当日"
    assert today["changes"]["task_count"] == "+400.0%"
    assert week["changes"]["task_count"] is None
    assert month["totals"]["task_count"] == 12
    assert month["changes"]["task_count"] == "+50.0%"
    assert month["comparison_end"] == datetime(2026, 2, 28, 17, tzinfo=ZONE)
    assert month["comparable_end"] == datetime(2026, 3, 28, 17, tzinfo=ZONE)
    assert month["end"] == datetime(2026, 3, 31, 17, tzinfo=ZONE)


def test_leadership_month_quarter_year_totals_and_comparisons(tmp_path):
    cfg = cutting_settings(project_root=tmp_path, data_source_mode="sqlserver")
    calls = []

    def fetch_all(sql, params):
        calls.append(params)
        return [dict(bucket_start=datetime.combine(day, datetime.min.time()), task_count=count, cut_length_m=count * 20, groove_length_m=0,
                     work_time_seconds=count * 60) for day, count in [
            (date(2025, 11, 18), 2), (date(2026, 8, 18), 3), (date(2026, 10, 18), 4), (date(2026, 11, 18), 8),
        ]]

    query = QueryService(cfg, client=SimpleNamespace(fetch_all=fetch_all))
    ranges = RangeReportService(cfg, query, None, None)
    columns = ranges.cutting_closing_comparison(
        date(2026, 11, 1), date(2026, 11, 30), datetime(2026, 11, 18, 17, 5, tzinfo=ZONE),
        grouping="month_quarter_year",
    )
    assert calls == [(cfg.report_machine_no, datetime(2024, 12, 31, 17), datetime(2026, 11, 18, 17))]
    assert [(column["label"], column["comparison_label"]) for column in columns] == [
        ("本月", "上月同期"), ("本季度", "上季度同期"), ("本年", "上年同期"),
    ]
    assert [column["totals"]["task_count"] for column in columns] == [8, 12, 15]
    assert [column["changes"]["task_count"] for column in columns] == ["+100.0%", "+300.0%", "+650.0%"]
    assert [column["comparison_end"].date() for column in columns] == [
        date(2026, 10, 18), date(2026, 8, 18), date(2025, 11, 18),
    ]
    assert all(column["end"] == datetime(2026, 11, 18, 17, tzinfo=ZONE) for column in columns)


@pytest.mark.parametrize(("cutoff", "index", "prior_end", "comparable_end"), [
    (datetime(2025, 6, 30, 17, tzinfo=ZONE), 1, date(2025, 3, 31), date(2025, 6, 29)),
    (datetime(2024, 12, 31, 17, tzinfo=ZONE), 2, date(2023, 12, 31), date(2024, 12, 30)),
])
def test_leadership_shorter_prior_quarter_or_year_compares_equal_days(cutoff, index, prior_end, comparable_end):
    def load(windows):
        return {window: {"tasks": (window[1] - window[0]).days} for window in windows}

    column = closing_comparison(cutoff.date(), cutoff.date(), cutoff, ZONE, load, grouping="month_quarter_year")[index]
    assert column["comparison_end"].date() == prior_end
    assert column["comparable_end"].date() == comparable_end
    assert column["end"] == cutoff
    assert column["totals"]["tasks"] == column["previous_totals"]["tasks"] + 1
    assert column["changes"]["tasks"] == "0.0%"


def test_generated_leadership_html_shows_its_month_quarter_year_ranges():
    root = Path(__file__).resolve().parents[2]
    cfg = cutting_settings(project_root=root)
    ranges = RangeReportService(cfg, QueryService(cfg), None, None)
    analysis = AnalysisResult(status="success", overview="演示分析", features="", alerts="", suggestions="")
    prepared = prepare_report(
        dict(report_id="closing-comparison", report_type="leadership", report_date="2026-03-01",
             created_at="2026-03-31T17:05:00+08:00"),
        settings=cfg, leadership_service=LeadershipReportService(cfg), range_service=ranges,
        chart_service=ChartService(), analysis_service=SimpleNamespace(),
        ensure_active=lambda: None, update_status=lambda *args: None, analyze=lambda *args, **kwargs: analysis,
    )
    html = ReportService(cfg).environment.get_template("report.html.j2").render(
        report=prepared.report, echarts_source="", demo_mode=True, device_name="切割演示"
    )
    table = html.split('<table aria-label="月、季度、年同期对比">', 1)[1].split("</table>", 1)[0]
    for text in ("本月", "本季度", "本年", "上月同期", "上季度同期", "上年同期", "切割长度（米）", "切割工时",
                 "本期可比区间", "2026-03-31 17:00", "2026-02-28 17:00", "2026-03-28 17:00",
                 "2025-12-29 17:00", "2025-03-31 17:00"):
        assert f">{text}<" in table, text
    assert "本周累计" not in table and "周从周一开始" not in html


def test_comparison_highlights_large_changes_and_keeps_missing_data_distinct(tmp_path):
    end = datetime(2026, 11, 18, 17, tzinfo=ZONE)
    start = end - timedelta(days=1)
    column = dict(label="今日", start=start, end=end, comparable_end=end, comparison_label="上一业务日",
                  comparison_start=start - timedelta(days=1), comparison_end=start,
                  totals=dict(task_count=5, cut_length_m=12, groove_length_m=11.99, work_time_seconds=10, electricity=2, gas=None),
                  previous_totals=dict(task_count=10, cut_length_m=10, groove_length_m=10, work_time_seconds=10, electricity=None, gas=3),
                  comparable_totals=dict(task_count=5, cut_length_m=12, groove_length_m=11.99, work_time_seconds=10, electricity=2, gas=None),
                  changes=dict(task_count="-50.0%", cut_length_m="+20.0%", groove_length_m="+19.9%", work_time_seconds="0.0%", electricity=None, gas=None))
    renderer = ReportService(cutting_settings(project_root=tmp_path))
    render = renderer.environment.get_template("closing_comparison.html.j2").module.render_closing_comparison
    table = render([column], [("task_count", "任务数", "count"), ("cut_length_m", "切割长度", "number"),
                              ("groove_length_m", "坡口长度", "number"), ("work_time_seconds", "切割工时", "duration"), ("electricity", "电耗", "number"),
                              ("gas", "气耗", "number")], "")

    assert 'lp-closing-change--unfavorable" title="不利变化">-50.0%</span>' in table
    assert 'lp-closing-change--favorable" title="有利变化">+20.0%</span>' in table
    assert "lp-closing-change\">+19.9%</span>" in table
    assert "lp-closing-change\">0.0%</span>" in table
    assert "lp-tone-warning\">同期数据不足</span>" in table
    assert "lp-tone-warning\">数据不完整</span>" in table


@pytest.mark.parametrize("metric", ["task_count", "cut_length_m", "groove_length_m", "job_count", "weld_length_m", "part_row_count",
                                   "incident_count", "critical_count", "high_count", "work_time_seconds", "working_hours", "electricity", "gas"])
def test_comparison_colors_follow_metric_direction_and_threshold(tmp_path, metric):
    render = ReportService(cutting_settings(project_root=tmp_path)).environment.get_template(
        "closing_comparison.html.j2").module.render_closing_comparison
    end = datetime(2026, 11, 18, 17, tzinfo=ZONE)
    start = end - timedelta(days=1)
    lower_is_better = metric in {"incident_count", "critical_count", "high_count"}
    neutral = metric in {"work_time_seconds", "working_hours", "electricity", "gas"}
    for delta in ["+20.0%", "-20.0%", "+19.9%", "-19.9%", "0.0%"]:
        column = dict(label="今日", start=start, end=end, comparable_end=end, comparison_label="上一业务日",
                      comparison_start=start - timedelta(days=1), comparison_end=start,
                      totals={metric: 10}, previous_totals={metric: 10}, comparable_totals={metric: 10}, changes={metric: delta})
        html = render([column], [(metric, "指标", "number")], "")
        significant = abs(float(delta[:-1])) >= 20 and not neutral
        favorable = significant and (delta.startswith('-') if lower_is_better else delta.startswith('+'))
        assert ('lp-closing-change--favorable' in html) == favorable
        assert ('lp-closing-change--unfavorable' in html) == (significant and not favorable)
        assert f'>{delta}</span>' in html
        assert '较同期' not in html


def test_workshop_closing_comparison_keeps_ship_filter_and_cutoff(tmp_path, monkeypatch):
    cfg = cutting_settings(project_root=tmp_path, data_source_mode="sqlserver")
    query = QueryService(cfg)
    calls = []

    def fetch(filters, *, force):
        calls.append((filters.start_date, filters.end_date, filters.project_code, filters.ship_no, force))
        return SimpleNamespace(summary_records=[
            SimpleNamespace(source_updated_at=stamp, cut_length=30, groove_length=2, work_time=120)
            for stamp in ("2026-11-16 17:00:00", "2026-11-17 17:00:00")
        ])

    monkeypatch.setattr(query, "fetch_summary_data", fetch)
    ranges = RangeReportService(cfg, query, None, None)
    today = ranges.cutting_closing_comparison(
        date(2026, 11, 17), date(2026, 11, 17), datetime(2026, 11, 17, 17, 5, tzinfo=ZONE),
        project_code="P1", ship_no="船名",
    )[0]
    assert calls == [(date(2026, 10, 1), date(2026, 11, 17), "P1", "船名", True)]
    assert today["totals"] == dict(task_count=1, cut_length_m=30, groove_length_m=2, work_time_seconds=120)


@pytest.mark.parametrize("start_hour,expected_hours", [(17, 24), (0, 41)])
def test_equipment_meter_cutoff_returns_known_hours_without_crossing_17(tmp_path, start_hour, expected_hours):
    cfg = cutting_settings(project_root=tmp_path)
    calls = []

    def list_month(source, month):
        calls.append(month)
        return snapshots

    service = EnergyPeriodService(cfg, SimpleNamespace(list_month=list_month))
    start = datetime(2026, 11, 16, start_hour, tzinfo=ZONE)
    end = datetime(2026, 11, 17, 17, tzinfo=ZONE)
    snapshots = [service.blank_day(day) for day in (start.date(), end.date())]
    for snapshot in snapshots:
        for slot in snapshot["rows"]:
            slot["electricity"] = {"value": 2, "status": "measured"}
            slot["gas"] = {"value": 1, "status": "interpolated"}
    snapshots[1]["rows"][5]["gas"] = {"value": None, "status": "missing"}
    snapshots[1]["rows"][17]["electricity"] = {"value": 999, "status": "measured"}
    assert service.closing_meter_coverage(((start, end),))[(start, end)] == {
        "electricity": {"value": expected_hours * 2, "valid_count": expected_hours, "expected_count": expected_hours},
        "gas": {"value": expected_hours - 1, "valid_count": expected_hours - 1, "expected_count": expected_hours},
    }
    assert calls == ["2026-11"]


@pytest.mark.parametrize(("kind", "own_metrics"), [
    ("workshop", ("坡口长度（米）", "切割长度（米）")),
    ("equipment", ("仪表电耗（kWh）", "仪表气耗（Nm³）")),
])
def test_generated_cutting_export_renders_its_own_comparison(kind, own_metrics, tmp_path):
    cfg = cutting_settings(project_root=tmp_path)
    query = QueryService(cfg)
    snapshots = []
    energy = EnergyPeriodService(cfg, SimpleNamespace(list_month=lambda _source, month: [
        item for item in snapshots if item["report_date"].startswith(month)
    ]), query_service=query)
    if kind == "equipment":
        for day in (15, 16, 17):
            snapshot = energy.blank_day(date(2026, 11, day))
            for row in snapshot["rows"]:
                row["gas"] = {"value": 1, "status": "measured"}
                row["electricity"] = {"value": 2, "status": "measured"}
            snapshots.append(snapshot)
        snapshots[-1]["rows"][5]["gas"] = {"value": None, "status": "missing"}
    ranges = RangeReportService(cfg, query, SimpleNamespace(get_confirmations=lambda ids: {}), energy)
    analysis = AnalysisResult(status="success", overview="演示分析", features="", alerts="", suggestions="")
    job = dict(report_id="closing", report_type=kind, report_date="2026-11-17",
               period_start="2026-11-17", period_end="2026-11-17",
               project_code=None, ship_no=None, created_at="2026-11-17T17:05:00+08:00")
    arguments = dict(settings=cfg, range_service=ranges, query_service=query, chart_service=ChartService(),
                     analysis_service=SimpleNamespace(), ensure_active=lambda: None,
                     update_status=lambda *args: None, analyze=lambda *args, **kwargs: analysis)
    if kind == "workshop":
        prepared = prepare_workshop(job, kind, workshop_repository=None, statistics_service=StatisticsService(), **arguments)
    else:
        prepared = prepare_equipment(job, energy_period_service=energy, **arguments)
    html = ReportService(cfg).environment.get_template("report.html.j2").render(
        report=prepared.report, echarts_source="", demo_mode=True, device_name="切割演示", energy_connected=cfg.energy_connected
    )
    table = html.split('<table aria-label="日、周、月同期对比">', 1)[1].split("</table>", 1)[0]
    for expected in ("今日", "本周累计", "本月累计", "2026-11-17 17:00", "2026-11-16 00:00", "2026-11-01 00:00", *own_metrics):
        assert expected in table
    if kind == "equipment":
        today = prepared.report["closing_comparison"][0]
        assert today["totals"]["gas"] is None and today["changes"]["gas"] is None
        assert today["previous_totals"]["gas"] == 24 and today["changes"]["electricity"] == "0.0%"
        assert "23.0" in table and "23/24 小时" in table
        assert "电耗（kWh）" in html and "气耗（Nm³）" in html
    else:
        assert "仪表电耗" not in table
