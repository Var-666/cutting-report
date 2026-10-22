from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Generic, TypeVar
from zoneinfo import ZoneInfo

from app.config import Settings
from app.database.sqlserver import SqlServerClient, SqlServerConfigurationError
from app.models.domain import (
    EquipmentRecord,
    QueryBundle,
    ReportFilter,
    SummaryBaseRecord,
    WorkshopRecord,
)


@dataclass(frozen=True, slots=True)
class EquipmentQueryResult:
    records: list[EquipmentRecord]
    data_updated_at: datetime | None = None


_CachedResult = TypeVar("_CachedResult")


class _QueryCache(Generic[_CachedResult]):
    """Bounded query cache; source reads never hold the publication lock."""

    def __init__(self) -> None:
        self._entries: dict[tuple, tuple[float, float, _CachedResult]] = {}
        self._lock = Lock()
        self._query_locks = [Lock() for _ in range(32)]

    def read(
        self, key: tuple, loader: Callable[[], _CachedResult], *, ttl: int, force: bool = False
    ) -> _CachedResult:
        if ttl <= 0:
            return loader()
        requested_at = monotonic()
        with self._lock:
            cached = self._entries.get(key)
            if not force and cached and cached[0] > requested_at:
                return cached[2]
        query_lock = self._query_locks[hash(key) % len(self._query_locks)]
        with query_lock:
            now = monotonic()
            with self._lock:
                cached = self._entries.get(key)
                cache_is_fresh = cached and cached[0] > now
                refreshed_after_request = cached and cached[1] >= requested_at
                if (not force and cache_is_fresh) or refreshed_after_request:
                    return cached[2]
            result = loader()
            created_at = monotonic()
            with self._lock:
                self._entries = {
                    cache_key: value for cache_key, value in self._entries.items()
                    if value[0] > created_at
                }
                self._entries[key] = (created_at + ttl, created_at, result)
                while len(self._entries) > 128:
                    self._entries.pop(next(iter(self._entries)))
            return result

def _date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


