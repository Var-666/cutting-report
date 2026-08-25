from __future__ import annotations

from calendar import monthrange
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from threading import Lock
from time import monotonic

from app.config import Settings
from app.database.sqlserver import SqlServerClient
from app.database.excel_task import ExcelTaskDataSource
from app.models.domain import (
    LeadershipPeriodRecord,
    LeadershipReportResult,
    ProjectDistributionRecord,
)


ZERO = Decimal("0")
METER_PRECISION = Decimal("0.001")


def _decimal(value: object) -> Decimal:
    if value is None or value == "":
        return ZERO
    return Decimal(str(value))


def _date(value: object) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _previous_month(month_start: date, months: int) -> date:
    absolute_month = month_start.year * 12 + month_start.month - 1 - months
    return date(absolute_month // 12, absolute_month % 12 + 1, 1)


class LeadershipReportService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = SqlServerClient(settings)
        self.excel = ExcelTaskDataSource(settings.task_excel_path)
        self.query_path = (
            Path(__file__).resolve().parents[1]
            / "database"
            / "queries"
            / "leadership_report.sql"
        )
        self.project_query_path = (
            Path(__file__).resolve().parents[1]
            / "database"
            / "queries"
            / "leadership_projects.sql"
        )
        self._cache_lock = Lock()
        self._month_locks: dict[date, Lock] = {}
        self._cache: dict[
            date, tuple[float, float, LeadershipReportResult]
        ] = {}

    def fetch(
        self, report_month: date, *, force: bool = False
    ) -> LeadershipReportResult:
        month_start = report_month.replace(day=1)
        cache_seconds = self.settings.leadership_report_cache_seconds
        if cache_seconds <= 0:
            return self._fetch_uncached(month_start)

        requested_at = monotonic()
        with self._cache_lock:
            cached = self._cache.get(month_start)
            if not force and cached and cached[0] > requested_at:
                return cached[2]
            month_lock = self._month_locks.setdefault(month_start, Lock())

        # 同一月份只允许一个线程查询。等待中的普通请求，以及在同一次
        # 强制刷新期间到达的刷新请求，直接复用先完成的结果。
        with month_lock:
            now = monotonic()
            with self._cache_lock:
                cached = self._cache.get(month_start)
                cache_is_fresh = cached and cached[0] > now
                refreshed_after_request = cached and cached[1] >= requested_at
                if (not force and cache_is_fresh) or refreshed_after_request:
                    return cached[2]

            result = self._fetch_uncached(month_start)
            created_at = monotonic()
            with self._cache_lock:
                self._cache[month_start] = (
                    created_at + cache_seconds,
                    created_at,
                    result,
                )
            return result

    def _fetch_uncached(self, month_start: date) -> LeadershipReportResult:
        if self.settings.effective_data_source_mode == "excel":
            return self._excel_data(month_start)
        if self.settings.effective_data_source_mode == "demo":
            return self._demo_data(month_start)

        rows = self.client.fetch_all(
            self.query_path.read_text(encoding="utf-8"),
            (month_start,),
        )
        daily_records = [
            self._record_from_row(row)
            for row in rows
            if row.get("period_type") == "daily"
        ]
        monthly_records = [
            self._record_from_row(row)
            for row in rows
            if row.get("period_type") == "monthly"
        ]
        next_month = _previous_month(month_start, -1)
        project_rows = self.client.fetch_all(
            self.project_query_path.read_text(encoding="utf-8"),
            (month_start, next_month),
        )
        projects = [
            ProjectDistributionRecord(
                project_name=str(row.get("project_name") or "未标注工程"),
                task_count=int(row.get("task_count") or 0),
                cut_length_m=_decimal(row.get("cut_length_m")),
                work_time_seconds=int(_decimal(row.get("work_time_seconds"))),
            )
            for row in project_rows
        ]
        return self._result(
            month_start=month_start,
            source_mode="sqlserver",
            daily_records=daily_records,
            monthly_records=monthly_records,
            project_distribution=projects,
        )

    def _excel_data(self, month_start: date) -> LeadershipReportResult:
        next_month = _previous_month(month_start, -1)
        range_start = _previous_month(month_start, 12)
        rows = self.excel.completed_rows(start=range_start, end=next_month)
        daily: dict[date, dict[str, object]] = {}
        monthly: dict[date, dict[str, object]] = {}
        projects: dict[str, dict[str, object]] = {}

        def add(target: dict[date, dict[str, object]], key: date, row: dict) -> None:
            bucket = target.setdefault(
                key,
                {
                    "task_count": 0,
                    "cut_length_m": ZERO,
                    "groove_length_m": ZERO,
                    "work_time_seconds": 0,
                    "remark_count": 0,
                },
            )
            normalized = self.excel.normalized(row)
            bucket["task_count"] += 1
            bucket["cut_length_m"] += normalized["cut_length"]
            bucket["groove_length_m"] += normalized["groove_length"]
            bucket["work_time_seconds"] += int(normalized["work_time"])
            if normalized["remark"]:
                bucket["remark_count"] += 1

        for row in rows:
            production_date = self.excel.production_date(row)
            if production_date is None:
                continue
            month_key = production_date.replace(day=1)
            add(monthly, month_key, row)
            if month_key == month_start:
                add(daily, production_date, row)
                normalized = self.excel.normalized(row)
                project_name = normalized["project_name"] or "未标注工程"
                project = projects.setdefault(
                    project_name,
                    {"task_count": 0, "cut_length_m": ZERO, "work_time_seconds": 0},
                )
                project["task_count"] += 1
                project["cut_length_m"] += normalized["cut_length"]
                project["work_time_seconds"] += int(normalized["work_time"])

        def records(source: dict[date, dict[str, object]]) -> list[LeadershipPeriodRecord]:
            return [
                LeadershipPeriodRecord(
                    period_start=period,
                    task_count=int(values["task_count"]),
                    cut_length_m=values["cut_length_m"],
                    groove_length_m=values["groove_length_m"],
                    work_time_seconds=int(values["work_time_seconds"]),
                    remark=(
                        f"含 {values['remark_count']} 条任务备注"
                        if values["remark_count"]
                        else None
                    ),
                )
                for period, values in source.items()
            ]

        return self._result(
            month_start=month_start,
            source_mode="excel",
            daily_records=records(daily),
            monthly_records=records(monthly),
            project_distribution=[
                ProjectDistributionRecord(
                    project_name=name,
                    task_count=int(values["task_count"]),
                    cut_length_m=values["cut_length_m"],
                    work_time_seconds=int(values["work_time_seconds"]),
                )
                for name, values in sorted(
                    projects.items(),
                    key=lambda item: (-int(item[1]["task_count"]), item[0]),
                )
            ],
        )

    @staticmethod
    def _record_from_row(row: dict) -> LeadershipPeriodRecord:
        return LeadershipPeriodRecord(
            period_start=_date(row["period_start"]),
            task_count=int(row.get("task_count") or 0),
            cut_length_m=_decimal(row.get("cut_length_m")),
            groove_length_m=_decimal(row.get("groove_length_m")),
            work_time_seconds=int(_decimal(row.get("work_time_seconds"))),
            remark=row.get("remark"),
        )

    def _result(
        self,
        *,
        month_start: date,
        source_mode: str,
        daily_records: list[LeadershipPeriodRecord],
        monthly_records: list[LeadershipPeriodRecord],
        project_distribution: list[ProjectDistributionRecord] | None = None,
    ) -> LeadershipReportResult:
        total = LeadershipPeriodRecord(
            period_start=month_start,
            task_count=sum(item.task_count for item in daily_records),
            cut_length_m=sum((item.cut_length_m for item in daily_records), ZERO),
            groove_length_m=sum((item.groove_length_m for item in daily_records), ZERO),
            work_time_seconds=sum(item.work_time_seconds for item in daily_records),
            remark=None,
        )
        monthly_by_period = {item.period_start: item for item in monthly_records}
        year_ago_total = monthly_by_period.get(_previous_month(month_start, 12))
        complete_months = [
            monthly_by_period.get(
                period_start,
                LeadershipPeriodRecord(
                    period_start=period_start,
                    task_count=0,
                    cut_length_m=ZERO,
                    groove_length_m=ZERO,
                    work_time_seconds=0,
                    remark=None,
                ),
            )
            for period_start in (
                _previous_month(month_start, offset)
                for offset in range(0, 12)
            )
        ]
        twelve_month_total = LeadershipPeriodRecord(
            period_start=month_start,
            task_count=sum(item.task_count for item in complete_months),
            cut_length_m=sum((item.cut_length_m for item in complete_months), ZERO),
            groove_length_m=sum(
                (item.groove_length_m for item in complete_months), ZERO
            ),
            work_time_seconds=sum(item.work_time_seconds for item in complete_months),
            remark=None,
        )
        return LeadershipReportResult(
            report_month=month_start,
            source_mode=source_mode,
            daily_records=sorted(
                daily_records, key=lambda item: item.period_start, reverse=True
            ),
            monthly_records=complete_months,
            current_month_total=total,
            twelve_month_total=twelve_month_total,
            year_ago_total=year_ago_total,
            project_distribution=project_distribution or [],
        )

    def _demo_data(self, month_start: date) -> LeadershipReportResult:
        last_day = monthrange(month_start.year, month_start.month)[1]
        demo_rows = [
            (1, 24, "969.272", 7595),
            (2, 19, "832.514", 6840),
            (4, 27, "1124.806", 9238),
            (5, 16, "708.335", 5774),
            (8, 31, "1286.920", 10462),
            (11, 22, "914.680", 7316),
            (14, 25, "1038.492", 8465),
            (18, 18, "776.158", 6130),
            (22, 29, "1195.743", 9578),
            (25, 21, "867.309", 6992),
        ]
        daily_records = [
            LeadershipPeriodRecord(
                period_start=month_start.replace(day=day),
                task_count=task_count,
                cut_length_m=Decimal(cut_length),
                groove_length_m=ZERO,
                work_time_seconds=work_seconds,
                remark=None,
            )
            for day, task_count, cut_length, work_seconds in demo_rows
            if day <= last_day
        ]
        current_total = LeadershipPeriodRecord(
            period_start=month_start,
            task_count=sum(item.task_count for item in daily_records),
            cut_length_m=sum((item.cut_length_m for item in daily_records), ZERO),
            groove_length_m=sum(
                (item.groove_length_m for item in daily_records), ZERO
            ),
            work_time_seconds=sum(item.work_time_seconds for item in daily_records),
            remark=None,
        )

        monthly_records: list[LeadershipPeriodRecord] = []
        for offset in range(12, -1, -1):
            period_start = _previous_month(month_start, offset)
            if offset == 0:
                monthly_records.append(current_total)
                continue
            scale = Decimal("0.72") + Decimal(11 - offset) * Decimal("0.031")
            monthly_records.append(
                LeadershipPeriodRecord(
                    period_start=period_start,
                    task_count=max(1, int(Decimal(current_total.task_count) * scale)),
                    cut_length_m=(current_total.cut_length_m * scale).quantize(
                        METER_PRECISION, rounding=ROUND_HALF_UP
                    ),
                    groove_length_m=ZERO,
                    work_time_seconds=int(
                        Decimal(current_total.work_time_seconds) * scale
                    ),
                    remark=None,
                )
            )
        return self._result(
            month_start=month_start,
            source_mode="demo",
            daily_records=daily_records,
            monthly_records=monthly_records,
            project_distribution=[
                ProjectDistributionRecord(
                    project_name="DEMO-A",
                    task_count=current_total.task_count * 55 // 100,
                    cut_length_m=(current_total.cut_length_m * Decimal("0.55")).quantize(METER_PRECISION),
                    work_time_seconds=current_total.work_time_seconds * 55 // 100,
                ),
                ProjectDistributionRecord(
                    project_name="DEMO-B",
                    task_count=current_total.task_count - current_total.task_count * 55 // 100,
                    cut_length_m=(current_total.cut_length_m * Decimal("0.45")).quantize(METER_PRECISION),
                    work_time_seconds=current_total.work_time_seconds - current_total.work_time_seconds * 55 // 100,
                ),
            ],
        )
