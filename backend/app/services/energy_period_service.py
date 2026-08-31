from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import threading
from uuid import UUID
from zoneinfo import ZoneInfo

from app.models.domain import to_primitive
from app.services.energy_api import EnergyApiClient, EnergyApiError
from app.services.energy_service import STATUS_TEXT, interval_consumption, make_series


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
        unit = cfg.energy_electricity_unit or "原始单位待确认"
        notice = (
            "独立仪表组：所选电表增量相加＋累计流量；不绑定机床、不按任务分摊。"
            "日表按整点小时求累计读数差，月表汇总已保存日记录。"
            "仅汇总有效时段；部分合计不代表完整全天/整月用量。"
        )
        if cfg.energy_allow_interpolation:
            notice += "非精确边界采用线性插值并标记 ≈。"
        notice += f"最大允许采样间隔 {cfg.energy_report_max_gap_seconds // 60} 分钟（与任务报表独立配置）。"
        if not cfg.energy_electricity_unit:
            notice += "电能原始单位尚未确认，电耗数值暂按原始单位及配置因子显示，不标作 kWh。"
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
