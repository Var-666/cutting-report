from __future__ import annotations

import calendar
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import threading
from uuid import UUID
from zoneinfo import ZoneInfo

from app.models.domain import to_primitive
from app.services.energy_api import EnergyApiClient, EnergyApiError
from app.services.energy_service import (
    STATUS_TEXT, interval_consumption, local_time, make_series, sampling_policy,
)
from app.services.equipment_service import sort_equipment_records, summarize_equipment


LABELS = {**STATUS_TEXT, "partial": "已有数据", "pending": "时段未结束",
          "future": "日期未到", "not_queried": "尚未查询", "missing": "缺少有效边界读数"}
CHANNELS = ("gas", "electricity")


def channel_result(value=None, status="missing", **extra):
    return {"value": value, "status": status, "label": LABELS.get(status, status), **extra}


def total_channel(rows, channel):
    values = [row[channel] for row in rows if row[channel]["value"] is not None]
    complete_count = sum(value["status"] in {"measured", "interpolated"} for value in values)
    estimated_count = sum(value["status"] == "interpolated" or value.get("estimated_count", 0) > 0 for value in values)
    total = sum((Decimal(str(value["value"])) for value in values), Decimal("0")) if values else None
    status = ("missing" if not values else "partial" if complete_count < len(rows)
              else "interpolated" if estimated_count else "measured")
    return channel_result(total, status, valid_count=len(values), complete_count=complete_count,
                          expected_count=len(rows), estimated_count=estimated_count)


def restore_task_energy_records(records, task_report):
    """Restore typed task records from the API-safe task report payload.

    The task report is serialized for both the device page and HTML export.  The
    analysis service still works with ``EquipmentRecord`` instances, so keep the
    conversion in one place to guarantee that all three consumers use the same
    meter results and data-quality status.
    """
    calculated_by_id = {
        str(row["task_id"]): row for row in task_report.get("records", [])
    }
    output = []
    for source in records:
        values = calculated_by_id.get(str(source.task_id))
        # The task report can intentionally exclude tasks belonging to another
        # machine.  Do not silently re-add those rows to exports or analysis.
        if values is None:
            continue
        output.append(
            replace(
                source,
                gas_consumption=(
                    Decimal(str(values["gas_consumption"]))
                    if values.get("gas_consumption") is not None
                    else None
                ),
                electricity_consumption=(
                    Decimal(str(values["electricity_consumption"]))
                    if values.get("electricity_consumption") is not None
                    else None
                ),
                gas_data_status=values.get("gas_data_status") or "missing",
                electricity_data_status=(
                    values.get("electricity_data_status") or "missing"
                ),
                energy_note=values.get("energy_note"),
                energy_details=values.get("energy_details") or {},
            )
        )
    return output