class QueryService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = SqlServerClient(settings)
        self.query_dir = Path(__file__).resolve().parents[1] / "database" / "queries"
        self._equipment_cache = _QueryCache[EquipmentQueryResult]()
        self._bundle_cache = _QueryCache[QueryBundle]()
        self._equipment_metrics_cache_lock = Lock()
        self._equipment_metrics_cache: dict[
            tuple[date, date], tuple[float, dict[str, dict[str, Decimal | int]]]
        ] = {}
        self._equipment_metrics_locks = [Lock() for _ in range(16)]
        self._latest_date_lock = Lock()
        self._latest_date_cache: dict[date, tuple[float, date | None]] = {}
        self._workshop_cache_lock = Lock()
        self._workshop_prepare_locks = [Lock() for _ in range(16)]
        self._workshop_cache: dict[date, tuple[float, QueryBundle]] = {}

    def prepare_workshop_data(self, report_date: date) -> QueryBundle:
        """Read-only, short-lived warmup; does not create reports or confirmations."""
        prepare_lock = self._workshop_prepare_locks[hash(report_date) % len(self._workshop_prepare_locks)]
        with prepare_lock:
            with self._workshop_cache_lock:
                now = monotonic()
                cached = self._workshop_cache.get(report_date)
                if cached and cached[0] > now:
                    return cached[1]
                self._workshop_cache = {
                    key: value for key, value in self._workshop_cache.items() if value[0] > now
                }
            # Source reads run outside the cache publication lock.
            bundle = self.fetch_workshop_data(ReportFilter(report_date=report_date))
            ttl = min(60, self.settings.report_data_cache_seconds)
            if ttl > 0 and bundle.workshop_records:
                with self._workshop_cache_lock:
                    self._workshop_cache[report_date] = (monotonic() + ttl, bundle)
                    while len(self._workshop_cache) > 32:
                        self._workshop_cache.pop(next(iter(self._workshop_cache)))
            return bundle

    def fetch_report_data(self, filters: ReportFilter) -> QueryBundle:
        if self.settings.effective_data_source_mode == "demo":
            return self._demo_data(filters)
        summary = self.fetch_summary_data(filters)
        workshop = self.fetch_workshop_data(filters)
        equipment = self.fetch_equipment_data(filters)
        updated = [
            value
            for value in (
                summary.data_updated_at,
                workshop.data_updated_at,
                equipment.data_updated_at,
            )
            if value is not None
        ]
        return QueryBundle(
            summary.summary_records,
            workshop.workshop_records,
            equipment.records,
            max(updated) if updated else None,
        )

    @staticmethod
    def _cache_key(kind: str, filters: ReportFilter) -> tuple:
        return (
            kind,
            filters.start_date,
            filters.end_date,
            filters.project_code,
            filters.ship_no,
        )

    def _fetch_cached_bundle(
        self,
        kind: str,
        filters: ReportFilter,
        loader: Callable[[ReportFilter], QueryBundle],
        *,
        force: bool = False,
    ) -> QueryBundle:
        return self._bundle_cache.read(
            self._cache_key(kind, filters), lambda: loader(filters),
            ttl=self.settings.report_data_cache_seconds, force=force,
        )

    def fetch_summary_data(
        self, filters: ReportFilter, *, force: bool = False
    ) -> QueryBundle:
        if self.settings.effective_data_source_mode == "sqlserver":
            return self._fetch_cached_bundle(
                "summary", filters, self._sqlserver_summary_data, force=force
            )
        bundle = self.fetch_report_data(filters)
        return QueryBundle(bundle.summary_records, [], [], bundle.data_updated_at)

    def fetch_workshop_data(
        self, filters: ReportFilter, *, force: bool = False
    ) -> QueryBundle:
        if self.settings.effective_data_source_mode == "sqlserver":
            return self._fetch_cached_bundle(
                "workshop", filters, self._sqlserver_workshop_data, force=force
            )
        bundle = self.fetch_report_data(filters)
        return QueryBundle([], bundle.workshop_records, [], bundle.data_updated_at)

    def fetch_equipment_data(
        self, filters: ReportFilter, *, force: bool = False
    ) -> EquipmentQueryResult:
        """Fetch only equipment rows and coalesce repeated per-day requests."""
        return self._equipment_cache.read(
            self._cache_key("equipment", filters), lambda: self._fetch_equipment_uncached(filters),
            ttl=self.settings.report_data_cache_seconds, force=force,
        )

    def fetch_equipment_month_work_times(
        self, report_month: date
    ) -> dict[str, Decimal]:
        """Return configured-machine cutting seconds grouped by production day."""
        return {
            report_date: values["work_time_seconds"]
            for report_date, values in self.fetch_equipment_month_metrics(report_month).items()
        }

    def fetch_equipment_month_metrics(
        self, report_month: date
    ) -> dict[str, dict[str, Decimal | int]]:
        """Return configured-machine task counts and cutting seconds by day."""
        month_start = report_month.replace(day=1)
        month_end = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
        return self.fetch_equipment_range_metrics(month_start, month_end)

    def fetch_equipment_range_metrics(
        self, start_date: date, end_exclusive: date
    ) -> dict[str, dict[str, Decimal | int]]:
        """Return cached daily equipment metrics for a range."""
        if end_exclusive <= start_date:
            return {}
        cache_seconds = self.settings.report_data_cache_seconds
        if cache_seconds <= 0:
            return self._fetch_equipment_range_metrics_uncached(
                start_date, end_exclusive
            )
        key = (start_date, end_exclusive)
        with self._equipment_metrics_locks[
            hash(key) % len(self._equipment_metrics_locks)
        ]:
            now = monotonic()
            with self._equipment_metrics_cache_lock:
                cached = self._equipment_metrics_cache.get(key)
                if cached and cached[0] > now:
                    return cached[1]
            result = self._fetch_equipment_range_metrics_uncached(
                start_date, end_exclusive
            )
            with self._equipment_metrics_cache_lock:
                self._equipment_metrics_cache = {
                    cache_key: value
                    for cache_key, value in self._equipment_metrics_cache.items()
                    if value[0] > now
                }
                self._equipment_metrics_cache[key] = (
                    monotonic() + cache_seconds,
                    result,
                )
                while len(self._equipment_metrics_cache) > 32:
                    self._equipment_metrics_cache.pop(
                        next(iter(self._equipment_metrics_cache))
                    )
            return result

    def _fetch_equipment_range_metrics_uncached(
        self, start_date: date, end_exclusive: date
    ) -> dict[str, dict[str, Decimal | int]]:
        """Return daily equipment metrics for a range using one source query."""
        allowed = {
            value.strip().casefold()
            for value in self.settings.energy_report_equipment_ids
            if value and value.strip()
        }
        allowed &= {self.settings.report_machine_no.casefold()}
        if not allowed:
            return {}

        totals: dict[str, dict[str, Decimal | int]] = {}

        def add(
            report_date: object,
            equipment_id: object,
            work_time: object,
            task_count: object = 1,
        ) -> None:
            if not equipment_id or str(equipment_id).strip().casefold() not in allowed:
                return
            if isinstance(report_date, datetime):
                stamp = report_date.date()
            elif isinstance(report_date, date):
                stamp = report_date
            else:
                try:
                    stamp = date.fromisoformat(str(report_date)[:10])
                except ValueError:
                    return
            value = _decimal(work_time) or Decimal("0")
            if value < 0:
                return
            key = stamp.isoformat()
            bucket = totals.setdefault(
                key, {"work_time_seconds": Decimal("0"), "task_count": 0}
            )
            bucket["work_time_seconds"] += value
            bucket["task_count"] += max(0, int(task_count or 0))

        if self.settings.effective_data_source_mode == "demo":
            stamp = start_date
            while stamp < end_exclusive:
                for row in self._demo_data(ReportFilter(report_date=stamp)).equipment_records:
                    add(stamp, row.equipment_id, row.work_time)
                stamp += timedelta(days=1)
            return totals

        rows = self.client.fetch_all(
            self._read_query("equipment_month_work_time.sql"),
            (self.settings.report_machine_no, start_date, end_exclusive),
        )
        for row in rows:
            add(
                row.get("report_date"),
                row.get("equipment_id"),
                row.get("work_time_seconds"),
                row.get("task_count"),
            )
        return totals

    def _fetch_equipment_uncached(self, filters: ReportFilter) -> EquipmentQueryResult:
        if self.settings.effective_data_source_mode == "demo":
            bundle = self._demo_data(filters)
            return EquipmentQueryResult(bundle.equipment_records, bundle.data_updated_at)
        return self._sqlserver_equipment_data(filters)

    def _sqlserver_equipment_data(
        self, filters: ReportFilter
    ) -> EquipmentQueryResult:
        timezone = ZoneInfo(self.settings.timezone)
        start = datetime.combine(filters.start_date, time.min, tzinfo=timezone).replace(
            tzinfo=None
        )
        end = datetime.combine(
            filters.end_exclusive, time.min, tzinfo=timezone
        ).replace(tzinfo=None)
        parameters = (
            self.settings.report_machine_no,
            start,
            end,
            filters.project_code,
            filters.project_code,
            filters.ship_no,
            filters.ship_no,
        )
        rows = self.client.fetch_all(
            self._read_query("equipment.sql"), parameters
        )
        records = [
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
                production_date=_date(row.get("production_date")),
                production_end_time=row.get("production_end_time"),
                remark=row.get("remark"),
            )
            for row in rows
        ]
        latest_update = max(
            (row.get("source_updated_at") for row in rows if row.get("source_updated_at")),
            default=None,
        )
        return EquipmentQueryResult(records, latest_update)

    def list_projects(self, filters: ReportFilter) -> list[str]:
        if self.settings.effective_data_source_mode == "demo":
            return ["A001", "A002", "B101"]
        start = filters.start_date
        end = filters.end_exclusive
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
                  AND UPPER(LTRIM(RTRIM([MACHINE_NO]))) = UPPER(?)
            )
            SELECT DISTINCT project_code
            FROM source
            WHERE production_date >= ? AND production_date < ?
              AND project_code IS NOT NULL
            ORDER BY project_code
            """,
            (self.settings.report_machine_no, start, end),
        )
        return [str(row["project_code"]) for row in rows]

    def list_ships(self, filters: ReportFilter) -> list[str]:
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
                  AND UPPER(LTRIM(RTRIM([MACHINE_NO]))) = UPPER(?)
            )
            SELECT DISTINCT project_name
            FROM source
            WHERE production_date >= ? AND production_date < ?
              AND project_name IS NOT NULL
              AND (? IS NULL OR project_name = ?)
            ORDER BY project_name
            """,
            (
                self.settings.report_machine_no,
                filters.start_date,
                filters.end_exclusive,
                filters.project_code,
                filters.project_code,
            ),
        )
        return [str(row["project_name"]) for row in rows]

    def latest_production_date(
        self, on_or_before: date, *, force: bool = False
    ) -> date | None:
        cache_seconds = self.settings.report_data_cache_seconds
        if cache_seconds <= 0:
            return self._latest_production_date_uncached(on_or_before)

        now = monotonic()
        with self._latest_date_lock:
            cached = self._latest_date_cache.get(on_or_before)
            if not force and cached and cached[0] > now:
                return cached[1]
            result = self._latest_production_date_uncached(on_or_before)
            self._latest_date_cache = {
                key: value
                for key, value in self._latest_date_cache.items()
                if value[0] > now
            }
            self._latest_date_cache[on_or_before] = (now + cache_seconds, result)
            while len(self._latest_date_cache) > 128:
                self._latest_date_cache.pop(next(iter(self._latest_date_cache)))
            return result

    def _latest_production_date_uncached(self, on_or_before: date) -> date | None:
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
                  AND UPPER(LTRIM(RTRIM([MACHINE_NO]))) = UPPER(?)
            )
            SELECT MAX(production_date) AS latest_date
            FROM source
            WHERE production_date <= ?
            """,
            (self.settings.report_machine_no, on_or_before),
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

    def _sqlserver_summary_data(self, filters: ReportFilter) -> QueryBundle:
        timezone = ZoneInfo(self.settings.timezone)
        start = datetime.combine(filters.start_date, time.min, tzinfo=timezone).replace(
            tzinfo=None
        )
        end = datetime.combine(
            filters.end_exclusive, time.min, tzinfo=timezone
        ).replace(tzinfo=None)
        parameters = (
            self.settings.report_machine_no,
            start,
            end,
            filters.project_code,
            filters.project_code,
            filters.ship_no,
            filters.ship_no,
        )
        rows = self.client.fetch_all(self._read_query("summary_base.sql"), parameters)
        records = [
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
                production_date=_date(row.get("production_date")),
                source_updated_at=row.get("source_updated_at"),
            )
            for row in rows
        ]
        latest_update = max(
            (row.source_updated_at for row in records if row.source_updated_at),
            default=None,
        )
        return QueryBundle(records, [], [], latest_update)

    def _sqlserver_workshop_data(self, filters: ReportFilter) -> QueryBundle:
        timezone = ZoneInfo(self.settings.timezone)
        start = datetime.combine(filters.start_date, time.min, tzinfo=timezone).replace(
            tzinfo=None
        )
        end = datetime.combine(
            filters.end_exclusive, time.min, tzinfo=timezone
        ).replace(tzinfo=None)
        parameters = (
            self.settings.report_machine_no,
            start,
            end,
            filters.project_code,
            filters.project_code,
            filters.ship_no,
            filters.ship_no,
        )
        rows = self.client.fetch_all(self._read_query("workshop.sql"), parameters)
        records = [
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
                production_date=_date(row.get("production_date")),
            )
            for row in rows
        ]
        latest_update = max(
            (row.get("source_updated_at") for row in rows if row.get("source_updated_at")),
            default=None,
        )
        return QueryBundle([], records, [], latest_update)

    def _demo_data(self, filters: ReportFilter) -> QueryBundle:
        project_names = ["A001", "A001", "A002", "B101"]
        summaries: list[SummaryBaseRecord] = []
        workshops: list[WorkshopRecord] = []
        equipment: list[EquipmentRecord] = []
        timezone = ZoneInfo(self.settings.timezone)
        stamp = filters.start_date
        latest_update = None
        while stamp <= filters.end_date:
            base_time = datetime.combine(stamp, time(8), tzinfo=timezone)
            for index in range(1, 29):
                project_name = project_names[(index - 1) % len(project_names)]
                equipment_id = f"Cutter{((index - 1) % 2) + 1:02d}"
                if equipment_id.casefold() != self.settings.report_machine_no.casefold():
                    continue
                if filters.project_code and filters.project_code != project_name:
                    continue
                if filters.ship_no and filters.ship_no != project_name:
                    continue
                task_id = f"{stamp:%Y%m%d}-{index:03d}"
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
                    production_date=stamp,
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
                        production_date=summary.production_date,
                        remark="演示数据",
                    )
                )
                equipment.append(
                    EquipmentRecord(
                        task_id=task_id,
                        equipment_id=equipment_id,
                        project_code=project_name,
                        ship_no=project_name,
                        segment_name=summary.segment_name,
                        cut_length=cut_length,
                        work_time=work_time,
                        gas_consumption=Decimal("8.5") + Decimal(index) * Decimal("0.42"),
                        electricity_consumption=Decimal("14.2")
                        + Decimal(index) * Decimal("0.61"),
                        production_time=summary.production_time,
                        production_date=summary.production_date,
                        production_end_time=summary.production_time + timedelta(seconds=float(work_time)),
                        remark="演示数据",
                    )
                )
            latest_update = base_time + timedelta(hours=10)
            stamp += timedelta(days=1)
        return QueryBundle(
            summary_records=summaries,
            workshop_records=workshops,
            equipment_records=equipment,
            data_updated_at=latest_update,
        )
