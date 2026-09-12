from __future__ import annotations

import hashlib
import json
import logging
import statistics
from datetime import datetime
from decimal import Decimal
from typing import Callable
from zoneinfo import ZoneInfo

from app.config import Settings
from app.database.metadata import MetadataRepository
from app.models.domain import (
    AnalysisResult,
    EquipmentRecord,
    LeadershipReportResult,
    WorkshopRecord,
    to_primitive,
)
from app.services.llm_service import LlmService


logger = logging.getLogger(__name__)
PROMPT_VERSION = "report-analysis-v7-one-decimal"


def _duration(value: Decimal | int | float) -> str:
    seconds = max(0, int(round(float(value or 0))))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _number(value: Decimal | int | float, digits: int = 1) -> str:
    return f"{float(value or 0):,.{digits}f}"


def _change(current: Decimal | int | float, previous: Decimal | int | float) -> float | None:
    previous_value = float(previous or 0)
    if previous_value == 0:
        return None
    return (float(current or 0) - previous_value) / previous_value * 100


def _fact(identifier: str, label: str, value: str, source: str) -> dict[str, str]:
    return {"id": identifier, "label": label, "value": value, "source": source}


def _round_context_numbers(value):
    if isinstance(value, dict):
        return {key: _round_context_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_round_context_numbers(item) for item in value]
    if isinstance(value, float):
        return round(value, 1)
    return value


def _project_distribution(records: list[WorkshopRecord] | list[EquipmentRecord]) -> list[dict]:
    buckets: dict[str, dict[str, Decimal | int]] = {}
    for row in records:
        name = row.project_code or row.ship_no or "未标注工程"
        bucket = buckets.setdefault(
            name, {"task_count": 0, "cut_length": Decimal("0"), "work_time": Decimal("0")}
        )
        bucket["task_count"] = int(bucket["task_count"]) + 1
        bucket["cut_length"] = Decimal(bucket["cut_length"]) + (row.cut_length or Decimal("0"))
        bucket["work_time"] = Decimal(bucket["work_time"]) + (row.work_time or Decimal("0"))
    return [
        {
            "project_name": name,
            "task_count": int(values["task_count"]),
            "cut_length": float(values["cut_length"]),
            "work_time": float(values["work_time"]),
        }
        for name, values in sorted(
            buckets.items(), key=lambda item: (-int(item[1]["task_count"]), item[0])
        )
    ]


def _work_time_outliers(
    records: list[WorkshopRecord] | list[EquipmentRecord],
) -> list[dict]:
    valid = [row for row in records if row.work_time is not None and row.work_time > 0]
    if len(valid) < 4:
        return []
    values = [float(row.work_time or 0) for row in valid]
    q1, _, q3 = statistics.quantiles(values, n=4, method="inclusive")
    threshold = q3 + 1.5 * (q3 - q1)
    if threshold <= q3:
        threshold = q3 * 2
    return [
        {
            "task_id": row.task_id,
            "program_no": getattr(row, "program_no", None),
            "project_name": row.project_code or row.ship_no or "未标注工程",
            "work_time": float(row.work_time or 0),
            "threshold": round(threshold, 1),
        }
        for row in sorted(valid, key=lambda item: item.work_time or 0, reverse=True)
        if float(row.work_time or 0) > threshold
    ][:5]


