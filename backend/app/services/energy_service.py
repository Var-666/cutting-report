from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from uuid import UUID
from zoneinfo import ZoneInfo

from app.config import Settings
from app.models.domain import EquipmentRecord
from app.services.energy_api import EnergyApiClient, EnergyApiError
from app.services.equipment_service import mock_energy, sort_equipment_records


STATUS_TEXT = {
    "measured": "实测边界读数差值", "interpolated": "实测读数插值估算",
    "missing": "缺少区间边界读数", "gap": "采样间隔过大",
    "counter_reset": "累计表回零或读数倒退", "invalid_reading": "读数无效或同刻冲突",
    "unconfigured": "参数编号或单位未确认", "unmapped": "设备未绑定仪表",
    "invalid_interval": "缺少或异常的实际起止时间", "overlap": "同一计量设备任务区间重叠",
    "unavailable": "能管数据不可用", "mock": "模拟数据",
}


def local_time(value, timezone: str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.astimezone(ZoneInfo(timezone)).replace(tzinfo=None) if parsed.tzinfo else parsed


def make_series(rows, ids, timezone):
    readings: dict[str, dict[datetime, Decimal | None]] = {key: {} for key in ids}
    invalid = set()
    for row in rows:
        key = str(row.get("MfId", "")).lower()
        if key not in readings:
            continue
        try:
            stamp = local_time(row.get("Mt"), timezone)
        except (TypeError, ValueError, OverflowError):
            invalid.add(key)
            continue
        try:
            value = Decimal(str(row.get("Mv")))
            if not value.is_finite() or value < 0:
                value = None
        except (InvalidOperation, ValueError):
            value = None
        previous = readings[key].get(stamp)
        if stamp in readings[key] and previous != value:
            value = None
        readings[key][stamp] = value
    return {key: (sorted(points.items()), key in invalid) for key, points in readings.items()}


def sampling_policy(settings):
    """Shared transition policy for legacy task and unified energy reports."""
    if not settings.energy_sampling_change_at:
        return {}
    if not 60 <= settings.energy_new_max_gap_seconds <= 3600:
        raise ValueError("新采样间隔上限必须为 60—3600 秒")
    return {"sampling_change_at": local_time(settings.energy_sampling_change_at, settings.timezone),
            "new_max_gap_seconds": settings.energy_new_max_gap_seconds}


def sampling_notice(settings, historical_gap):
    policy = sampling_policy(settings)
    if not policy:
        return f"最大允许采样间隔 {historical_gap} 秒。"
    stamp = policy["sampling_change_at"].isoformat(sep=" ")
    return (f"采样规则切换时间 {stamp}（{settings.timezone}）；"
            f"此前允许间隔 {historical_gap} 秒，此后 {policy['new_max_gap_seconds']} 秒；"
            "超过上限的时段标为数据缺口，不计入有效耗量。")


def interval_consumption(series, start, end, *, max_gap_seconds, interpolate, factor=Decimal("1"),
                         sampling_change_at=None, new_max_gap_seconds=180):
    points, invalid = series
    result = {"value": None, "status": "missing"}
    if invalid:
        return {**result, "status": "invalid_reading"}
    times = [stamp for stamp, _ in points]
    left = bisect_right(times, start) - 1
    right = bisect_left(times, end)
    if left < 0 or right >= len(points):
        return result
    selected = points[left:right + 1]
    if any(value is None for _, value in selected):
        return {**result, "status": "invalid_reading"}
    for (t0, v0), (t1, v1) in zip(selected, selected[1:]):
        if v1 < v0:
            return {**result, "status": "counter_reset"}
        # Check each side separately when samples straddle the acquisition change.
        # A normal final historical sample must not be judged as a one-minute sample.
        before_end = min(t1, sampling_change_at) if sampling_change_at else t1
        old_gap = max(0, (before_end - t0).total_seconds())
        new_gap = max(0, (t1 - max(t0, sampling_change_at)).total_seconds()) if sampling_change_at else 0
        if old_gap > max_gap_seconds or new_gap > new_max_gap_seconds:
            return {**result, "status": "gap"}
    estimated = start not in times or end not in times
    if estimated and not interpolate:
        return result

    def boundary(stamp):
        i = bisect_left(times, stamp)
        if times[i] == stamp:
            return points[i][1]
        t0, v0 = points[i - 1]
        t1, v1 = points[i]
        ratio = Decimal(str((stamp - t0).total_seconds())) / Decimal(str((t1 - t0).total_seconds()))
        return v0 + (v1 - v0) * ratio

    try:
        start_value, end_value = boundary(start), boundary(end)
        consumption = ((end_value - start_value) * factor).quantize(Decimal("0.001"))
    except ArithmeticError:
        return {**result, "status": "invalid_reading"}
    return {
        "value": consumption,
        "status": "interpolated" if estimated else "measured",
        "start_reading": start_value, "end_reading": end_value,
        "first_sample_time": selected[0][0], "first_sample_value": selected[0][1],
        "last_sample_time": selected[-1][0], "last_sample_value": selected[-1][1],
        "sample_count": len(selected), "conversion_factor": factor,
    }


@dataclass
class EnergyResult:
    records: list[EquipmentRecord]
    mode: str
    notice: str
    units: dict[str, str]


class EquipmentEnergyService:
    def __init__(self, settings: Settings, client=None):
        self.settings = settings
        self.client = client or EnergyApiClient(settings)

    def enrich(self, records: list[EquipmentRecord], *, force=False, context_records=None) -> EnergyResult:
        cfg = self.settings
        if cfg.effective_data_source_mode == "demo" and cfg.energy_connected:
            return EnergyResult(sort_equipment_records(mock_energy(records)), "mock",
                "气耗和电耗为系统生成的稳定模拟数据，仅用于演示，不可用于结算或考核。",
                {"gas": "Nm³（模拟）", "electricity": "kWh（模拟）"})
        electricity_unit = cfg.energy_electricity_unit or "kWh"
        units = {"gas": cfg.energy_gas_unit or "单位待确认", "electricity": electricity_unit}
        clean = [replace(row, gas_consumption=None, electricity_consumption=None,
                         gas_data_status="missing", electricity_data_status="missing",
                         energy_note=None, energy_details={}) for row in records]

        def unavailable(message):
            return EnergyResult(sort_equipment_records([
                replace(row, gas_data_status="unavailable", electricity_data_status="unavailable", energy_note=message)
                for row in clean]), "unavailable", message + "；缺失值不作为零值或模拟值。", units)

        if not cfg.energy_connected:
            return unavailable("暂未接入")
        if not clean:
            return EnergyResult([], "unavailable", "所选日期暂无设备任务。", units)
        if not cfg.energy_api_enabled:
            return unavailable("尚未启用能管接口")
        if not cfg.energy_equipment_ids:
            return unavailable("请配置这套仪表对应的生产设备 MACHINE_NO")
        gas_id = cfg.energy_gas_parameter_id.lower()
        electric_ids = tuple(key.lower() for key in cfg.energy_electricity_parameter_ids)
        gas_ready = bool(gas_id and cfg.energy_gas_unit in {"m³", "Nm³"})
        electric_ready = bool(electric_ids and electricity_unit == "kWh")
        ids = ((gas_id,) if gas_ready else ()) + (electric_ids if electric_ready else ())
        try:
            policy = sampling_policy(cfg)
            if not 1 <= cfg.energy_max_gap_seconds <= 3600:
                raise ValueError()
            for key in ids:
                UUID(key)
            if len(set(ids)) != len(ids):
                raise ValueError()
            gas_factor, electric_factor = Decimal(cfg.energy_gas_factor), Decimal(cfg.energy_electricity_factor)
            if any(not value.is_finite() or value <= 0 for value in (gas_factor, electric_factor)):
                raise ValueError()
        except (ValueError, InvalidOperation):
            return unavailable("能管参数编号、转换因子或采样间隔配置无效")
        if not ids:
            return unavailable("请确认气量单位以及需要合计的电表参数编号和单位")

        intervals = {}
        reference = context_records if context_records is not None else records
        # Aliases share one meter set; never count overlapping tasks twice.
        for row in reference:
            if row.equipment_id not in cfg.energy_equipment_ids:
                continue
            try:
                start = local_time(row.production_time, cfg.timezone)
                end = local_time(row.production_end_time, cfg.timezone)
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
        usable = {row.task_id: intervals[row.task_id] for row in clean
                  if row.task_id in intervals and row.task_id not in conflicts and row.equipment_id in cfg.energy_equipment_ids}
        series = {}
        if usable:
            padding = timedelta(seconds=max(cfg.energy_max_gap_seconds, policy.get("new_max_gap_seconds", 0)))
            try:
                readings = self.client.fetch(ids, min(t[0] for t in usable.values()) - padding,
                                             max(t[1] for t in usable.values()) + padding, force=force)
                series = make_series(readings, ids, cfg.timezone)
            except EnergyApiError as exc:
                return unavailable(str(exc))

        output = []
        for row in clean:
            status = ("unmapped" if row.equipment_id not in cfg.energy_equipment_ids
                      else "invalid_interval" if row.task_id not in intervals
                      else "overlap" if row.task_id in conflicts else None)
            if status:
                output.append(replace(row, gas_data_status=status, electricity_data_status=status, energy_note=STATUS_TEXT[status]))
                continue
            start, end = usable[row.task_id]
            details = {"method": "cumulative_difference", "start": start, "end": end,
                       "scope": "任务实际开始至结束（含期间待机）；不是全天总耗量"}

            def calculate(key, factor):
                return {"parameter_id": key, **interval_consumption(series[key], start, end,
                    max_gap_seconds=cfg.energy_max_gap_seconds,
                    interpolate=cfg.energy_allow_interpolation, factor=factor, **policy)}

            gas = calculate(gas_id, gas_factor) if gas_ready else {"value": None, "status": "unconfigured"}
            electricity = [calculate(key, electric_factor) for key in electric_ids] if electric_ready else []
            electric_status = next((item["status"] for item in electricity if item["value"] is None), None)
            electric_value = None
            if not electricity:
                electric_status = "unconfigured"
            elif electric_status is None:
                electric_value = sum((item["value"] for item in electricity), Decimal("0"))
                electric_status = "interpolated" if any(item["status"] == "interpolated" for item in electricity) else "measured"
            details.update(gas={**gas, "unit": units["gas"]}, electricity={"meters": electricity, "unit": units["electricity"]})
            output.append(replace(row, gas_consumption=gas["value"], electricity_consumption=electric_value,
                gas_data_status=gas["status"], electricity_data_status=electric_status, energy_details=details,
                energy_note=f"气耗：{STATUS_TEXT[gas['status']]}；电耗：{STATUS_TEXT[electric_status]}"))

        available = sum(row.gas_consumption is not None for row in output) + sum(row.electricity_consumption is not None for row in output)
        mode = "metered" if available == 2 * len(output) else "partial" if available else "unavailable"
        notice = "按已绑定设备的实际 START_TIME / END_TIME 计算累计读数差值；汇总仅覆盖有效任务区间（含期间待机），不是整日总耗量。"
        if cfg.energy_allow_interpolation:
            notice += "边界非采样时刻采用线性插值并标为估算。"
        notice += sampling_notice(cfg, cfg.energy_max_gap_seconds)
        if mode != "metered":
            notice += "部分或全部读数缺失；仅汇总已匹配记录，不将缺失计为零。"
        return EnergyResult(sort_equipment_records(output), mode, notice, units)
