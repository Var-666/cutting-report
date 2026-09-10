from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Iterable
from zoneinfo import ZoneInfo

from app.config import Settings
from app.database.workshop import WorkshopRepository
from app.models.domain import (
    EquipmentRecord,
    LeadershipPeriodRecord,
    LeadershipReportResult,
    LeadershipSnapshotState,
    ProjectDistributionRecord,
    QueryBundle,
    ReportFilter,
    WorkshopRecord,
    to_primitive,
)
from app.services.energy_period_service import channel_result
from app.services.equipment_service import summarize_equipment
from app.services.query_service import QueryService


MAX_REPORT_RANGE_DAYS = 366


def validate_report_range(start_date: date, end_date: date) -> None:
    if end_date < start_date:
        raise ValueError("结束日期不能早于开始日期")
    if (end_date - start_date).days >= MAX_REPORT_RANGE_DAYS:
        raise ValueError(f"查询区间最长为 {MAX_REPORT_RANGE_DAYS} 天")
    if not 1900 <= start_date.year <= 2100 or not 1900 <= end_date.year <= 2100:
        raise ValueError("日期需在 1900—2100 年之间")


def report_filter(
    start_date: date,
    end_date: date,
    *,
    project_code: str | None = None,
    ship_no: str | None = None,
) -> ReportFilter:
    validate_report_range(start_date, end_date)
    return ReportFilter(
        report_date=start_date,
        period_start=start_date,
        period_end=end_date,
        project_code=project_code,
        ship_no=ship_no,
    )


def previous_equal_range(start_date: date, end_date: date) -> tuple[date, date]:
    length = end_date - start_date + timedelta(days=1)
    previous_end = start_date - timedelta(days=1)
    return previous_end - length + timedelta(days=1), previous_end


def _number(value: Decimal | None) -> Decimal:
    return value if value is not None else Decimal("0")


def _total(
    period_start: date,
    records: Iterable,
    *,
    remark: str | None = None,
) -> LeadershipPeriodRecord:
    rows = list(records)
    return LeadershipPeriodRecord(
        period_start=period_start,
        task_count=len(rows),
        cut_length_m=sum((_number(row.cut_length) for row in rows), Decimal("0")),
        groove_length_m=sum(
            (_number(getattr(row, "groove_length", None)) for row in rows),
            Decimal("0"),
        ),
        work_time_seconds=int(
            sum((_number(row.work_time) for row in rows), Decimal("0"))
        ),
        remark=remark,
    )