class ReportAnalysisService:
    def __init__(self, settings: Settings, metadata: MetadataRepository) -> None:
        self.settings = settings
        self.metadata = metadata
        self.llm = LlmService(settings)
        self.timezone = ZoneInfo(settings.timezone)

    def _analyze(
        self,
        *,
        report_type: str,
        report_date: str,
        context: dict,
        simulator: Callable[[dict], AnalysisResult],
        force: bool,
    ) -> AnalysisResult:
        primitive_context = _round_context_numbers(to_primitive(context))
        canonical = json.dumps(
            primitive_context, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        input_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        engine = (
            f"model:{self.settings.llm_model}"
            if self.settings.llm_enabled
            else "simulated"
        )
        cache_key = hashlib.sha256(
            f"{PROMPT_VERSION}|{report_type}|{report_date}|{engine}|{input_hash}".encode(
                "utf-8"
            )
        ).hexdigest()
        if not force:
            cached = self.metadata.get_analysis(cache_key)
            if cached:
                allowed = {
                    key: value
                    for key, value in cached.items()
                    if key in AnalysisResult.__dataclass_fields__
                }
                allowed["cached"] = True
                return AnalysisResult(**allowed)

        mode = "simulated"
        if self.settings.llm_enabled:
            try:
                result = self.llm.analyze_context(report_type, primitive_context)
                mode = "model"
            except Exception:
                logger.exception("LLM analysis failed; falling back to simulated analysis")
                result = simulator(primitive_context)
                result.status = "degraded"
                result.mode = "simulated_fallback"
                mode = "simulated_fallback"
        else:
            result = simulator(primitive_context)

        result.facts = primitive_context.get("facts", [])
        result.generated_at = datetime.now(self.timezone).isoformat(timespec="seconds")
        result.cached = False
        payload = to_primitive(result)
        self.metadata.save_analysis(
            cache_key=cache_key,
            report_type=report_type,
            report_date=report_date,
            input_hash=input_hash,
            status=result.status,
            mode=mode,
            model_name=self.settings.llm_model if self.settings.llm_enabled else None,
            prompt_version=PROMPT_VERSION,
            result=payload,
            generated_at=result.generated_at,
        )
        return result

    def analyze_leadership(
        self, report: LeadershipReportResult, *, force: bool = False
    ) -> AnalysisResult:
        current = report.current_month_total
        previous_candidates = [
            item
            for item in report.monthly_records
            if item.period_start < report.report_month
        ]
        previous = report.comparison_total or max(
            previous_candidates, key=lambda item: item.period_start, default=None
        )
        range_mode = report.period_start is not None and report.period_end is not None
        period_label = (
            f"{report.period_start.isoformat()} 至 {report.period_end.isoformat()}"
            if range_mode
            else report.report_month.isoformat()[:7]
        )
        period_name = "区间" if range_mode else "当月"
        month_change = _change(current.task_count, previous.task_count) if previous else None
        year_change = (
            _change(current.task_count, report.year_ago_total.task_count)
            if report.year_ago_total
            else None
        )
        length_month_change = (
            _change(current.cut_length_m, previous.cut_length_m) if previous else None
        )
        projects = [
            {
                "project_name": item.project_name,
                "task_count": item.task_count,
                "cut_length_m": float(item.cut_length_m),
                "work_time_seconds": item.work_time_seconds,
            }
            for item in report.project_distribution[:10]
        ]
        daily_counts = [item.task_count for item in report.daily_records]
        daily_anomalies: list[dict] = []
        if len(daily_counts) >= 5:
            average = statistics.mean(daily_counts)
            deviation = statistics.pstdev(daily_counts)
            threshold = average + 1.5 * deviation
            daily_anomalies = [
                {
                    "date": item.period_start.isoformat(),
                    "task_count": item.task_count,
                    "threshold": round(threshold, 1),
                }
                for item in report.daily_records
                if deviation > 0 and item.task_count > threshold
            ]
        facts = [
            _fact("F001", f"{period_name}任务数", f"{current.task_count} 项", f"{period_name}汇总.task_count"),
            _fact("F002", f"{period_name}切割长度", f"{_number(current.cut_length_m)} 米", f"{period_name}汇总.cut_length_m"),
            _fact("F003", f"{period_name}切割时长", _duration(current.work_time_seconds), f"{period_name}汇总.work_time_seconds"),
            _fact("F004", "任务数环比", f"{month_change:+.1f}%" if month_change is not None else "无法比较", f"({period_name}任务数-前一等长区间任务数)/前一等长区间任务数" if range_mode else "(当月任务数-上月任务数)/上月任务数"),
            _fact("F005", "任务数同比", f"{year_change:+.1f}%" if year_change is not None else "无法比较", "(当月任务数-上年同月任务数)/上年同月任务数"),
            _fact("F006", "主要工程", f"{projects[0]['project_name']} · {projects[0]['task_count']} 项 · {projects[0]['task_count'] / current.task_count * 100:.1f}%" if projects and current.task_count else "暂无工程数据", "当月工程分布中任务最多工程及其任务数/当月任务数"),
        ]
        context = {
            "facts": facts,
            "report_month": period_label,
            "comparisons": {
                "task_mom_percent": round(month_change, 1) if month_change is not None else None,
                "cut_length_mom_percent": round(length_month_change, 1) if length_month_change is not None else None,
                "task_yoy_percent": round(year_change, 1) if year_change is not None else None,
            },
            "project_distribution": projects,
            "anomalies": daily_anomalies,
        }
        return self._analyze(
            report_type="leadership",
            report_date=period_label,
            context=context,
            simulator=self._simulate_leadership,
            force=force,
        )

    @staticmethod
    def _simulate_leadership(context: dict) -> AnalysisResult:
        fact = {item["id"]: item["value"] for item in context["facts"]}
        return AnalysisResult(
            status="simulated",
            mode="simulated",
            overview=f"{context['report_month']} 的月度任务量环比：{fact['F004']} [F004]。",
            features=f"任务数同比：{fact['F005']} [F005]。主要工程：{fact['F006']} [F006]。",
            alerts="任务数和切割长度反映生产规模，不能直接等同于产能利用率或计划达成率 [F001][F002]。",
            suggestions="建议结合任务量变化与主要工程，核对生产计划和资源安排 [F004][F005][F006]。",
        )

    def analyze_workshop(
        self,
        report_date: str,
        records: list[WorkshopRecord],
        *,
        comparison_records: list[WorkshopRecord] | None = None,
        comparison_date: str | None = None,
        year_ago_records: list[WorkshopRecord] | None = None,
        year_ago_date: str | None = None,
        force: bool = False,
    ) -> AnalysisResult:
        confirmed = sum(1 for row in records if row.confirmed_by)
        total_work = sum((row.work_time or Decimal("0") for row in records), Decimal("0"))
        total_cut = sum((row.cut_length or Decimal("0") for row in records), Decimal("0"))
        projects = _project_distribution(records)
        anomalies = _work_time_outliers(records)
        previous_count = len(comparison_records) if comparison_records is not None else None
        previous_cut = (
            sum((row.cut_length or Decimal("0") for row in comparison_records), Decimal("0"))
            if comparison_records is not None
            else None
        )
        task_change = _change(len(records), previous_count) if previous_count is not None else None
        cut_change = _change(total_cut, previous_cut) if previous_cut is not None else None
        year_ago_count = len(year_ago_records) if year_ago_records is not None else None
        year_change = _change(len(records), year_ago_count) if year_ago_count is not None else None
        facts = [
            _fact("F001", "任务总数", f"{len(records)} 项", f"车间日报[{report_date}].任务数"),
            _fact("F002", "总切割长度", f"{_number(total_cut)} 米", f"车间日报[{report_date}].cut_length 汇总"),
            _fact("F003", "总切割时长", _duration(total_work), f"车间日报[{report_date}].work_time 汇总"),
            _fact("F004", "工人确认率", f"{confirmed / len(records) * 100:.1f}%" if records else "暂无任务", "已确认任务/任务总数"),
            _fact("F005", "未确认任务", f"{len(records) - confirmed} 项", "任务总数-已确认任务"),
            _fact("F006", "工时异常任务", f"{len(anomalies)} 项", "work_time > Q3 + 1.5×IQR（最多列出5项）"),
        ]
        context = {
            "facts": facts,
            "report_date": report_date,
            "confirmation_rate": round(confirmed / len(records) * 100, 1) if records else 0,
            "comparisons": {
                "comparison_date": comparison_date,
                "task_change_percent": round(task_change, 1) if task_change is not None else None,
                "cut_length_change_percent": round(cut_change, 1) if cut_change is not None else None,
                "year_ago_date": year_ago_date,
                "task_yoy_percent": round(year_change, 1) if year_change is not None else None,
            },
            "project_distribution": projects[:10],
            "anomalies": anomalies,
        }
        return self._analyze(
            report_type="workshop",
            report_date=report_date,
            context=context,
            simulator=self._simulate_workshop,
            force=force,
        )

    @staticmethod
    def _simulate_workshop(context: dict) -> AnalysisResult:
        fact = {item["id"]: item["value"] for item in context["facts"]}
        return AnalysisResult(
            status="simulated",
            mode="simulated",
            overview=f"{context['report_date']} 共纳入 {fact['F001']} 生产任务 [F001]。",
            features=f"当前工人确认率：{fact['F004']} [F004]。切割长度与时长应结合任务类型评价 [F002][F003]。",
            alerts=f"检出工时异常任务 {fact['F006']} [F006]；仍有 {fact['F005']} 未完成工人确认 [F005]。",
            suggestions="建议优先完成未确认任务，并结合程序、材料和现场记录复核工时异常任务 [F005][F006]。",
        )

    def analyze_equipment(
        self,
        report_date: str,
        records: list[EquipmentRecord],
        *,
        energy_data_mode: str = "unavailable",
        energy_units: dict[str, str] | None = None,
        energy_notice: str = "",
        comparison_records: list[EquipmentRecord] | None = None,
        comparison_date: str | None = None,
        year_ago_records: list[EquipmentRecord] | None = None,
        year_ago_date: str | None = None,
        force: bool = False,
    ) -> AnalysisResult:
        total_work = sum((row.work_time or Decimal("0") for row in records), Decimal("0"))
        total_gas = sum((row.gas_consumption or Decimal("0") for row in records), Decimal("0"))
        total_electricity = sum((row.electricity_consumption or Decimal("0") for row in records), Decimal("0"))
        units = energy_units or ({"gas": "Nm³", "electricity": "kWh"} if energy_data_mode == "mock" else {"gas": "单位待确认", "electricity": "单位待确认"})
        gas_count = sum(row.gas_consumption is not None for row in records)
        electric_count = sum(row.electricity_consumption is not None for row in records)
        gas_estimates = sum(row.gas_data_status == "interpolated" for row in records)
        electric_estimates = sum(row.electricity_data_status == "interpolated" for row in records)
        overlap_count = sum(
            "overlap" in {row.gas_data_status, row.electricity_data_status}
            for row in records
        )
        invalid_interval_count = sum(
            "invalid_interval" in {row.gas_data_status, row.electricity_data_status}
            for row in records
        )
        sampling_issue_statuses = {
            "missing", "gap", "counter_reset", "invalid_reading", "unavailable"
        }
        sampling_issue_count = sum(
            bool(
                {row.gas_data_status, row.electricity_data_status}
                & sampling_issue_statuses
            )
            for row in records
        )

        def energy_fact(total, count, unit):
            if not count:
                return "缺失"
            return f"{_number(total)} {unit}"
        projects = _project_distribution(records)
        anomalies = _work_time_outliers(records)
        previous_count = len(comparison_records) if comparison_records is not None else None
        task_change = _change(len(records), previous_count) if previous_count is not None else None
        year_ago_count = len(year_ago_records) if year_ago_records is not None else None
        year_change = _change(len(records), year_ago_count) if year_ago_count is not None else None
        quality_statuses = sampling_issue_statuses | {"overlap", "invalid_interval", "unconfigured", "unmapped"}
        quality_count = sum(
            row.gas_consumption is None
            or row.electricity_consumption is None
            or bool({row.gas_data_status, row.electricity_data_status} & quality_statuses)
            for row in records
        )
        facts = [
            _fact("F001", "任务总数", f"{len(records)} 项", f"设备日报[{report_date}].任务数"),
            _fact("F002", "总切割时长", _duration(total_work), f"设备日报[{report_date}].work_time 汇总"),
            _fact("F003", "已匹配任务气耗", energy_fact(total_gas, gas_count, units["gas"]), "设备日报.gas_consumption 非空记录汇总"),
            _fact("F004", "已匹配任务电耗", energy_fact(total_electricity, electric_count, units["electricity"]), "设备日报.electricity_consumption 非空记录汇总"),
            _fact("F005", "工时异常任务", f"{len(anomalies)} 项", "work_time > Q3 + 1.5×IQR（最多列出5项）"),
            _fact("F006", "能耗数据待核对任务", f"{quality_count} 项", f"气耗或电耗缺失/异常的任务计数，同一任务只计一次；计量区间重叠 {overlap_count} 项，采样或读数缺口 {sampling_issue_count} 项，起止时间异常 {invalid_interval_count} 项；另含未配置/未绑定记录"),
        ]
        context = {
            "facts": facts,
            "report_date": report_date,
            "energy_data_mode": energy_data_mode,
            "energy_coverage": {"gas": gas_count, "electricity": electric_count, "tasks": len(records)},
            "energy_estimates": {"gas": gas_estimates, "electricity": electric_estimates},
            "energy_quality": {
                "overlap": overlap_count,
                "sampling_issue": sampling_issue_count,
                "invalid_interval": invalid_interval_count,
            },
            "comparisons": {
                "comparison_date": comparison_date,
                "task_change_percent": round(task_change, 1) if task_change is not None else None,
                "year_ago_date": year_ago_date,
                "task_yoy_percent": round(year_change, 1) if year_change is not None else None,
            },
            "project_distribution": projects[:10],
            "anomalies": anomalies,
        }
        return self._analyze(
            report_type="equipment",
            report_date=report_date,
            context=context,
            simulator=self._simulate_equipment,
            force=force,
        )

    @staticmethod
    def _simulate_equipment(context: dict) -> AnalysisResult:
        fact = {item["id"]: item["value"] for item in context["facts"]}
        energy_note = (
            "气耗、电耗当前为模拟值，本次不进行能耗异常判定 [F003][F004]。"
            if context.get("energy_data_mode") == "mock"
            else "气耗、电耗仅汇总已匹配任务，缺失不等于零，不进行能耗异常判定 [F003][F004]。"
        )
        mode = context.get("energy_data_mode")
        recommendation = (
            "接入真实电表、气表及设备状态数据"
            if mode == "mock"
            else "检查内网连接、仪表参数编号和采样时间覆盖"
            if mode == "unavailable"
            else "复核缺失采样、重叠任务和插值估算，补充设备状态与能耗基准"
        )
        return AnalysisResult(
            status="simulated",
            mode="simulated",
            overview=f"{context['report_date']} 共纳入 {fact['F001']} 设备任务 [F001]，总切割时长 {fact['F002']} [F002]。",
            features=energy_note,
            alerts=f"检出工时异常任务 {fact['F005']} [F005]；能耗数据待核对任务 {fact['F006']} [F006]。",
            suggestions=f"建议{recommendation}，并核对工时异常任务的现场记录 [F005][F006]。",
        )
