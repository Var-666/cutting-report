from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import logging
from uuid import UUID
from zoneinfo import ZoneInfo

from app.config import Settings
from app.core.operations_logging import data_quality
from app.models.domain import EquipmentRecord
from app.modules.energy.client import EnergyApiClient, EnergyApiError, EnergyReadingSource
from app.modules.energy.meter import interval_consumption, local_time, make_series, sampling_notice, sampling_policy
from app.modules.cutting.equipment import mock_energy, sort_equipment_records

# Persisted operations incident fingerprints include the logger name.
logger = logging.getLogger('app.services.energy_service')


STATUS_TEXT = {
    "measured": "实测边界读数差值", "interpolated": "实测读数插值估算",
    "estimated": "累计读数估算；缺失首尾按已覆盖时段的平均速率补齐",
    "missing": "缺少区间边界读数", "gap": "采样间隔过大",
    "counter_reset": "累计表回零或读数倒退", "invalid_reading": "读数无效或同刻冲突",
    "unconfigured": "参数编号或单位未确认", "unmapped": "设备未绑定仪表",
    "invalid_interval": "缺少或异常的实际起止时间", "overlap": "同一计量设备任务区间重叠",
    "unavailable": "能管数据不可用", "mock": "模拟数据",
}
TASK_OVERLAP_TOLERANCE = timedelta(minutes=1)


def overlapping_energy_tasks(intervals: dict[str, tuple[datetime, datetime]]) -> set[str]:
    conflicts = set()
    latest = None
    for task_id, (start, end) in sorted(intervals.items(), key=lambda item: item[1][0]):
        # A short task cannot share more than the tolerance, even inside a longer task.
        if end - start <= TASK_OVERLAP_TOLERANCE:
            continue
        if latest is not None and latest[0] - start > TASK_OVERLAP_TOLERANCE:
            conflicts.update((latest[1], task_id))
        if latest is None or end > latest[0]:
            latest = (end, task_id)
    return conflicts


def task_intervals(records, timezone: str) -> dict[str, tuple[datetime, datetime]]:
    intervals = {}
    for row in records:
        try:
            start = local_time(row.production_time, timezone)
            end = local_time(row.production_end_time, timezone)
        except (TypeError, ValueError, OverflowError):
            continue
        if end > start and end - start <= timedelta(days=7):
            intervals[row.task_id] = (start, end)
    return intervals


def task_meter_consumption(series, start, end, *, gas_id, electricity_ids,
                           gas_factor, electricity_factor, max_gap_seconds, interpolate, policy):
    def calculate(key, factor):
        return {"parameter_id": key, **interval_consumption(series[key], start, end,
            max_gap_seconds=max_gap_seconds, interpolate=interpolate, factor=factor,
            estimate=True, **policy)}

    gas = calculate(gas_id, gas_factor) if gas_id else {"value": None, "status": "unconfigured"}
    meters = [calculate(key, electricity_factor) for key in electricity_ids]
    if not meters:
        return gas, meters, None, "unconfigured"
    failure = next((meter["status"] for meter in meters if meter["value"] is None), None)
    if failure:
        return gas, meters, None, failure
    value = sum((meter["value"] for meter in meters), Decimal("0"))
    status = ("estimated" if any(meter["status"] == "estimated" for meter in meters)
              else "interpolated" if any(meter["status"] == "interpolated" for meter in meters) else "measured")
    return gas, meters, value, status


def task_energy_note(gas, meters, electric_status):
    note = f"气耗：{STATUS_TEXT[gas['status']]}；电耗：{STATUS_TEXT[electric_status]}"
    for label, channel in [("气表", gas), *[(f"电表{index}", meter) for index, meter in enumerate(meters, 1)]]:
        if channel.get("status") == "estimated":
            note += (f"；{label}有效时段 {channel['covered_start']:%Y-%m-%d %H:%M:%S}"
                     f"～{channel['covered_end']:%Y-%m-%d %H:%M:%S}"
                     f"（覆盖{channel['covered_seconds']}秒 / 任务{channel['task_seconds']}秒）")
    return note


