from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Iterable
from zoneinfo import ZoneInfo

from app.config import Settings
from app.modules.cutting.confirmations import ConfirmationRepository
from app.models.domain import (
    EquipmentRecord,
    production_day,
    LeadershipPeriodRecord,
    LeadershipReportResult,
    LeadershipSnapshotState,
    ProjectDistributionRecord,
    QueryBundle,
    ReportFilter,
    WorkshopRecord,
    to_primitive,
)
from app.modules.energy.periods import (
    channel_result,
    restore_task_energy_records,
    total_channel,
)
from app.modules.cutting.equipment import summarize_equipment
from app.modules.cutting.service import EquipmentQueryResult, QueryService
from app.modules.reports.closing_comparison import ClosingGrouping, Totals, Window, closing_comparison, sum_closing_totals


MAX_REPORT_RANGE_DAYS = 366
MAX_LIVE_ENERGY_RANGE_DAYS = 31


def filter_workshop_records(records: list[WorkshopRecord], *, project_code: str | None = None,
                            task_id: str | None = None, confirmation_status: str = 'all',
                            ship_no: str | None = None) -> list[WorkshopRecord]:
    return [row for row in records if (not project_code or row.project_code == project_code)
            and (not task_id or row.task_id == task_id)
            and (not ship_no or row.ship_no == ship_no)
            and (confirmation_status == 'all' or bool(row.confirmed_by) == (confirmation_status == 'confirmed'))]


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
        confirmations: ConfirmationRepository,
        energy_period_service,
    ) -> None:
        self.settings = settings
        self.query_service = query_service
        self.confirmations = confirmations
        self.energy_period_service = energy_period_service

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

    def summary_bundle(self, start_date: date, end_date: date, *, force: bool = False) -> QueryBundle:
        return self.query_service.fetch_summary_data(
            report_filter(start_date, end_date), force=force
        )

    def workshop_bundle(
        self,
        start_date: date,
        end_date: date,
        *,
        project_code: str | None = None,
        ship_no: str | None = None,
        force: bool = False,
        business_days: bool = False,
    ) -> QueryBundle:
        return self.query_service.fetch_workshop_data(
            report_filter(
                start_date,
                end_date,
                project_code=project_code,
                ship_no=ship_no,
            ),
            force=force,
            business_days=business_days,
        )

    def leadership(
        self, start_date: date, end_date: date, *, force: bool = False
    ) -> LeadershipReportResult:
        bundle = self.summary_bundle(start_date, end_date, force=force)
        by_day: dict[date, list] = defaultdict(list)
        by_month: dict[date, list] = defaultdict(list)
        by_project: dict[str, list] = defaultdict(list)
        for row in bundle.summary_records:
            stamp = production_day(row)
            if stamp is None:
                continue
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

    def cutting_closing_comparison(
        self, start_date: date, end_date: date, reference_at: datetime, *,
        project_code: str | None = None, ship_no: str | None = None, include_meter_energy: bool = False,
        grouping: ClosingGrouping = "day_week_month",
        business_day_totals: Callable[[date], Totals] | None = None,
    ) -> list[dict]:
        metrics = ("task_count", "cut_length_m", "groove_length_m", "work_time_seconds")
        meter_coverage = {}

        def load(windows: tuple[Window, ...]) -> dict[Window, Totals]:
            start = min(left for left, _ in windows)
            end = max(right for _, right in windows)
            buckets = self.query_service.fetch_cutting_closing_totals(start, end, project_code=project_code, ship_no=ship_no)
            totals = {window: sum_closing_totals(window, buckets, metrics) for window in windows}
            if business_day_totals is not None:
                business_day = windows[0]
                totals[business_day] = business_day_totals(business_day[1].date())
            if include_meter_energy:
                meter_coverage.update(self.energy_period_service.closing_meter_coverage(windows))
                for window, channels in meter_coverage.items():
                    totals[window].update({
                        name: channel["value"] if channel["valid_count"] == channel["expected_count"] else None
                        for name, channel in channels.items()
                    })
            return totals

        columns = closing_comparison(start_date, end_date, reference_at, self.settings.timezone, load, grouping=grouping)
        if meter_coverage:
            for column in columns:
                column["meter_coverage"] = meter_coverage[(column["start"], column["end"])]
        return columns

    def workshop_records(
        self,
        start_date: date,
        end_date: date,
        *,
        project_code: str | None = None,
        ship_no: str | None = None,
        force: bool = False,
        business_days: bool = False,
    ) -> tuple[QueryBundle, list[WorkshopRecord]]:
        bundle = self.workshop_bundle(
            start_date, end_date, project_code=project_code, ship_no=ship_no,
            force=force, business_days=business_days,
        )
        return bundle, self.with_workshop_confirmations(bundle.workshop_records)

    def with_workshop_confirmations(self, records: list[WorkshopRecord]) -> list[WorkshopRecord]:
        confirmations = self.confirmations.get_confirmations([str(row.task_id) for row in records])
        rows = []
        for row in records:
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
                    confirmed_by_company_no=(
                        confirmation["worker_company_no"] if confirmation else None
                    ),
                    confirmed_at=(
                        datetime.fromisoformat(confirmation["confirmed_at"])
                        if confirmation
                        else None
                    ),
                )
            )
        return rows

    @staticmethod
    def _energy_channel(records: list[EquipmentRecord], field: str) -> dict:
        status_field = field.replace("_consumption", "_data_status")
        return total_channel(
            [{field: channel_result(getattr(row, field), getattr(row, status_field))}
             for row in records],
            field,
        )

    def equipment_tasks(
        self, start_date: date, end_date: date, *, force: bool = False
    ) -> tuple[EquipmentQueryResult, dict]:
        """Share the authoritative task query/calculation with report exports."""
        validate_report_range(start_date, end_date)
        if (end_date - start_date).days >= MAX_LIVE_ENERGY_RANGE_DAYS:
            raise ValueError(
                f"设备任务能耗查询区间最长为 {MAX_LIVE_ENERGY_RANGE_DAYS} 天；"
                "近 12 个月趋势请使用默认月度汇总"
            )
        filters = report_filter(start_date, end_date)
        source = self.query_service.fetch_equipment_data(filters, force=force)
        task_report = self.energy_period_service.get_task_range(
            start_date, end_date, source.records, force=force
        )
        return source, task_report

    def equipment(self, start_date: date, end_date: date, *, force: bool = False) -> dict:
        source, task_report = self.equipment_tasks(start_date, end_date, force=force)
        records = restore_task_energy_records(source.records, task_report)
        mode = task_report["summary"]["energy_data_mode"]
        by_day: dict[date, list[EquipmentRecord]] = defaultdict(list)
        for row in records:
            stamp = production_day(row)
            if stamp is not None:
                by_day[stamp].append(row)

        daily_rows = []
        today = datetime.now(ZoneInfo(self.settings.timezone)).date()
        stamp = start_date
        while stamp <= end_date:
            rows = by_day.get(stamp, [])
            summary = summarize_equipment(rows, mode)
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

        summary = task_report["summary"]
        task_report.update({
            "data_updated_at": to_primitive(source.data_updated_at),
        })
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
            "total_work_time_seconds": summary["total_work_time"],
            "saved_day_count": 0,
            "elapsed_day_count": sum(not row["is_future"] for row in daily_rows),
            "calendar_day_count": len(daily_rows),
            "units": task_report["units"],
            "equipment_scope": task_report["equipment_scope"],
        }
        return {"task_report": task_report, "period_report": period_report}