class EnergyPeriodService:
    """Read-only meter queries + local daily snapshots for one configured machine.

    An hour means [HH:00, next HH:00]. A day means local midnight to next
    midnight. Never substitute the first/last available sample for that boundary.
    Monthly records are built from saved daily reports; synchronization is explicit.
    """

    def __init__(self, settings, repository, *, client=None, clock=None, query_service=None):
        self.settings = settings
        self.repository = repository
        self.client = client or EnergyApiClient(settings)
        self.clock = clock or (lambda: datetime.now(ZoneInfo(settings.timezone)))
        self.query_service = query_service
        self._lock = threading.RLock()
        self._month_sync_locks = [threading.Lock() for _ in range(12)]

    def now(self):
        stamp = self.clock()
        return stamp.astimezone(ZoneInfo(self.settings.timezone)).replace(tzinfo=None) if stamp.tzinfo else stamp

    @property
    def electricity_ids(self):
        return tuple(key.lower() for key in self.settings.energy_report_electricity_parameter_ids)

    @property
    def equipment_ids(self):
        return tuple(
            value.strip() for value in self.settings.energy_report_equipment_ids
            if value and value.strip()
        )

    @property
    def normalized_equipment_ids(self):
        return frozenset(value.casefold() for value in self.equipment_ids)

    def filter_task_records(self, records):
        """Keep only tasks belonging to the configured physical machine."""
        allowed = self.normalized_equipment_ids
        return [
            row for row in records
            if row.equipment_id and row.equipment_id.strip().casefold() in allowed
        ]

    @property
    def gas_id(self):
        return self.settings.energy_gas_parameter_id.lower()

    @property
    def source_key(self):
        cfg = self.settings
        # Credentials are never persisted. Account identity prevents cross-account reuse.
        scope = ["hourly-v2", cfg.energy_api_base_url.rstrip("/"), cfg.energy_api_username,
                 self.gas_id, sorted(self.electricity_ids), cfg.timezone,
                 cfg.energy_gas_unit, cfg.energy_electricity_unit,
                 cfg.energy_gas_factor, cfg.energy_electricity_factor,
                 cfg.energy_report_max_gap_seconds, cfg.energy_allow_interpolation,
                 sorted(self.normalized_equipment_ids)]
        return sha256(json.dumps(scope, ensure_ascii=False).encode()).hexdigest()

    @property
    def history_source_key(self):
        try:
            policy = sampling_policy(self.settings)
        except ValueError:
            policy = "invalid"
        return sha256(json.dumps([self.source_key, policy], default=str, sort_keys=True).encode()).hexdigest()

    def day_source_key(self, report_date):
        # Preserve the exact historical namespace. Only affected days require a
        # fresh snapshot, so old permissive results cannot pass as revalidated.
        try:
            policy = sampling_policy(self.settings)
        except ValueError:
            return self.source_key  # configuration_error handles invalid settings
        end = datetime.combine(report_date + timedelta(days=1), time.min)
        if not policy or end <= policy["sampling_change_at"]:
            return self.source_key
        scope = [self.source_key, "sampling-transition-v1",
                 policy["sampling_change_at"].isoformat(), policy["new_max_gap_seconds"]]
        return sha256(json.dumps(scope).encode()).hexdigest()

    def configuration_error(self):
        cfg = self.settings
        if not cfg.energy_connected:
            return "暂未接入"
        if not cfg.energy_api_enabled:
            return "尚未启用能管接口，请配置 ENERGY_API_ENABLED 和内网账号。"
        if not cfg.energy_api_username or not cfg.energy_api_password:
            return "尚未配置能管账号或密码。"
        try:
            sampling_policy(cfg)
            ids = (self.gas_id, *self.electricity_ids)
            if (not self.equipment_ids or not self.gas_id or not self.electricity_ids
                    or len(set(ids)) != len(ids)):
                raise ValueError()
            for key in ids:
                UUID(key)
            for factor in (cfg.energy_gas_factor, cfg.energy_electricity_factor):
                number = Decimal(factor)
                if not number.is_finite() or number <= 0:
                    raise ValueError()
            if not 60 <= cfg.energy_report_max_gap_seconds <= 3600:
                raise ValueError()
        except (ValueError, InvalidOperation):
            return "设备范围、仪表参数编号、换算因子、采样切换时间或间隔配置无效。"
        return None

    def metadata(self):
        cfg = self.settings
        unit = cfg.energy_electricity_unit or "kWh"
        return {"units": {"gas": cfg.energy_gas_unit or "原始单位待确认", "electricity": unit},
                "notice": "", "source_name": "电表合计 / 气量计", "timezone": cfg.timezone,
                "configuration_error": self.configuration_error(),
                "energy_connected": cfg.energy_connected,
                "equipment_scope": list(self.equipment_ids),
                "parameters": {"electricity": list(self.electricity_ids), "gas": self.gas_id}}

    def blank_day(self, report_date, *, status="not_queried"):
        now = self.now()
        start = datetime.combine(report_date, time.min)
        rows = []
        for hour in range(24):
            left, right = start + timedelta(hours=hour), start + timedelta(hours=hour + 1)
            state = "future" if report_date > now.date() else "pending" if right > now else status
            rows.append({"hour": hour, "label": f"{hour:02d}:00—{hour + 1:02d}:00",
                         "start": left.isoformat(), "end": right.isoformat(),
                         **{key: channel_result(status=state) for key in CHANNELS}})
        return {"report_date": report_date.isoformat(), "rows": rows,
                "summary": {key: total_channel(rows, key) for key in CHANNELS},
                "updated_at": None, "saved": False, "sync_ok": False,
                "is_today": report_date == now.date(), "warning": ""}

    def _failed(self, report_date, cached, message):
        report = cached or self.blank_day(report_date, status="unavailable")
        return {**report, **self.metadata(), "sync_ok": False,
                "is_today": report_date == self.now().date(),
                "warning": message + (" 显示上次已保存结果，未覆盖历史记录。" if cached else "")}

    def _snapshot_due(self, report_date, cached, now):
        if not cached:
            return True
        # A snapshot made before midnight is provisional even if still within TTL.
        if report_date < now.date() and cached.get("is_today"):
            return True
        try:
            age = (now - datetime.fromisoformat(cached["updated_at"])).total_seconds()
        except (KeyError, TypeError, ValueError):
            return True
        ttl = self.settings.energy_report_snapshot_ttl_seconds
        if report_date == now.date():
            ttl = min(60, ttl)
        return not 0 <= age < ttl

    @staticmethod
    def _loses_known_values(cached, report):
        if not cached:
            return False
        new_rows = {row["start"]: row for row in report["rows"]}
        return any(
            old[channel]["value"] is not None
            and new_rows.get(old["start"], {}).get(channel, {}).get("value") is None
            for old in cached["rows"] for channel in CHANNELS
        )

    def get_day(self, report_date: date, *, force=False):
        with self._lock:
            source_key = self.day_source_key(report_date)
            cached = self.repository.get_day(source_key, report_date.isoformat())
            outdated = (source_key != self.source_key and not cached
                        and self.repository.get_day(self.source_key, report_date.isoformat()))
            now = self.now()
            if report_date > now.date():
                return to_primitive({**self.blank_day(report_date), **self.metadata(), "warning": "未来日期不查询。"})
            error = self.configuration_error()
            if error:
                return to_primitive(self._failed(report_date, cached, error))
            if cached and not force and not self._snapshot_due(report_date, cached, now):
                return {**cached, **self.metadata(), "is_today": report_date == now.date(), "from_cache": True}

            start = datetime.combine(report_date, time.min)
            end = start + timedelta(days=1)
            padding = timedelta(seconds=max(self.settings.energy_report_max_gap_seconds,
                                            sampling_policy(self.settings).get("new_max_gap_seconds", 0)))
            try:
                readings = self.client.fetch((self.gas_id, *self.electricity_ids), start - padding,
                                             min(end + padding, now), force=force)
                report = self.calculate_day(report_date, readings)
            except EnergyApiError as exc:
                message = str(exc)
                if outdated:
                    message += " 该日旧快照已保留，但尚未通过新采样规则校验，请重新同步。"
                return to_primitive(self._failed(report_date, cached, message))
            # A transient empty/partial response must not silently erase known data.
            if self._loses_known_values(cached, report):
                return to_primitive(self._failed(report_date, cached, "本次返回的有效时段减少，已保留原记录，请核对平台数据。"))
            report.update(updated_at=now.isoformat(timespec="seconds"), saved=True, sync_ok=True)
            report = to_primitive(report)
            self.repository.save_day(source_key, report)
            return {**report, **self.metadata(), "from_cache": False}

    def calculate_day(self, report_date, readings):
        report = self.blank_day(report_date, status="missing")
        series = make_series(readings, (self.gas_id, *self.electricity_ids), self.settings.timezone)
        for row in report["rows"]:
            if row["gas"]["status"] in {"pending", "future"}:
                continue
            start, end = datetime.fromisoformat(row["start"]), datetime.fromisoformat(row["end"])

            def calculate(key, factor):
                result = interval_consumption(series[key], start, end,
                    max_gap_seconds=self.settings.energy_report_max_gap_seconds,
                    interpolate=self.settings.energy_allow_interpolation, factor=Decimal(factor),
                    **sampling_policy(self.settings))
                return {"parameter_id": key, **result}

            gas = calculate(self.gas_id, self.settings.energy_gas_factor)
            row["gas"] = channel_result(gas["value"], gas["status"], evidence=gas)
            meters = [calculate(key, self.settings.energy_electricity_factor) for key in self.electricity_ids]
            failure = next((meter["status"] for meter in meters if meter["value"] is None), None)
            status = failure or ("interpolated" if any(meter["status"] == "interpolated" for meter in meters) else "measured")
            value = None if failure else sum((meter["value"] for meter in meters), Decimal("0"))
            row["electricity"] = channel_result(value, status, evidence=meters)
        report["summary"] = {key: total_channel(report["rows"], key) for key in CHANNELS}
        report["sample_counts"] = {key: len(points) for key, (points, _) in series.items()}
        return report

    def get_task_day(self, report_date: date, records, *, force=False):
        return self.get_task_range(report_date, report_date, records, force=force)

    def get_task_range(self, start_date: date, end_date: date, records, *, force=False):
        report = self._calculate_task_report(start_date, records, force=force)
        report["period_start"] = start_date.isoformat()
        report["period_end"] = end_date.isoformat()
        return report

    def _calculate_task_report(self, report_date: date, records, *, force=False):
        """Calculate task consumption only for the configured physical machine."""
        units = self.metadata()["units"]
        source_records = list(records)
        selected_records = self.filter_task_records(source_records)
        clean = [replace(row, gas_consumption=None, electricity_consumption=None,
                         gas_data_status="missing", electricity_data_status="missing",
                         energy_note=None, energy_details={}) for row in selected_records]
        excluded_count = len(source_records) - len(selected_records)

        def result(rows, mode, notice, warning=""):
            ordered = sort_equipment_records(rows)
            return to_primitive({
                "report_date": report_date.isoformat(),
                "records": ordered,
                "summary": summarize_equipment(ordered, mode),
                "units": units,
                "equipment_scope": list(self.equipment_ids),
                "source_task_count": len(source_records),
                "excluded_task_count": excluded_count,
                "energy_notice": notice,
                "warning": warning,
                "configuration_error": self.configuration_error(),
                "energy_connected": self.settings.energy_connected,
            })

        error = self.configuration_error()
        if error:
            status = "unavailable" if self.settings.energy_connected else "not_connected"
            unavailable = [replace(row, gas_data_status=status,
                                   electricity_data_status=status, energy_note=error)
                           for row in clean]
            return result(unavailable, "unavailable", "", error)

        if not clean:
            return result([], "unavailable", "")

        intervals = {}
        for row in clean:
            try:
                start = local_time(row.production_time, self.settings.timezone)
                end = local_time(row.production_end_time, self.settings.timezone)
                if end <= start or end - start > timedelta(days=7):
                    continue
            except (TypeError, ValueError, OverflowError):
                continue
            intervals[row.task_id] = (start, end)

        conflicts = set()
        last_end, last_id = None, None
        for task_id, (start, end) in sorted(intervals.items(), key=lambda item: item[1][0]):
            if last_end is not None and start < last_end:
                conflicts.update((last_id, task_id))
            if last_end is None or end > last_end:
                last_end, last_id = end, task_id
        usable = {task_id: interval for task_id, interval in intervals.items() if task_id not in conflicts}

        ids = (self.gas_id, *self.electricity_ids)
        series = {}
        if usable:
            padding = timedelta(seconds=max(self.settings.energy_report_max_gap_seconds,
                                            sampling_policy(self.settings).get("new_max_gap_seconds", 0)))
            try:
                readings = self.client.fetch(ids, min(item[0] for item in usable.values()) - padding,
                                             max(item[1] for item in usable.values()) + padding,
                                             force=force)
                series = make_series(readings, ids, self.settings.timezone)
            except EnergyApiError as exc:
                message = str(exc)
                unavailable = [replace(row, gas_data_status="unavailable",
                                       electricity_data_status="unavailable", energy_note=message)
                               for row in clean]
                return result(unavailable, "unavailable", "", message)

        output = []
        for row in clean:
            status = ("invalid_interval" if row.task_id not in intervals
                      else "overlap" if row.task_id in conflicts else None)
            if status:
                output.append(replace(row, gas_data_status=status,
                                      electricity_data_status=status,
                                      energy_note=STATUS_TEXT[status]))
                continue

            start, end = usable[row.task_id]

            def calculate(key, factor):
                measured = interval_consumption(
                    series[key], start, end,
                    max_gap_seconds=self.settings.energy_report_max_gap_seconds,
                    interpolate=self.settings.energy_allow_interpolation,
                    factor=Decimal(factor),
                    **sampling_policy(self.settings),
                )
                return {"parameter_id": key, **measured}

            gas = calculate(self.gas_id, self.settings.energy_gas_factor)
            meters = [calculate(key, self.settings.energy_electricity_factor)
                      for key in self.electricity_ids]
            electric_failure = next((meter["status"] for meter in meters if meter["value"] is None), None)
            electric_status = electric_failure or (
                "interpolated" if any(meter["status"] == "interpolated" for meter in meters) else "measured"
            )
            electric_value = None if electric_failure else sum(
                (meter["value"] for meter in meters), Decimal("0")
            )
            details = {
                "method": "cumulative_difference",
                "scope": "任务实际开始至结束（含期间待机）",
                "start": start,
                "end": end,
                "gas": {**gas, "unit": units["gas"]},
                "electricity": {"meters": meters, "unit": units["electricity"]},
            }
            output.append(replace(
                row,
                gas_consumption=gas["value"],
                electricity_consumption=electric_value,
                gas_data_status=gas["status"],
                electricity_data_status=electric_status,
                energy_note=f"气耗：{STATUS_TEXT[gas['status']]}；电耗：{STATUS_TEXT[electric_status]}",
                energy_details=details,
            ))

        available = sum(row.gas_consumption is not None for row in output) + sum(
            row.electricity_consumption is not None for row in output
        )
        mode = "metered" if available == 2 * len(output) else "partial" if available else "unavailable"
        warning = "存在时间重叠任务，相关行未计入汇总。" if conflicts else ""
        return result(output, mode, "", warning)

    def get_month(self, report_month, *, month_metrics=None):
        year, month = map(int, report_month.split("-"))
        count = calendar.monthrange(year, month)[1]
        metrics_supplied = month_metrics is not None
        if month_metrics is None:
            month_metrics = {}
        if self.query_service and not metrics_supplied:
            if hasattr(self.query_service, "fetch_equipment_month_metrics"):
                month_metrics = self.query_service.fetch_equipment_month_metrics(
                    date(year, month, 1)
                )
            else:
                month_metrics = {
                    key: {"work_time_seconds": value, "task_count": 0}
                    for key, value in self.query_service.fetch_equipment_month_work_times(
                        date(year, month, 1)
                    ).items()
                }
        legacy = {item["report_date"]: item for item in self.repository.list_month(self.source_key, report_month)}
        saved = {}
        keys = {self.day_source_key(date(year, month, number)) for number in range(1, count + 1)}
        for source_key in keys:
            candidates = legacy.values() if source_key == self.source_key else self.repository.list_month(source_key, report_month)
            for item in candidates:
                if self.day_source_key(date.fromisoformat(item["report_date"])) == source_key:
                    saved[item["report_date"]] = item
        resync_dates = sorted(set(legacy) - set(saved))
        today = self.now().date()
        rows = []
        for number in range(1, count + 1):
            stamp = date(year, month, number)
            stored = saved.get(stamp.isoformat())
            metrics = month_metrics.get(stamp.isoformat(), {})
            state = "future" if stamp > today else "not_queried"
            row = {"report_date": stamp.isoformat(), "saved": bool(stored),
                   "updated_at": stored["updated_at"] if stored else None,
                   "is_future": stamp > today, "is_today": stamp == today,
                   "needs_resync": stamp.isoformat() in resync_dates,
                   "task_count": (None if stamp > today else int(metrics.get("task_count", 0))),
                   "work_time_seconds": (None if stamp > today else int(metrics.get("work_time_seconds", 0)))}
            for key in CHANNELS:
                row[key] = stored["summary"][key] if stored else channel_result(status=state,
                    valid_count=0, complete_count=0, expected_count=24, estimated_count=0)
            rows.append(row)
        elapsed = [row for row in rows if not row["is_future"]]
        return to_primitive({"report_month": report_month, "rows": rows,
            "summary": {key: total_channel(elapsed, key) for key in CHANNELS},
            "total_task_count": sum(row["task_count"] or 0 for row in elapsed),
            "total_work_time_seconds": sum(row["work_time_seconds"] or 0 for row in elapsed),
            "saved_day_count": sum(row["saved"] for row in rows),
            "elapsed_day_count": len(elapsed), "calendar_day_count": count,
            "resync_dates": resync_dates,
            **self.metadata(), "storage_notice": ""})

    def get_monthly_range(self, start_date: date, end_date: date):
        if end_date < start_date:
            raise ValueError("结束日期不能早于开始日期")
        month_count = (
            (end_date.year - start_date.year) * 12
            + end_date.month - start_date.month + 1
        )
        if month_count > 12:
            raise ValueError("月度汇总区间最长为 12 个月")
        if not 1900 <= start_date.year <= 2100 or not 1900 <= end_date.year <= 2100:
            raise ValueError("日期需在 1900—2100 年之间")

        metrics = {}
        if self.query_service:
            if hasattr(self.query_service, "fetch_equipment_range_metrics"):
                metrics = self.query_service.fetch_equipment_range_metrics(
                    start_date, end_date + timedelta(days=1)
                )
            else:
                current = start_date.replace(day=1)
                while current <= end_date:
                    metrics.update(self.query_service.fetch_equipment_month_metrics(current))
                    current = (current.replace(day=28) + timedelta(days=4)).replace(day=1)

        selected_daily_rows = []
        monthly_rows = []
        current = start_date.replace(day=1)
        while current <= end_date:
            report_month = current.strftime("%Y-%m")
            month_metrics = {
                key: value for key, value in metrics.items()
                if key.startswith(report_month + "-")
            }
            report = self.get_month(report_month, month_metrics=month_metrics)
            rows = [
                row for row in report["rows"]
                if start_date.isoformat() <= row["report_date"] <= end_date.isoformat()
            ]
            selected_daily_rows.extend(rows)
            monthly_rows.append({
                "report_date": report_month,
                "task_count": sum(row["task_count"] or 0 for row in rows),
                "work_time_seconds": sum(row["work_time_seconds"] or 0 for row in rows),
                "electricity": total_channel(rows, "electricity"),
                "gas": total_channel(rows, "gas"),
            })
            current = (current.replace(day=28) + timedelta(days=4)).replace(day=1)

        return to_primitive({
            "period_start": start_date.isoformat(),
            "period_end": end_date.isoformat(),
            "rows": monthly_rows,
            "summary": {
                key: total_channel(selected_daily_rows, key) for key in CHANNELS
            },
            "total_task_count": sum(row["task_count"] or 0 for row in selected_daily_rows),
            "total_work_time_seconds": sum(row["work_time_seconds"] or 0 for row in selected_daily_rows),
            "saved_day_count": sum(bool(row["saved"]) for row in selected_daily_rows),
            "elapsed_day_count": sum(not row["is_future"] for row in selected_daily_rows),
            "calendar_day_count": len(selected_daily_rows),
            "missing_day_count": sum(
                not row["saved"] and not row["is_future"]
                for row in selected_daily_rows
            ),
            **self.metadata(),
        })

    def sync_month(self, report_month: date, *, force: bool = False) -> int:
        """Backfill one month's daily snapshots without blocking report reads."""
        month_start = report_month.replace(day=1)
        next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
        today = self.now().date()
        last_day = min(next_month - timedelta(days=1), today)
        if last_day < month_start:
            return 0
        error = self.configuration_error()
        if error:
            raise EnergyApiError(error)

        sync_lock = self._month_sync_locks[
            (month_start.year * 12 + month_start.month) % len(self._month_sync_locks)
        ]
        with sync_lock:
            missing = []
            now = self.now()
            stamp = month_start
            while stamp <= last_day:
                source_key = self.day_source_key(stamp)
                cached = self.repository.get_day(source_key, stamp.isoformat())
                if force or self._snapshot_due(stamp, cached, now):
                    missing.append(stamp)
                stamp += timedelta(days=1)
            if not missing:
                return 0

            start = datetime.combine(month_start, time.min)
            end = datetime.combine(last_day + timedelta(days=1), time.min)
            now = self.now()
            padding = timedelta(
                seconds=max(
                    self.settings.energy_report_max_gap_seconds,
                    sampling_policy(self.settings).get("new_max_gap_seconds", 0),
                )
            )
            fetch = getattr(self.client, "fetch_month", self.client.fetch)
            readings = fetch(
                (self.gas_id, *self.electricity_ids),
                start - padding,
                min(end + padding, now),
                force=force,
            )
            updated_at = now.isoformat(timespec="seconds")
            saved_count = 0
            for stamp in missing:
                day_start = datetime.combine(stamp, time.min)
                day_end = day_start + timedelta(days=1)
                day_readings = []
                for row in readings:
                    try:
                        reading_time = local_time(row.get("Mt"), self.settings.timezone)
                    except (TypeError, ValueError, OverflowError):
                        continue
                    if day_start - padding <= reading_time <= day_end + padding:
                        day_readings.append(row)
                # Do not turn a truncated/partial monthly response into a
                # permanent empty snapshot. A later background pass must retry it.
                if not day_readings:
                    continue
                report = self.calculate_day(stamp, day_readings)
                if all(
                    report["summary"][channel]["value"] is None
                    for channel in CHANNELS
                ):
                    continue
                cached = self.repository.get_day(self.day_source_key(stamp), stamp.isoformat())
                if self._loses_known_values(cached, report):
                    continue
                report.update(
                    updated_at=updated_at,
                    saved=True,
                    sync_ok=True,
                    is_today=stamp == today,
                )
                self.repository.save_day(
                    self.day_source_key(stamp), to_primitive(report)
                )
                saved_count += 1
            return saved_count
