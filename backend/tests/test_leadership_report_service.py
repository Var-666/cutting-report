from datetime import date
from decimal import Decimal

from app.config import Settings
from app.services.leadership_report_service import LeadershipReportService


def test_demo_report_has_daily_total_and_twelve_months(tmp_path) -> None:
    service = LeadershipReportService(
        Settings(project_root=tmp_path, demo_mode=True, data_source_mode="demo")
    )

    result = service.fetch(date(2025, 8, 18))

    assert result.report_month == date(2025, 8, 1)
    assert result.source_mode == "demo"
    assert len(result.daily_records) == 10
    assert len(result.monthly_records) == 12
    assert result.daily_records[0].period_start > result.daily_records[-1].period_start
    assert result.monthly_records[0].period_start == date(2025, 8, 1)
    assert result.monthly_records[-1].period_start == date(2024, 9, 1)
    assert result.current_month_total.task_count == sum(
        row.task_count for row in result.daily_records
    )
    assert result.current_month_total.cut_length_m == sum(
        (row.cut_length_m for row in result.daily_records), Decimal("0")
    )
    assert result.current_month_total.groove_length_m == Decimal("0")
    assert result.twelve_month_total.task_count == sum(
        row.task_count for row in result.monthly_records
    )
    assert result.twelve_month_total.cut_length_m == sum(
        (row.cut_length_m for row in result.monthly_records), Decimal("0")
    )
    assert result.twelve_month_total.work_time_seconds == sum(
        row.work_time_seconds for row in result.monthly_records
    )


def test_sqlserver_rows_are_normalized_without_portion_deduplication(
    tmp_path, monkeypatch
) -> None:
    service = LeadershipReportService(
        Settings(project_root=tmp_path, demo_mode=False, data_source_mode="sqlserver")
    )
    captured = {}

    def fake_fetch_all(sql, parameters):
        captured["sql"] = sql
        captured["parameters"] = parameters
        return [
            {
                "period_type": "daily",
                "period_start": date(2025, 8, 4),
                "task_count": 20,
                "cut_length_m": Decimal("969.272"),
                "groove_length_m": Decimal("0"),
                "work_time_seconds": Decimal("7595"),
                "remark": None,
            },
            {
                "period_type": "monthly",
                "period_start": date(2025, 8, 1),
                "task_count": 20,
                "cut_length_m": Decimal("969.272"),
                "groove_length_m": Decimal("0"),
                "work_time_seconds": Decimal("7595"),
                "remark": None,
            },
        ]

    monkeypatch.setattr(service.client, "fetch_all", fake_fetch_all)

    result = service.fetch(date(2025, 8, 18))

    assert captured["parameters"] == (date(2025, 8, 1),)
    assert "[dbo].[task]" in captured["sql"]
    assert "t.[STATUS] = 2" in captured["sql"]
    assert "COALESCE(t.[WORK_TIME], 0) >= 0" in captured["sql"]
    assert result.source_mode == "sqlserver"
    assert result.current_month_total.task_count == 20
    assert result.current_month_total.cut_length_m == Decimal("969.272")
    assert result.current_month_total.work_time_seconds == 7595
