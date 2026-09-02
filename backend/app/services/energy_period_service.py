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
from app.services.energy_service import STATUS_TEXT, interval_consumption, local_time, make_series
from app.services.equipment_service import sort_equipment_records, summarize_equipment


LABELS = {**STATUS_TEXT, "partial": "部分合计", "pending": "时段未结束",
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
        values = calculated_by_id.get(str(source.task_id), {})
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
    """Read-only meter queries + local daily snapshots. No production task binding.

    An hour means [HH:00, next HH:00]. A day means local midnight to next
    midnight. Never substitute the first/last available sample for that boundary.
    Monthly records are built from saved daily reports; synchronization is explicit.
    """

    def __init__(self, settings, repository, *, client=None, clock=None):
        self.settings = settings
        self.repository = repository
        self.client = client or EnergyApiClient(settings)
        self.clock = clock or (lambda: datetime.now(ZoneInfo(settings.timezone)))
        self._lock = threading.RLock()

    def now(self):
        stamp = self.clock()
        return stamp.astimezone(ZoneInfo(self.settings.timezone)).replace(tzinfo=None) if stamp.tzinfo else stamp

    @property
    def electricity_ids(self):
        return tuple(key.lower() for key in self.settings.energy_report_electricity_parameter_ids)

    @property
    def gas_id(self):
        return self.settings.energy_gas_parameter_id.lower()

    @property
    def source_key(self):
        cfg = self.settings
        # Credentials are never persisted. Account identity prevents cross-account reuse.
        scope = ["hourly-v1", cfg.energy_api_base_url.rstrip("/"), cfg.energy_api_username,
                 self.gas_id, sorted(self.electricity_ids), cfg.timezone,
                 cfg.energy_gas_unit, cfg.energy_electricity_unit,
                 cfg.energy_gas_factor, cfg.energy_electricity_factor,
                 cfg.energy_report_max_gap_seconds, cfg.energy_allow_interpolation]
        return sha256(json.dumps(scope, ensure_ascii=False).encode()).hexdigest()

    def configuration_error(self):
        cfg = self.settings
        if not cfg.energy_api_enabled:
            return "尚未启用能管接口，请配置 ENERGY_API_ENABLED 和内网账号；此报表无需设备绑定。"
        if not cfg.energy_api_username or not cfg.energy_api_password:
            return "尚未配置能管账号或密码。"
        try:
            ids = (self.gas_id, *self.electricity_ids)
            if not self.gas_id or not self.electricity_ids or len(set(ids)) != len(ids):
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
            return "仪表参数编号、换算因子或日/月报采样间隔配置无效。"
        return None

    def metadata(self):
        cfg = self.settings
        unit = cfg.energy_electricity_unit or "kWh"
        notice = (
            "独立仪表组：所选电表增量相加＋累计流量；不绑定机床、不按任务分摊。"
            "日表按整点小时求累计读数差，月表汇总已保存日记录。"
            "仅汇总有效时段；部分合计不代表完整全天/整月用量。"
        )
        if cfg.energy_allow_interpolation:
            notice += "非精确边界采用线性插值估算。"
        notice += f"最大允许采样间隔 {cfg.energy_report_max_gap_seconds // 60} 分钟（与任务报表独立配置）。"
        return {"units": {"gas": cfg.energy_gas_unit or "原始单位待确认", "electricity": unit},
                "notice": notice, "source_name": "电表合计 / 气量计", "timezone": cfg.timezone,
                "configuration_error": self.configuration_error(),
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

    def get_day(self, report_date: date, *, force=False):
        with self._lock:
            cached = self.repository.get_day(self.source_key, report_date.isoformat())
            now = self.now()
            if report_date > now.date():
                return to_primitive({**self.blank_day(report_date), **self.metadata(), "warning": "未来日期不查询。"})
            error = self.configuration_error()
            if error:
                return to_primitive(self._failed(report_date, cached, error))
            if cached and not force:
                age = (now - datetime.fromisoformat(cached["updated_at"])).total_seconds()
                ttl = min(60, self.settings.energy_report_snapshot_ttl_seconds) if report_date == now.date() else self.settings.energy_report_snapshot_ttl_seconds
                if 0 <= age < ttl:
                    return {**cached, **self.metadata(), "is_today": report_date == now.date(), "from_cache": True}

            start = datetime.combine(report_date, time.min)
            end = start + timedelta(days=1)
            padding = timedelta(seconds=self.settings.energy_report_max_gap_seconds)
            try:
                readings = self.client.fetch((self.gas_id, *self.electricity_ids), start - padding,
                                             min(end + padding, now), force=force)
                report = self.calculate_day(report_date, readings)
            except EnergyApiError as exc:
                return to_primitive(self._failed(report_date, cached, str(exc)))
            # A transient empty/partial response must not silently erase known data.
            if cached and any(old[key]["value"] is not None and new[key]["value"] is None
                              for old, new in zip(cached["rows"], report["rows"])
                              for key in CHANNELS):
                return to_primitive(self._failed(report_date, cached, "本次返回的有效时段减少，已保留原记录，请核对平台数据。"))
            report.update(updated_at=now.isoformat(timespec="seconds"), saved=True, sync_ok=True)
            report = to_primitive(report)
            self.repository.save_day(self.source_key, report)
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
                    interpolate=self.settings.energy_allow_interpolation, factor=Decimal(factor))
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
        """Calculate consumption for every task interval without equipment binding.

        The configured report meter group is treated as one cutting machine. Tasks
        with invalid or overlapping intervals remain visible but are not allocated.
        """
        units = self.metadata()["units"]
        clean = [replace(row, gas_consumption=None, electricity_consumption=None,
                         gas_data_status="missing", electricity_data_status="missing",
                         energy_note=None, energy_details={}) for row in records]

        def result(rows, mode, notice, warning=""):
            ordered = sort_equipment_records(rows)
            return to_primitive({
                "report_date": report_date.isoformat(),
                "records": ordered,
                "summary": summarize_equipment(ordered, mode),
                "units": units,
                "energy_notice": notice,
                "warning": warning,
                "configuration_error": self.configuration_error(),
            })

        if not clean:
            return result([], "unavailable", "所选日期暂无切割任务。")

        error = self.configuration_error()
        if error:
            unavailable = [replace(row, gas_data_status="unavailable",
                                   electricity_data_status="unavailable", energy_note=error)
                           for row in clean]
            return result(unavailable, "unavailable", error + "；缺失值不作为零值。", error)

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
            padding = timedelta(seconds=self.settings.energy_report_max_gap_seconds)
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
                return result(unavailable, "unavailable", message + "；缺失值不作为零值。", message)

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
        notice = (
            "按每项任务实际开始至结束时间计算累计读数差值；三块电表相加，气表独立计算。"
            "当前仪表组直接对应本设备，不要求维护设备绑定；汇总只包含有效任务读数。"
        )
        if self.settings.energy_allow_interpolation:
            notice += "边界非采样时刻采用线性插值并标记为估算。"
        if conflicts:
            notice += "存在时间重叠任务，无法重复分配同一仪表耗量，相关行保持缺失。"
        return result(output, mode, notice)

    def get_month(self, report_month):
        year, month = map(int, report_month.split("-"))
        count = calendar.monthrange(year, month)[1]
        saved = {item["report_date"]: item for item in self.repository.list_month(self.source_key, report_month)}
        today = self.now().date()
        rows = []
        for number in range(1, count + 1):
            stamp = date(year, month, number)
            stored = saved.get(stamp.isoformat())
            state = "future" if stamp > today else "not_queried"
            row = {"report_date": stamp.isoformat(), "saved": bool(stored),
                   "updated_at": stored["updated_at"] if stored else None,
                   "is_future": stamp > today, "is_today": stamp == today}
            for key in CHANNELS:
                row[key] = stored["summary"][key] if stored else channel_result(status=state,
                    valid_count=0, complete_count=0, expected_count=24, estimated_count=0)
            rows.append(row)
        elapsed = [row for row in rows if not row["is_future"]]
        return to_primitive({"report_month": report_month, "rows": rows,
            "summary": {key: total_channel(elapsed, key) for key in CHANNELS},
            "saved_day_count": sum(row["saved"] for row in rows),
            "elapsed_day_count": len(elapsed), "calendar_day_count": count,
            **self.metadata(), "storage_notice": "查询单日后保存小时明细和日合计；月表读取已保存记录。点击同步本月可逐日补齐，不是后台定时采集。"})