class RangeReportService:
    def __init__(
        self,
        settings: Settings,
        query_service: QueryService,
        workshop: WorkshopRepository,
        energy_service,
    ) -> None:
        self.settings = settings
        self.query_service = query_service
        self.workshop = workshop
        self.energy_service = energy_service

    def bundle(
        self,
        start_date: date,
        end_date: date,
        *,
        project_code: str | None = None,
        ship_no: str | None = None,
    ) -> QueryBundle:
        return self.query_service.fetch_report_data(
            report_filter(
                start_date,
                end_date,
                project_code=project_code,
                ship_no=ship_no,
            )
        )

    def leadership(
        self, start_date: date, end_date: date
    ) -> LeadershipReportResult:
        bundle = self.bundle(start_date, end_date)
        by_day: dict[date, list] = defaultdict(list)
        by_month: dict[date, list] = defaultdict(list)
        by_project: dict[str, list] = defaultdict(list)
        for row in bundle.summary_records:
            if row.production_time is None:
                continue
            stamp = row.production_time.date()
            by_day[stamp].append(row)
            by_month[stamp.replace(day=1)].append(row)
            by_project[row.project_code or "未标注工程"].append(row)

        daily = [
            _total(
                stamp,
                rows,
                remark=(
                    f"含 {sum(bool(getattr(row, 'remark', None)) for row in rows)} 条任务备注"
                    if any(getattr(row, "remark", None) for row in rows)
                    else None
                ),
            )
            for stamp, rows in sorted(by_day.items(), reverse=True)
        ]
        monthly = [
            _total(stamp, rows)
            for stamp, rows in sorted(by_month.items(), reverse=True)
        ]
        projects = [
            ProjectDistributionRecord(
                project_name=name,
                task_count=len(rows),
                cut_length_m=sum(
                    (_number(row.cut_length) for row in rows), Decimal("0")
                ),
                work_time_seconds=int(
                    sum((_number(row.work_time) for row in rows), Decimal("0"))
                ),
            )
            for name, rows in by_project.items()
        ]
        projects.sort(key=lambda row: (-row.task_count, row.project_name))
        period_total = _total(
            start_date,
            bundle.summary_records,
            remark=f"{start_date.isoformat()} 至 {end_date.isoformat()}",
        )
        return LeadershipReportResult(
            report_month=start_date.replace(day=1),
            source_mode=self.settings.effective_data_source_mode,
            daily_records=daily,
            monthly_records=monthly,
            current_month_total=period_total,
            twelve_month_total=period_total,
            project_distribution=projects,
            snapshot=LeadershipSnapshotState(
                data_as_of=bundle.data_updated_at
                or datetime.now(ZoneInfo(self.settings.timezone)),
                source=self.settings.effective_data_source_mode,
            ),
            period_start=start_date,
            period_end=end_date,
        )

    def workshop_records(
        self,
        start_date: date,
        end_date: date,
        *,
        project_code: str | None = None,
        ship_no: str | None = None,
    ) -> tuple[QueryBundle, list[WorkshopRecord]]:
        bundle = self.bundle(
            start_date,
            end_date,
            project_code=project_code,
            ship_no=ship_no,
        )
        confirmations = self.workshop.get_confirmations(
            [str(row.task_id) for row in bundle.workshop_records]
        )
        rows = []
        for row in bundle.workshop_records:
            confirmation = confirmations.get(str(row.task_id))
            rows.append(
                replace(
                    row,
                    confirmed_by_id=(
                        confirmation["worker_id"] if confirmation else None
                    ),
                    confirmed_by=(
                        confirmation["worker_name"] if confirmation else None
                    ),
                    confirmed_at=(
                        datetime.fromisoformat(confirmation["confirmed_at"])
                        if confirmation
                        else None
                    ),
                )
            )
        return bundle, rows

    @staticmethod
    def _energy_channel(records: list[EquipmentRecord], field: str) -> dict:
        values = [getattr(row, field) for row in records]
        valid = [value for value in values if value is not None]
        status = "missing" if not valid else "measured" if len(valid) == len(values) else "partial"
        return channel_result(
            sum(valid, Decimal("0")) if valid else None,
            status,
            valid_count=len(valid),
            complete_count=len(valid),
            expected_count=len(values),
            estimated_count=0,
        )

    def equipment(self, start_date: date, end_date: date, *, force: bool = False) -> dict:
        filters = report_filter(start_date, end_date)
        source = self.query_service.fetch_equipment_data(filters, force=force)
        energy = self.energy_service.enrich(source.records, force=force)
        records = energy.records
        by_day: dict[date, list[EquipmentRecord]] = defaultdict(list)
        for row in records:
            if row.production_time is not None:
                by_day[row.production_time.date()].append(row)

        daily_rows = []
        today = datetime.now(ZoneInfo(self.settings.timezone)).date()
        stamp = start_date
        while stamp <= end_date:
            rows = by_day.get(stamp, [])
            summary = summarize_equipment(rows, energy.mode)
            daily_rows.append(
                {
                    "report_date": stamp.isoformat(),
                    "task_count": len(rows),
                    "work_time_seconds": summary["total_work_time"],
                    "electricity": self._energy_channel(
                        rows, "electricity_consumption"
                    ),
                    "gas": self._energy_channel(rows, "gas_consumption"),
                    "is_future": stamp > today,
                    "is_today": stamp == today,
                    "saved": False,
                }
            )
            stamp += timedelta(days=1)

        summary = summarize_equipment(records, energy.mode)
        task_report = {
            "report_date": start_date.isoformat(),
            "period_start": start_date.isoformat(),
            "period_end": end_date.isoformat(),
            "records": to_primitive(records),
            "summary": to_primitive(summary),
            "units": energy.units,
            "equipment_scope": [self.settings.report_machine_no],
            "source_task_count": len(source.records),
            "excluded_task_count": len(source.records) - len(records),
            "energy_notice": energy.notice,
            "warning": "",
            "configuration_error": None if energy.mode != "unavailable" else energy.notice,
            "data_updated_at": to_primitive(source.data_updated_at),
        }
        period_report = {
            "period_start": start_date.isoformat(),
            "period_end": end_date.isoformat(),
            "rows": to_primitive(daily_rows),
            "summary": {
                "electricity": self._energy_channel(
                    records, "electricity_consumption"
                ),
                "gas": self._energy_channel(records, "gas_consumption"),
            },
            "total_task_count": len(records),
            "total_work_time_seconds": to_primitive(summary["total_work_time"]),
            "saved_day_count": 0,
            "elapsed_day_count": sum(not row["is_future"] for row in daily_rows),
            "calendar_day_count": len(daily_rows),
            "units": energy.units,
            "equipment_scope": [self.settings.report_machine_no],
        }
        return {"task_report": task_report, "period_report": period_report}
