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
    captured = []

    def fake_fetch_all(sql, parameters):
        captured.append({"sql": sql, "parameters": parameters})
        if "project_name" in sql:
            return [
                {
                    "project_name": "P001",
                    "task_count": 20,
                    "cut_length_m": Decimal("969.272"),
                    "work_time_seconds": Decimal("7595"),
                }
            ]
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

    assert captured[0]["parameters"] == (date(2025, 8, 1),)
    assert captured[1]["parameters"] == (date(2025, 8, 1), date(2025, 9, 1))
    assert "[dbo].[task]" in captured[0]["sql"]
    assert "t.[STATUS] = 2" in captured[0]["sql"]
    assert "COALESCE(t.[WORK_TIME], 0) >= 0" in captured[0]["sql"]
    assert result.source_mode == "sqlserver"
    assert result.current_month_total.task_count == 20
    assert result.current_month_total.cut_length_m == Decimal("969.272")
    assert result.current_month_total.work_time_seconds == 7595
    assert result.project_distribution[0].project_name == "P001"


def test_sqlserver_report_is_cached_by_month_and_can_be_refreshed(
    tmp_path, monkeypatch
) -> None:
    service = LeadershipReportService(
        Settings(
            project_root=tmp_path,
            demo_mode=False,
            data_source_mode="sqlserver",
            leadership_report_cache_seconds=300,
        )
    )
    call_count = 0

    def fake_fetch_all(sql, parameters):
        nonlocal call_count
        call_count += 1
        if "project_name" in sql:
            return []
        return [
            {
                "period_type": "daily",
                "period_start": date(2026, 8, 25),
                "task_count": call_count,
                "cut_length_m": Decimal("1"),
                "groove_length_m": Decimal("0"),
                "work_time_seconds": Decimal("1"),
                "remark": None,
            }
        ]

    monkeypatch.setattr(service.client, "fetch_all", fake_fetch_all)

    first = service.fetch(date(2026, 8, 1))
    second = service.fetch(date(2026, 8, 25))

    assert first is second
    assert call_count == 2

    refreshed = service.fetch(date(2026, 8, 1), force=True)

    assert refreshed is not first
    assert call_count == 4
