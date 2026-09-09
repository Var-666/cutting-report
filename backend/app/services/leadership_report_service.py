from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
from pathlib import Path
from threading import Lock
from time import monotonic
from zoneinfo import ZoneInfo

from app.config import Settings
from app.database.leadership_snapshots import (
    LeadershipMonthSnapshot,
    LeadershipSnapshotRepository,
)
from app.database.sqlserver import SqlServerClient
from app.database.excel_task import ExcelTaskDataSource
from app.models.domain import (
    LeadershipPeriodRecord,
    LeadershipReportResult,
    LeadershipSnapshotState,
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
    SNAPSHOT_VERSION = 2

    def __init__(
        self,
        settings: Settings,
        snapshots: LeadershipSnapshotRepository | None = None,
    ) -> None:
        self.settings = settings
        self.client = SqlServerClient(settings)
        self.excel = ExcelTaskDataSource(settings.task_excel_path)
        self.snapshots = snapshots
        self.query_path = (
            Path(__file__).resolve().parents[1]
            / "database"
            / "queries"
            / "leadership_report.sql"
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
        if (
            self.snapshots is not None
            and self.settings.effective_data_source_mode == "sqlserver"
        ):
            if force:
                self.refresh(
                    month_start,
                    bootstrap=self.needs_bootstrap(month_start),
                )
            return self._fetch_snapshot_report(month_start)

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

        range_start = _previous_month(month_start, 12)
        next_month = _previous_month(month_start, -1)
        snapshots = self._query_snapshots(range_start, next_month)
        return self._compose_snapshot_report(
            month_start, snapshots, cached=False
        )

    def needs_bootstrap(self, report_month: date) -> bool:
        if self.snapshots is None:
            return False
        months = self._window_months(report_month.replace(day=1))
        return len(self._load_snapshots(months)) != len(months)

    def refresh(self, report_month: date, *, bootstrap: bool = False) -> int:
        month_start = report_month.replace(day=1)
        range_start = _previous_month(month_start, 12) if bootstrap else month_start
        range_end = _previous_month(month_start, -1)
        snapshots = self._query_snapshots(range_start, range_end)
        if self.snapshots is not None:
            self.snapshots.upsert_many(list(snapshots.values()))
        with self._cache_lock:
            self._cache.clear()
        return len(snapshots)

    def _fetch_snapshot_report(self, month_start: date) -> LeadershipReportResult:
        cache_seconds = self.current_refresh_seconds
        now_tick = monotonic()
        with self._cache_lock:
            cached = self._cache.get(month_start)
            if cached and cached[0] > now_tick:
                return cached[2]

        snapshots = self._load_snapshots(self._window_months(month_start))
        result = self._compose_snapshot_report(month_start, snapshots, cached=True)
        # Missing or expired snapshots are checked again quickly while the worker
        # refreshes them. Fresh results retain the normal in-process TTL.
        selected = snapshots.get(month_start)
        remaining_seconds = (
            int(
                (
                    selected.expires_at
                    - datetime.now(ZoneInfo(self.settings.timezone))
                ).total_seconds()
            )
            if selected is not None
            else 0
        )
        ttl = (
            2
            if result.snapshot.stale
            else min(cache_seconds, max(1, remaining_seconds))
        )
        created_at = monotonic()
        with self._cache_lock:
            self._cache[month_start] = (created_at + ttl, created_at, result)
        return result

    def _load_snapshots(
        self, months: list[date]
    ) -> dict[date, LeadershipMonthSnapshot]:
        if self.snapshots is None:
            return {}
        return self.snapshots.get_many(
            months,
            source_fingerprint=self._source_fingerprint(),
            snapshot_version=self.SNAPSHOT_VERSION,
        )

    def _query_snapshots(
        self, range_start: date, range_end: date
    ) -> dict[date, LeadershipMonthSnapshot]:
        started_at = monotonic()
        result_sets = self.client.fetch_result_sets(
            self.query_path.read_text(encoding="utf-8"),
            (self.settings.report_machine_no, range_start, range_end),
        )
        if len(result_sets) != 2:
            raise RuntimeError("领导报表批量查询未返回预期的两个结果集")

        daily_by_month: dict[date, list[LeadershipPeriodRecord]] = {}
        remark_counts_by_month: dict[date, int] = {}
        for row in result_sets[0]:
            period_start = _date(row["period_start"])
            remark_count = int(row.get("remark_count") or 0)
            report_month = period_start.replace(day=1)
            remark_counts_by_month[report_month] = (
                remark_counts_by_month.get(report_month, 0) + remark_count
            )
            daily_by_month.setdefault(report_month, []).append(
                LeadershipPeriodRecord(
                    period_start=period_start,
                    task_count=int(row.get("task_count") or 0),
                    cut_length_m=_decimal(row.get("cut_length_m")),
                    groove_length_m=_decimal(row.get("groove_length_m")),
                    work_time_seconds=int(_decimal(row.get("work_time_seconds"))),
                    remark=(
                        f"含 {remark_count} 条任务备注" if remark_count else None
                    ),
                )
            )

        projects_by_month: dict[date, list[ProjectDistributionRecord]] = {}
        for row in result_sets[1]:
            period_start = _date(row["period_start"]).replace(day=1)
            projects_by_month.setdefault(period_start, []).append(
                ProjectDistributionRecord(
                    project_name=str(row.get("project_name") or "未标注工程"),
                    task_count=int(row.get("task_count") or 0),
                    cut_length_m=_decimal(row.get("cut_length_m")),
                    work_time_seconds=int(_decimal(row.get("work_time_seconds"))),
                )
            )

        now = datetime.now(ZoneInfo(self.settings.timezone))
        current_month = now.date().replace(day=1)
        query_duration_ms = round((monotonic() - started_at) * 1000)
        snapshots: dict[date, LeadershipMonthSnapshot] = {}
        for report_month in self._months_between(range_start, range_end):
            daily = daily_by_month.get(report_month, [])
            ttl_seconds = (
                self.current_refresh_seconds
                if report_month >= current_month
                else self.history_refresh_seconds
            )
            snapshots[report_month] = LeadershipMonthSnapshot(
                report_month=report_month,
                daily_records=sorted(
                    daily, key=lambda item: item.period_start, reverse=True
                ),
                project_distribution=sorted(
                    projects_by_month.get(report_month, []),
                    key=lambda item: (-item.task_count, item.project_name),
                ),
                total=self._total(
                    report_month,
                    daily,
                    remark_count=remark_counts_by_month.get(report_month, 0),
                ),
                data_as_of=now,
                refreshed_at=now,
                expires_at=now + timedelta(seconds=ttl_seconds),
                query_duration_ms=query_duration_ms,
                source_fingerprint=self._source_fingerprint(),
                snapshot_version=self.SNAPSHOT_VERSION,
            )
        return snapshots

    def _compose_snapshot_report(
        self,
        month_start: date,
        snapshots: dict[date, LeadershipMonthSnapshot],
        *,
        cached: bool,
    ) -> LeadershipReportResult:
        months = self._window_months(month_start)
        selected = snapshots.get(month_start)
        monthly_records = [
            snapshots[period].total if period in snapshots else self._empty(period)
            for period in months[:12]
        ]
        now = datetime.now(ZoneInfo(self.settings.timezone))
        stale = (
            selected is None
            or selected.expires_at <= now
            or any(period not in snapshots for period in months)
        )
        return self._result(
            month_start=month_start,
            source_mode="sqlserver",
            daily_records=selected.daily_records if selected else [],
            monthly_records=monthly_records
            + ([snapshots[months[12]].total] if months[12] in snapshots else []),
            project_distribution=(selected.project_distribution if selected else []),
            snapshot=LeadershipSnapshotState(
                data_as_of=selected.data_as_of if selected else None,
                stale=stale,
                cached=cached,
                source="sqlite" if cached else "database",
                query_duration_ms=(selected.query_duration_ms if selected else None),
            ),
        )

    def _source_fingerprint(self) -> str:
        connection = self.settings.sqlserver_connection("production")
        identity = "|".join(
            (
                self.settings.effective_data_source_mode,
                str(connection["host"]),
                str(connection["port"]),
                str(connection["database"]),
                str(connection["username"]),
                self.settings.report_machine_no.casefold(),
                str(self.SNAPSHOT_VERSION),
            )
        )
        return sha256(identity.encode("utf-8")).hexdigest()

    @property
    def current_refresh_seconds(self) -> int:
        value = self.settings.leadership_report_cache_seconds
        return value if value > 0 else 300

    @property
    def history_refresh_seconds(self) -> int:
        value = self.settings.leadership_report_history_cache_seconds
        return value if value > 0 else 86400

    @staticmethod
    def _months_between(range_start: date, range_end: date) -> list[date]:
        months: list[date] = []
        current = range_start.replace(day=1)
        while current < range_end:
            months.append(current)
            current = _previous_month(current, -1)
        return months

    @staticmethod
    def _window_months(month_start: date) -> list[date]:
        return [_previous_month(month_start, offset) for offset in range(13)]

    @staticmethod
    def _empty(period_start: date) -> LeadershipPeriodRecord:
        return LeadershipPeriodRecord(
            period_start=period_start,
            task_count=0,
            cut_length_m=ZERO,
            groove_length_m=ZERO,
            work_time_seconds=0,
            remark=None,
        )

    @staticmethod
    def _total(
        month_start: date,
        daily_records: list[LeadershipPeriodRecord],
        *,
        remark_count: int = 0,
    ) -> LeadershipPeriodRecord:
        return LeadershipPeriodRecord(
            period_start=month_start,
            task_count=sum(item.task_count for item in daily_records),
            cut_length_m=sum((item.cut_length_m for item in daily_records), ZERO),
            groove_length_m=sum(
                (item.groove_length_m for item in daily_records), ZERO
            ),
            work_time_seconds=sum(item.work_time_seconds for item in daily_records),
            remark=f"含 {remark_count} 条任务备注" if remark_count else None,
        )

    def _excel_data(self, month_start: date) -> LeadershipReportResult:
        next_month = _previous_month(month_start, -1)
        range_start = _previous_month(month_start, 12)
        rows = self.excel.completed_rows(
            start=range_start,
            end=next_month,
            machine_no=self.settings.report_machine_no,
        )
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
        snapshot: LeadershipSnapshotState | None = None,
    ) -> LeadershipReportResult:
        total = self._total(month_start, daily_records)
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
            snapshot=snapshot
            or LeadershipSnapshotState(
                data_as_of=datetime.now(ZoneInfo(self.settings.timezone)),
                source=source_mode,
            ),
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
