from datetime import date, datetime
from decimal import Decimal

from app.config import Settings
from app.models.domain import EquipmentRecord
from app.services.chart_service import ChartService
from app.services.leadership_report_service import LeadershipReportService


def equipment(
    task_id: str,
    equipment_id: str,
    cut: str,
    work: str,
    production_time: datetime | None = None,
) -> EquipmentRecord:
    return EquipmentRecord(
        task_id=task_id,
        equipment_id=equipment_id,
        project_code=None,
        ship_no=None,
        segment_name=None,
        cut_length=Decimal(cut),
        work_time=Decimal(work),
        gas_consumption=Decimal("2.5"),
        electricity_consumption=Decimal("5.5"),
        production_time=production_time,
    )


def test_leadership_charts_match_daily_and_monthly_periods(tmp_path) -> None:
    report = LeadershipReportService(
        Settings(project_root=tmp_path, demo_mode=True, data_source_mode="demo")
    ).fetch(date(2026, 8, 1))

    charts = ChartService().build_leadership(report)

    assert len(charts["daily_trend"]["xAxis"]["data"]) == len(
        report.daily_records
    )
    assert len(charts["monthly_trend"]["xAxis"]["data"]) == 12
    assert charts["daily_trend"]["xAxis"]["data"][0] == "08-01"
    assert charts["daily_trend"]["xAxis"]["data"][-1] == "08-25"
    assert charts["monthly_trend"]["xAxis"]["data"][0] == "2025-09"
    assert charts["monthly_trend"]["xAxis"]["data"][-1] == "2026-08"
    assert charts["daily_trend"]["series"][2]["name"] == "切割工时"
    assert charts["monthly_trend"]["series"][2]["name"] == "完成任务"
    assert charts["daily_trend"]["backgroundColor"] == "#ffffff"
    assert charts["daily_trend"]["xAxis"]["axisLine"]["lineStyle"]["color"] == "#9eb4c0"
    assert charts["daily_trend"]["series"][0]["itemStyle"]["borderRadius"] == [3, 3, 0, 0]


def test_equipment_chart_uses_only_visible_table_fields_in_time_order() -> None:
    records = [
        equipment("1", "Cutter01", "10", "3600", datetime(2026, 8, 18, 9, 30)),
        equipment("2", "Cutter02", "15", "1800", datetime(2026, 8, 18, 8, 15)),
    ]

    charts = ChartService().build([], records)
    chart = charts["equipment_detail"]

    assert "equipment_production" not in charts
    assert chart["xAxis"]["data"] == ["08:15", "09:30"]
    assert chart["legend"]["data"] == ["气耗", "电耗", "切割时长（分钟）"]
    assert chart["series"][2]["data"] == [30.0, 60.0]
    assert "Cutter01" not in str(chart)
    assert "切割长度" not in str(chart)
    assert "任务数" not in str(chart)


def test_chart_type_can_switch_all_series_to_area() -> None:
    records = [equipment("1", "Cutter01", "10", "3600")]

    charts = ChartService().build([], records, "area")

    for option in charts.values():
        for series in option["series"]:
            assert series["type"] == "line"
            assert series["areaStyle"] == {"opacity": 0.16}