def observe_task_energy(cfg, source_key, usable, cutoff, start_date, end_date, *, records=None, channels=('gas', 'electricity')):
    if records is None:
        state, message, level = 'source_failed', '任务能耗取数失败，仪表数据不可用', logging.ERROR
        missing = estimates = None
    else:
        compared = [row for row in records if row.task_id in usable and usable[row.task_id][1] <= cutoff]
        if not compared:
            return
        states = [getattr(row, f'{channel}_data_status') for row in compared for channel in channels]
        valid = ('measured', 'estimated', 'interpolated')
        missing = sum(state not in valid for state in states)
        estimates = sum(state in ('estimated', 'interpolated') for state in states)
        if 'counter_reset' in states:
            state, message, level = 'counter_reset', '任务能耗受累计读数回退影响', logging.ERROR
        elif missing:
            state, message = 'missing_readings', '已结束任务缺少有效能耗读数'
            level = logging.ERROR if missing == len(states) else logging.WARNING
        elif len(compared) == len(usable):
            state, message, level = 'verified', '同范围任务能耗已重新完成取数和覆盖校验', logging.INFO
        else:
            return
    scope = sorted((str(task), start.isoformat(), end.isoformat()) for task, (start, end) in usable.items())
    data_quality(logger, 'ENERGY_TASK_DATA_QUALITY', message, scope=scope, level=level, state=state, line_id=cfg.device_id,
                 source_id=f'energy:{source_key}', stage='energy_tasks', period_start=start_date.isoformat(),
                 period_end=end_date.isoformat(), missing_count=missing, estimated_count=estimates)


@dataclass
class EnergyResult:
    records: list[EquipmentRecord]
    mode: str
    notice: str
    units: dict[str, str]
    source_failed: bool = False


class EquipmentEnergyService:
    def __init__(self, settings: Settings, client: EnergyReadingSource | None = None):
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

        def unavailable(message, *, source_failed=False):
            return EnergyResult(sort_equipment_records([
                replace(row, gas_data_status="unavailable", electricity_data_status="unavailable", energy_note=message)
                for row in clean]), "unavailable", message + "；缺失值不作为零值或模拟值。", units, source_failed)

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

        reference = context_records if context_records is not None else records
        # Aliases share one meter set and use the same overlap tolerance.
        intervals = task_intervals(
            (row for row in reference if row.equipment_id in cfg.energy_equipment_ids), cfg.timezone)
        conflicts = overlapping_energy_tasks(intervals)
        usable = {row.task_id: intervals[row.task_id] for row in clean
                  if row.task_id in intervals and row.task_id not in conflicts and row.equipment_id in cfg.energy_equipment_ids}
        series = {}
        if usable:
            padding = timedelta(seconds=max(cfg.energy_max_gap_seconds, policy.get("new_max_gap_seconds", 0)))
            first = min(start for start, _ in usable.values())
            last = max(end for _, end in usable.values())
            cutoff = datetime.now(ZoneInfo(cfg.timezone)).replace(tzinfo=None) - padding
            source_scope = (cfg.energy_api_base_url.rstrip('/'), cfg.energy_api_username, ids, cfg.timezone,
                            units, cfg.energy_gas_factor, cfg.energy_electricity_factor, cfg.energy_max_gap_seconds,
                            cfg.energy_allow_interpolation, policy)
            source_key = sha256(json.dumps(source_scope, default=str, sort_keys=True).encode()).hexdigest()
            try:
                readings, fresh = self.client.fetch_observed(ids, first - padding, last + padding, force=force)
                series = make_series(readings, ids, cfg.timezone)
            except EnergyApiError as exc:
                observe_task_energy(cfg, source_key, usable, cutoff, first.date(), last.date())
                return unavailable(str(exc), source_failed=True)

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

            gas, electricity, electric_value, electric_status = task_meter_consumption(
                series, start, end, gas_id=gas_id if gas_ready else None,
                electricity_ids=electric_ids if electric_ready else (), gas_factor=gas_factor,
                electricity_factor=electric_factor, max_gap_seconds=cfg.energy_max_gap_seconds,
                interpolate=cfg.energy_allow_interpolation, policy=policy)
            details.update(gas={**gas, "unit": units["gas"]}, electricity={"meters": electricity, "unit": units["electricity"]})
            output.append(replace(row, gas_consumption=gas["value"], electricity_consumption=electric_value,
                gas_data_status=gas["status"], electricity_data_status=electric_status, energy_details=details,
                energy_note=task_energy_note(gas, electricity, electric_status)))

        if usable and fresh:
            channels = tuple(channel for channel, ready in (('gas', gas_ready), ('electricity', electric_ready)) if ready)
            observe_task_energy(cfg, source_key, usable, cutoff, first.date(), last.date(), records=output, channels=channels)
        available = sum(row.gas_consumption is not None for row in output) + sum(row.electricity_consumption is not None for row in output)
        mode = "metered" if available == 2 * len(output) else "partial" if available else "unavailable"
        notice = "按已绑定设备的实际 START_TIME / END_TIME 计算累计读数差值；汇总仅覆盖有效任务区间（含期间待机），不是整日总耗量。"
        if cfg.energy_allow_interpolation:
            notice += "边界非采样时刻采用线性插值；采样稀疏时使用累计读数差，首尾缺数按平均速率估算，并保留覆盖时段。"
        else:
            notice += sampling_notice(cfg, cfg.energy_max_gap_seconds)
        if mode != "metered":
            notice += "部分或全部读数缺失；仅汇总已匹配记录，不将缺失计为零。"
        return EnergyResult(sort_equipment_records(output), mode, notice, units)
