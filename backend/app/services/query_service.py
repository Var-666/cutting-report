from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import Settings
from app.database.sqlserver import SqlServerClient, SqlServerConfigurationError
from app.database.excel_task import ExcelTaskDataSource
from app.models.domain import (
    EquipmentRecord,
    QueryBundle,
    ReportFilter,
    SummaryBaseRecord,
    WorkshopRecord,
)


def _decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


class QueryService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = SqlServerClient(settings)
        self.excel = ExcelTaskDataSource(settings.task_excel_path)
        self.query_dir = Path(__file__).resolve().parents[1] / "database" / "queries"

    def fetch_report_data(self, filters: ReportFilter) -> QueryBundle:
        if self.settings.effective_data_source_mode == "excel":
            return self._excel_data(filters)
        if self.settings.effective_data_source_mode == "demo":
            return self._demo_data(filters)
        return self._sqlserver_data(filters)

    def list_projects(self, filters: ReportFilter) -> list[str]:
        if self.settings.effective_data_source_mode == "excel":
            return self.excel.list_projects()
        if self.settings.effective_data_source_mode == "demo":
            return ["A001", "A002", "B101"]
        start = filters.report_date
        end = start + timedelta(days=1)
        rows = self.client.fetch_all(
            """
            WITH source AS (
                SELECT
                    NULLIF(LTRIM(RTRIM([PROJECT_NAME])), '') AS project_code,
                    COALESCE(
                        TRY_CONVERT(date, NULLIF(LTRIM(RTRIM([OP_DATE])), ''), 23),
                        CONVERT(date, [START_TIME])
                    ) AS production_date
                FROM [dbo].[task]
                WHERE [STATUS] = 2
            )
            SELECT DISTINCT project_code
            FROM source
            WHERE production_date >= ? AND production_date < ?
              AND project_code IS NOT NULL
            ORDER BY project_code
            """,
            (start, end),
        )
        return [str(row["project_code"]) for row in rows]

    def list_ships(self, filters: ReportFilter) -> list[str]:
        if self.settings.effective_data_source_mode == "excel":
            names = self.excel.list_projects()
            return [filters.project_code] if filters.project_code in names else names
        if self.settings.effective_data_source_mode == "demo":
            names = ["A001", "A002", "B101"]
            return [filters.project_code] if filters.project_code in names else names
        rows = self.client.fetch_all(
            """
            WITH source AS (
                SELECT
                    NULLIF(LTRIM(RTRIM([PROJECT_NAME])), '') AS project_name,
                    COALESCE(
                        TRY_CONVERT(date, NULLIF(LTRIM(RTRIM([OP_DATE])), ''), 23),
                        CONVERT(date, [START_TIME])
                    ) AS production_date
                FROM [dbo].[task]
                WHERE [STATUS] = 2
            )
            SELECT DISTINCT project_name
            FROM source
            WHERE production_date >= ? AND production_date < ?
              AND project_name IS NOT NULL
              AND (? IS NULL OR project_name = ?)
            ORDER BY project_name
            """,
            (
                filters.report_date,
                filters.report_date + timedelta(days=1),
                filters.project_code,
                filters.project_code,
            ),
        )
        return [str(row["project_name"]) for row in rows]

    def latest_production_date(self, on_or_before: date) -> date | None:
        if self.settings.effective_data_source_mode == "excel":
            return self.excel.latest_completed_date(on_or_before)
        if self.settings.effective_data_source_mode == "demo":
            return on_or_before
        rows = self.client.fetch_all(
            """
            WITH source AS (
                SELECT
                    COALESCE(
                        TRY_CONVERT(date, NULLIF(LTRIM(RTRIM([OP_DATE])), ''), 23),
                        CONVERT(date, [START_TIME])
                    ) AS production_date
                FROM [dbo].[task]
                WHERE [STATUS] = 2
                  AND COALESCE([WORK_TIME], 0) >= 0
            )
            SELECT MAX(production_date) AS latest_date
            FROM source
            WHERE production_date <= ?
            """,
            (on_or_before,),
        )
        if not rows or rows[0]["latest_date"] is None:
            return None
        value = rows[0]["latest_date"]
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        return date.fromisoformat(str(value)[:10])

    def _read_query(self, name: str) -> str:
        sql = (self.query_dir / name).read_text(encoding="utf-8")
        if "TODO:" in sql:
            raise SqlServerConfigurationError(
                f"{name} 尚未映射实际 SQL Server 表结构，不能关闭演示模式"
            )
        return sql

    def _excel_data(self, filters: ReportFilter) -> QueryBundle:
        rows = self.excel.completed_rows(
            start=filters.report_date,
            end=filters.report_date + timedelta(days=1),
            project_code=filters.project_code,
            ship_no=filters.ship_no,
        )
        normalized = [self.excel.normalized(row) for row in rows]
        summaries = [
            SummaryBaseRecord(
                task_id=row["task_id"],
                project_code=row["project_name"],
                ship_no=row["project_name"],
                segment_name=row["segment_name"],
                program_no=row["program_no"],
                cut_length=row["cut_length"],
                groove_length=row["groove_length"],
                work_time=row["work_time"],
                production_time=row["production_time"],
                source_updated_at=row["source_updated_at"],
            )
            for row in normalized
        ]
        workshops = [
            WorkshopRecord(
                task_id=row["task_id"],
                project_code=row["project_name"],
                ship_no=row["project_name"],
                segment_name=row["segment_name"],
                program_no=row["program_no"],
                cut_length=row["cut_length"],
                groove_length=row["groove_length"],
                work_time=row["work_time"],
                remark=row["remark"],
                production_time=row["production_time"],
            )
            for row in normalized
        ]
        equipment = [
            EquipmentRecord(
                task_id=row["task_id"],
                equipment_id=row["equipment_id"],
                project_code=row["project_name"],
                ship_no=row["project_name"],
                segment_name=row["segment_name"],
                cut_length=row["cut_length"],
                work_time=row["work_time"],
                gas_consumption=None,
                electricity_consumption=None,
                production_time=row["production_time"],
                remark=row["remark"],
            )
            for row in normalized
        ]
        updated_values = [
            row["source_updated_at"]
            for row in normalized
            if isinstance(row["source_updated_at"], datetime)
        ]
        return QueryBundle(
            summary_records=summaries,
            workshop_records=workshops,
            equipment_records=equipment,
            data_updated_at=max(updated_values) if updated_values else None,
        )

    def _sqlserver_data(self, filters: ReportFilter) -> QueryBundle:
        timezone = ZoneInfo(self.settings.timezone)
        start = datetime.combine(filters.report_date, time.min, tzinfo=timezone).replace(
            tzinfo=None
        )
        end = start + timedelta(days=1)
        parameters = (
            start,
            end,
            filters.project_code,
            filters.project_code,
            filters.ship_no,
            filters.ship_no,
        )
        summary_rows = self.client.fetch_all(
            self._read_query("summary_base.sql"), parameters
        )
        workshop_rows = self.client.fetch_all(self._read_query("workshop.sql"), parameters)
        equipment_rows = self.client.fetch_all(
            self._read_query("equipment.sql"), parameters
        )
        summaries = [
            SummaryBaseRecord(
                task_id=str(row["task_id"]),
                project_code=row.get("project_code"),
                ship_no=row.get("ship_no"),
                segment_name=row.get("segment_name"),
                program_no=row.get("program_no"),
                cut_length=_decimal(row.get("cut_length")),
                groove_length=_decimal(row.get("groove_length")),
                work_time=_decimal(row.get("work_time")),
                production_time=row.get("production_time"),
                source_updated_at=row.get("source_updated_at"),
            )
            for row in summary_rows
        ]
        workshops = [
            WorkshopRecord(
                task_id=str(row["task_id"]),
                project_code=row.get("project_code"),
                ship_no=row.get("ship_no"),
                segment_name=row.get("segment_name"),
                program_no=row.get("program_no"),
                cut_length=_decimal(row.get("cut_length")),
                groove_length=_decimal(row.get("groove_length")),
                work_time=_decimal(row.get("work_time")),
                remark=row.get("remark"),
                production_time=row.get("production_time"),
            )
            for row in workshop_rows
        ]
        equipment = [
            EquipmentRecord(
                task_id=str(row["task_id"]),
                equipment_id=row.get("equipment_id"),
                project_code=row.get("project_code"),
                ship_no=row.get("ship_no"),
                segment_name=row.get("segment_name"),
                cut_length=_decimal(row.get("cut_length")),
                work_time=_decimal(row.get("work_time")),
                gas_consumption=_decimal(row.get("gas_consumption")),
                electricity_consumption=_decimal(row.get("electricity_consumption")),
                production_time=row.get("production_time"),
                remark=row.get("remark"),
            )
            for row in equipment_rows
        ]
        latest_update = max(
            (row.source_updated_at for row in summaries if row.source_updated_at),
            default=None,
        )
        return QueryBundle(summaries, workshops, equipment, latest_update)

    def _demo_data(self, filters: ReportFilter) -> QueryBundle:
        project_names = ["A001", "A001", "A002", "B101"]
        summaries: list[SummaryBaseRecord] = []
        workshops: list[WorkshopRecord] = []
        equipment: list[EquipmentRecord] = []
        timezone = ZoneInfo(self.settings.timezone)
        base_time = datetime.combine(filters.report_date, time(8), tzinfo=timezone)
        for index in range(1, 29):
            project_name = project_names[(index - 1) % len(project_names)]
            if filters.project_code and filters.project_code != project_name:
                continue
            if filters.ship_no and filters.ship_no != project_name:
                continue
            task_id = f"{filters.report_date:%Y%m%d}-{index:03d}"
            cut_length = Decimal("42") + Decimal(index) * Decimal("3.650")
            groove_length = Decimal("3.5") + Decimal(index % 7) * Decimal("1.650")
            work_time = Decimal(95 + index * 13)
            summary = SummaryBaseRecord(
                task_id=task_id,
                project_code=project_name,
                ship_no=project_name,
                segment_name=f"P{((index - 1) % 8) + 1:02d}",
                program_no=f"OP-{index:03d}",
                cut_length=cut_length,
                groove_length=groove_length,
                work_time=work_time,
                production_time=base_time + timedelta(minutes=index * 21),
                source_updated_at=base_time + timedelta(hours=10),
            )
            summaries.append(summary)
            workshops.append(
                WorkshopRecord(
                    task_id=task_id,
                    project_code=project_name,
                    ship_no=project_name,
                    segment_name=summary.segment_name,
                    program_no=summary.program_no,
                    cut_length=cut_length,
                    groove_length=groove_length,
                    work_time=work_time,
                    production_time=summary.production_time,
                    remark="演示数据",
                )
            )
            equipment.append(
                EquipmentRecord(
                    task_id=task_id,
                    equipment_id=f"CUT-{((index - 1) % 4) + 1:02d}",
                    project_code=project_name,
                    ship_no=project_name,
                    segment_name=summary.segment_name,
                    cut_length=cut_length,
                    work_time=work_time,
                    gas_consumption=Decimal("8.5") + Decimal(index) * Decimal("0.42"),
                    electricity_consumption=Decimal("14.2")
                    + Decimal(index) * Decimal("0.61"),
                    production_time=summary.production_time,
                    remark="演示数据",
                )
            )
        return QueryBundle(
            summary_records=summaries,
            workshop_records=workshops,
            equipment_records=equipment,
            data_updated_at=base_time + timedelta(hours=10),
        )
