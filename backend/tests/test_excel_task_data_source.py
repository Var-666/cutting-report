from datetime import date, datetime
from decimal import Decimal

from openpyxl import Workbook

from app.config import Settings
from app.database.excel_task import ExcelTaskDataSource
from app.models.domain import ReportFilter
from app.services.custom_report_service import CustomReportService
from app.services.leadership_report_service import LeadershipReportService
from app.services.query_service import QueryService
from app.services.sql_workbench_service import SqlWorkbenchService


HEADERS = [
    "TASK_ID",
    "PROJECT_CODE",
    "PROJECT_NAME",
    "SEGMENT_NAME",
    "MACHINE_NO",
    "STATUS",
    "START_TIME",
    "END_TIME",
    "WORK_TIME",
    "OP_DATE",
    "OP_PROGRAM",
    "OP_LENGTH_CUT",
    "OP_LENGTH_GROOVE",
    "REMARK",
]


def create_task_workbook(path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "TASK"
    sheet.append(HEADERS)
    sheet.append(
        [
            101,
            None,
            "PJT-A",
            None,
            "Cutter01",
            2,
            datetime(2026, 8, 18, 8),
            datetime(2026, 8, 18, 9),
            3600,
            "2026-08-18",
            "P-01",
            1200,
            200,
            None,
        ]
    )
    sheet.append(
        [
            102,
            None,
            "PJT-A",
            None,
            "Cutter02",
            2,
            datetime(2026, 8, 18, 10),
            datetime(2026, 8, 18, 10, 30),
            1800,
            "2026-08-18",
            "P-02",
            800,
            0,
            "复核",
        ]
    )
    sheet.append(
        [
            103,
            None,
            "PJT-A",
            None,
            "Cutter01",
            1,
            datetime(2026, 8, 18, 11),
            None,
            600,
            "2026-08-18",
            "P-03",
            500,
            0,
            None,
        ]
    )
    sheet.append(
        [
            99,
            None,
            "PJT-B",
            None,
            "Cutter01",
            2,
            datetime(2026, 7, 31, 8),
            datetime(2026, 7, 31, 8, 20),
            1200,
            "2026-07-31",
            "P-00",
            600,
            0,
            None,
        ]
    )
    sheet.append(
        [
            104,
            None,
            "PJT-A",
            None,
            "Cutter01",
            2,
            datetime(2026, 8, 18, 16),
            datetime(2026, 8, 18, 8),
            -28800,
            "2026-08-18",
            "P-BAD",
            900,
            100,
            "结束时间早于开始时间",
        ]
    )
    workbook.save(path)


def excel_settings(tmp_path) -> Settings:
    create_task_workbook(tmp_path / "TASK.xlsx")
    return Settings(
        project_root=tmp_path,
        demo_mode=False,
        data_source_mode="excel",
        task_excel_file="TASK.xlsx",
    )


def test_excel_task_status_and_daily_query(tmp_path) -> None:
    settings = excel_settings(tmp_path)
    source = ExcelTaskDataSource(settings.task_excel_path)

    status = source.status()
    bundle = QueryService(settings).fetch_report_data(ReportFilter(date(2026, 8, 18)))

    assert status["row_count"] == 5
    assert status["completed_count"] == 4
    assert status["reportable_completed_count"] == 3
    assert status["excluded_negative_work_time_count"] == 1
    assert status["date_min"] == "2026-07-31"
    assert status["date_max"] == "2026-08-18"
    assert status["machines"] == ["Cutter01", "Cutter02"]
    assert [row.task_id for row in bundle.summary_records] == ["101", "102"]
    assert bundle.workshop_records[0].project_code == "PJT-A"
    assert bundle.workshop_records[0].ship_no == "PJT-A"
    assert bundle.equipment_records[0].production_end_time == datetime(2026, 8, 18, 9)
    assert source.list_projects() == ["PJT-A", "PJT-B"]
    assert source.latest_completed_date(date(2026, 8, 19)) == date(2026, 8, 18)
    assert source.latest_completed_date(date(2026, 8, 1)) == date(2026, 7, 31)
    assert sum(
        (row.cut_length or Decimal("0") for row in bundle.summary_records),
        Decimal("0"),
    ) == Decimal("2")
    assert bundle.data_updated_at == datetime(2026, 8, 18, 10, 30)


def test_excel_task_leadership_monthly_totals(tmp_path) -> None:
    settings = excel_settings(tmp_path)

    result = LeadershipReportService(settings).fetch(date(2026, 8, 18))

    assert result.source_mode == "excel"
    assert result.current_month_total.task_count == 2
    assert result.current_month_total.cut_length_m == Decimal("2")
    assert result.current_month_total.groove_length_m == Decimal("0.2")
    assert result.monthly_records[0].task_count == 2
    assert result.monthly_records[1].task_count == 1
    assert result.twelve_month_total.task_count == 3


def test_custom_query_runs_against_excel_task_table(tmp_path) -> None:
    settings = excel_settings(tmp_path)
    query_task = {
        "sql_text": """
            SELECT OP_DATE AS production_date,
                   TASK_ID AS task_id,
                   OP_LENGTH_CUT / 1000.0 AS cut_length
            FROM dbo.task
            WHERE STATUS = 2
              AND WORK_TIME >= 0
              AND OP_DATE >= :period_start
              AND OP_DATE < :period_end
            ORDER BY TASK_ID
        """,
        "columns": [
            {"field": "production_date", "label": "日期", "type": "date"},
            {"field": "task_id", "label": "任务号", "type": "text"},
            {"field": "cut_length", "label": "切割长度", "type": "number"},
        ],
    }

    rows = CustomReportService(settings).execute(
        query_task,
        report_date=date(2026, 8, 18),
        period_type="day",
    )

    assert rows == [
        {"production_date": "2026-08-18", "task_id": 101, "cut_length": 1.2},
        {"production_date": "2026-08-18", "task_id": 102, "cut_length": 0.8},
    ]


def test_sql_workbench_runs_read_only_query_against_excel(tmp_path) -> None:
    settings = excel_settings(tmp_path)
    source = ExcelTaskDataSource(settings.task_excel_path)

    result = SqlWorkbenchService(settings).execute_excel(
        source,
        """
        SELECT TOP (2) TASK_ID, OP_DATE, OP_LENGTH_CUT
        FROM dbo.task
        WHERE STATUS = 2
        ORDER BY TASK_ID DESC
        """,
        max_rows=1,
    )

    assert result.columns == ["TASK_ID", "OP_DATE", "OP_LENGTH_CUT"]
    assert result.items == [
        {"TASK_ID": 104, "OP_DATE": "2026-08-18", "OP_LENGTH_CUT": 900}
    ]
    assert result.returned == 1
    assert result.truncated is True
