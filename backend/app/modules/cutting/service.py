from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from functools import partial
import logging
from threading import Lock
from time import monotonic
from zoneinfo import ZoneInfo

from app.config import Settings
from app.core.operations_logging import data_quality
from app.modules.cutting.repository import CuttingQueryRepository
from app.services.ship_name_service import ShipNameService
from app.services.cache_revision import CacheRevision
from app.services.query_cache import QueryCache as _QueryCache
from app.services.report_closing_comparison import CUTOFF, Totals, business_day_window, closing_bucket
from app.database.sqlserver import SqlServerClient
from app.models.domain import (
    EquipmentRecord,
    QueryBundle,
    ReportFilter,
    SummaryBaseRecord,
    WorkshopRecord,
)

# Persisted operations incident fingerprints include the logger name.
logger = logging.getLogger('app.services.query_service')


@dataclass(frozen=True, slots=True)
class EquipmentQueryResult:
    records: list[EquipmentRecord]
    data_updated_at: datetime | None = None



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
    def __init__(self, settings: Settings, *, client: SqlServerClient | None = None, ship_names: ShipNameService | None = None, repository: CuttingQueryRepository | None = None) -> None:
        self.settings = settings
        self.ship_names = ship_names
        self.cache_revision = CacheRevision(settings, "production")
        self.repository = repository or CuttingQueryRepository(client, settings.report_machine_no)
        self._equipment_cache = _QueryCache[EquipmentQueryResult]()
        self._bundle_cache = _QueryCache[QueryBundle]()
        self._equipment_metrics_cache_lock = Lock()
        self._equipment_metrics_cache: dict[
            tuple, tuple[float, dict[str, dict[str, Decimal | int]]]
        ] = {}
        self._equipment_metrics_locks = [Lock() for _ in range(16)]
        self._latest_date_lock = Lock()
        self._latest_date_cache: dict[tuple, tuple[float, date | None]] = {}
        self._workshop_cache_lock = Lock()
        self._workshop_prepare_locks = [Lock() for _ in range(16)]
        self._workshop_cache: dict[tuple, tuple[float, QueryBundle]] = {}

    def enrich_ship_names(self, records: list, *, force: bool = False) -> list:
        return self.ship_names.enrich(records, force=force) if self.ship_names else records

    def _ship_records(self, records: list, filters: ReportFilter, *, force: bool = False) -> list:
        records = self.enrich_ship_names(records, force=force)
        if self.ship_names and filters.ship_no:
            records = [row for row in records if row.ship_no == filters.ship_no]
        return records

    def prepare_workshop_data(self, report_date: date) -> QueryBundle:
        """Read-only, short-lived warmup; does not create reports or confirmations."""
        prepare_lock = self._workshop_prepare_locks[hash(report_date) % len(self._workshop_prepare_locks)]
        with prepare_lock:
            cache_key = (report_date, self.cache_revision.version())
            with self._workshop_cache_lock:
                now = monotonic()
                cached = self._workshop_cache.get(cache_key)
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
                    self._workshop_cache[cache_key] = (monotonic() + ttl, bundle)
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

    def _cache_key(self, kind: str, filters: ReportFilter) -> tuple:
        return (
            kind,
            filters.start_date,
            filters.end_date,
            filters.project_code,
            filters.ship_no,
            self.cache_revision.version(),
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
            bundle = self._fetch_cached_bundle(
                "summary", filters, self._sqlserver_summary_data, force=force
            )
            return replace(bundle, summary_records=self._ship_records(bundle.summary_records, filters, force=force))
        bundle = self.fetch_report_data(filters)
        return QueryBundle(bundle.summary_records, [], [], bundle.data_updated_at)

    def fetch_workshop_data(
        self, filters: ReportFilter, *, force: bool = False, business_days: bool = False
    ) -> QueryBundle:
        if self.settings.effective_data_source_mode == "sqlserver":
            loader = partial(self._sqlserver_workshop_data, business_days=True) if business_days else self._sqlserver_workshop_data
            kind = "workshop:business_days" if business_days else "workshop"
            bundle = self._fetch_cached_bundle(kind, filters, loader, force=force)
            return replace(bundle, workshop_records=self._ship_records(bundle.workshop_records, filters, force=force))
        if business_days:
            zone = ZoneInfo(self.settings.timezone)
            start, end = business_day_window(filters.start_date, filters.end_date, zone)
            bundle = self._demo_data(replace(filters, report_date=start.date(), period_start=start.date(), period_end=end.date()))
            completed = {row.task_id: row.production_end_time for row in bundle.equipment_records
                         if row.production_end_time is not None and start <= row.production_end_time < end}
            records = [replace(row, production_date=self._workshop_business_date(completed[row.task_id]))
                       for row in bundle.workshop_records if row.task_id in completed]
            return QueryBundle([], records, [], max(completed.values(), default=None))
        bundle = self.fetch_report_data(filters)
        return QueryBundle([], bundle.workshop_records, [], bundle.data_updated_at)

    def fetch_equipment_data(
        self, filters: ReportFilter, *, force: bool = False
    ) -> EquipmentQueryResult:
        """Fetch only equipment rows and coalesce repeated per-day requests."""
        result = self._equipment_cache.read(
            self._cache_key("equipment", filters), lambda: self._fetch_equipment_uncached(filters),
            ttl=self.settings.report_data_cache_seconds, force=force,
        )
        return replace(result, records=self._ship_records(result.records, filters, force=force))

    def fetch_equipment_month_work_times(
        self, report_month: date
    ) -> dict[str, Decimal]:
        """Return configured-machine cutting seconds grouped by production day."""
        return {
            report_date: values["work_time_seconds"]
            for report_date, values in self.fetch_equipment_month_metrics(report_month).items()
        }

    def fetch_equipment_month_metrics(
        self, report_month: date, *, force: bool = False
    ) -> dict[str, dict[str, Decimal | int]]:
        """Return configured-machine task counts and cutting seconds by day."""
        month_start = report_month.replace(day=1)
        month_end = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
        return self.fetch_equipment_range_metrics(month_start, month_end, force=force)

    def fetch_equipment_range_metrics(
        self, start_date: date, end_exclusive: date, *, force: bool = False
    ) -> dict[str, dict[str, Decimal | int]]:
        """Return cached daily equipment metrics for a range."""
        if end_exclusive <= start_date:
            return {}
        cache_seconds = self.settings.report_data_cache_seconds
        if cache_seconds <= 0:
            return self._fetch_equipment_range_metrics_uncached(
                start_date, end_exclusive
            )
        key = (start_date, end_exclusive, self.cache_revision.version())
        with self._equipment_metrics_locks[
            hash(key) % len(self._equipment_metrics_locks)
        ]:
            now = monotonic()
            with self._equipment_metrics_cache_lock:
                cached = self._equipment_metrics_cache.get(key)
                if not force and cached and cached[0] > now:
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

        rows = self.repository.report_rows(
            "equipment_month_work_time.sql",
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
            None if self.ship_names else filters.ship_no,
            None if self.ship_names else filters.ship_no,
        )
        rows = self.repository.report_rows("equipment.sql", parameters)
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
        self._observe_query_quality('equipment', filters, rows, records, latest_update)
        return EquipmentQueryResult(records, latest_update)

    def list_projects(self, filters: ReportFilter) -> list[str]:
        if self.settings.effective_data_source_mode == "demo":
            return ["A001", "A002", "B101"]
        rows = self.repository.list_projects(filters)
        return [str(row["project_code"]) for row in rows]

    def list_ships(self, filters: ReportFilter) -> list[str]:
        if self.settings.effective_data_source_mode == "demo":
            names = ["A001", "A002", "B101"]
            return [filters.project_code] if filters.project_code in names else names
        rows = self.repository.list_ships(filters)
        if self.ship_names:
            names = self.ship_names.names_for([str(row['project_name']) for row in rows]) or {}
            return sorted({name for name in names.values() if name})
        return [str(row["project_name"]) for row in rows]

    def latest_production_date(
        self, on_or_before: date, *, force: bool = False
    ) -> date | None:
        cache_seconds = self.settings.report_data_cache_seconds
        if cache_seconds <= 0:
            return self._latest_production_date_uncached(on_or_before)

        now = monotonic()
        cache_key = (on_or_before, self.cache_revision.version())
        with self._latest_date_lock:
            cached = self._latest_date_cache.get(cache_key)
            if not force and cached and cached[0] > now:
                return cached[1]
            result = self._latest_production_date_uncached(on_or_before)
            self._latest_date_cache = {
                key: value
                for key, value in self._latest_date_cache.items()
                if value[0] > now
            }
            self._latest_date_cache[cache_key] = (now + cache_seconds, result)
            while len(self._latest_date_cache) > 128:
                self._latest_date_cache.pop(next(iter(self._latest_date_cache)))
            return result

    def _latest_production_date_uncached(self, on_or_before: date) -> date | None:
        if self.settings.effective_data_source_mode == "demo":
            return on_or_before
        return self.repository.latest_production_date(on_or_before)

    def fetch_cutting_closing_totals(
        self, start: datetime, end: datetime, *, project_code: str | None = None, ship_no: str | None = None,
    ) -> dict[datetime, Totals]:
        sqlserver = self.settings.effective_data_source_mode == "sqlserver"
        if sqlserver and not (project_code or ship_no):
            return {
                datetime.fromisoformat(str(row["bucket_start"])).replace(tzinfo=start.tzinfo): dict(
                    task_count=int(row["task_count"]),
                    cut_length_m=Decimal(str(row["cut_length_m"])),
                    groove_length_m=Decimal(str(row["groove_length_m"])),
                    work_time_seconds=int(row["work_time_seconds"]),
                )
                for row in self.repository.cutting_closing_totals(start, end)
            }
        filters = ReportFilter(
            report_date=start.date(), period_end=(end - timedelta(microseconds=1)).date(),
            project_code=project_code, ship_no=ship_no,
        )
        bundle = self.fetch_summary_data(filters, force=True)
        buckets: dict[datetime, Totals] = {}
        for row in bundle.summary_records:
            if project_code and row.project_code != project_code:
                continue
            # summary_base.sql reads END_TIME into source_updated_at.
            completed_at = row.source_updated_at if sqlserver else (
                row.production_time + timedelta(seconds=float(row.work_time or 0)) if row.production_time else None
            )
            if completed_at is None:
                continue
            try:
                stamp = datetime.fromisoformat(str(completed_at))
            except ValueError:
                continue
            stamp = stamp.astimezone(start.tzinfo) if stamp.tzinfo else stamp.replace(tzinfo=start.tzinfo)
            if not start <= stamp < end:
                continue
            total = buckets.setdefault(closing_bucket(stamp), dict(task_count=0, cut_length_m=Decimal("0"),
                groove_length_m=Decimal("0"), work_time_seconds=0))
            total["task_count"] += 1
            total["cut_length_m"] += row.cut_length or Decimal("0")
            total["groove_length_m"] += row.groove_length or Decimal("0")
            total["work_time_seconds"] += int(row.work_time or 0)
        return buckets

    def _observe_query_quality(self, kind: str, filters: ReportFilter, rows, records, latest_update):
        seen = set()
        invalid = 0
        for raw, record in zip(rows, records, strict=True):
            task_id = record.task_id.strip()
            day = record.production_date
            if raw['task_id'] is None or not task_id or task_id in seen or day is None or not filters.start_date <= day <= filters.end_date:
                invalid += 1
            seen.add(task_id)
        start, end = filters.start_date.isoformat(), filters.end_date.isoformat()
        message = '报表源数据含缺失或重复任务编号、无效统计日期' if invalid else '报表源任务编号和统计日期校验通过'
        data_quality(logger, 'REPORT_QUERY_DATA_QUALITY', message,
                     scope=(kind, start, end, filters.project_code, filters.ship_no, self.settings.report_machine_no),
                     level=logging.ERROR if invalid else logging.INFO, state='invalid_report_rows' if invalid else 'verified',
                     line_id=self.settings.device_id, source_id=self.settings.source_fingerprint, stage=kind,
                     period_start=start, period_end=end, summary_count=len(records), missing_count=invalid,
                     data_as_of=latest_update.isoformat() if isinstance(latest_update, datetime) else None)

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
            None if self.ship_names else filters.ship_no,
            None if self.ship_names else filters.ship_no,
        )
        rows = self.repository.report_rows("summary_base.sql", parameters)
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

    @staticmethod
    def _workshop_business_date(completed_at: datetime) -> date:
        return completed_at.date() + timedelta(days=completed_at.time() >= CUTOFF)

    def _sqlserver_workshop_data(self, filters: ReportFilter, *, business_days: bool = False) -> QueryBundle:
        if business_days:
            start, end = business_day_window(filters.start_date, filters.end_date, ZoneInfo(self.settings.timezone))
            start, end = start.replace(tzinfo=None), end.replace(tzinfo=None)
        else:
            start = datetime.combine(filters.start_date, time.min)
            end = datetime.combine(filters.end_exclusive, time.min)
        parameters = (
            self.settings.report_machine_no,
            start,
            end,
            filters.project_code,
            filters.project_code,
            None if self.ship_names else filters.ship_no,
            None if self.ship_names else filters.ship_no,
        )
        rows = self.repository.report_rows("workshop.sql", parameters)
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
                production_date=(self._workshop_business_date(row["source_updated_at"]) if business_days
                                 else _date(row.get("production_date"))),
            )
            for row in rows
        ]
        latest_update = max(
            (row.get("source_updated_at") for row in rows if row.get("source_updated_at")),
            default=None,
        )
        self._observe_query_quality('workshop', filters, rows, records, latest_update)
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
