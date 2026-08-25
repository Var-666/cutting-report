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
PROMPT_VERSION = "report-analysis-v3"


def _duration(value: Decimal | int | float) -> str:
    seconds = max(0, int(round(float(value or 0))))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _number(value: Decimal | int | float, digits: int = 3) -> str:
    return f"{float(value or 0):,.{digits}f}"


def _change(current: Decimal | int | float, previous: Decimal | int | float) -> float | None:
    previous_value = float(previous or 0)
    if previous_value == 0:
        return None
    return (float(current or 0) - previous_value) / previous_value * 100


def _fact(identifier: str, label: str, value: str, source: str) -> dict[str, str]:
    return {"id": identifier, "label": label, "value": value, "source": source}


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
            "threshold": round(threshold, 2),
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
        primitive_context = to_primitive(context)
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
                result.limitations = (
                    "真实模型服务本次不可用，当前展示规则化模拟分析。"
                    + (f" {result.limitations}" if result.limitations else "")
                )
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
        previous = max(previous_candidates, key=lambda item: item.period_start, default=None)
        peak = max(
            report.daily_records,
            key=lambda item: item.task_count,
            default=None,
        )
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
                    "threshold": round(threshold, 2),
                }
                for item in report.daily_records
                if deviation > 0 and item.task_count > threshold
            ]
        facts = [
            _fact("F001", "当月任务数", f"{current.task_count} 项", "当月汇总.task_count"),
            _fact("F002", "当月切割长度", f"{_number(current.cut_length_m)} 米", "当月汇总.cut_length_m"),
            _fact("F003", "当月切割时长", _duration(current.work_time_seconds), "当月汇总.work_time_seconds"),
        ]
        if previous:
            facts.append(_fact("F004", "上月任务数", f"{previous.task_count} 项", f"月度汇总[{previous.period_start:%Y-%m}].task_count"))
        if peak:
            facts.append(_fact("F005", "任务峰值日期", f"{peak.period_start.isoformat()}（{peak.task_count} 项）", "当月日汇总.task_count 最大值"))
        if month_change is not None:
            facts.append(_fact("F006", "任务数环比", f"{month_change:+.2f}%", "(当月-上月)/上月"))
        if report.year_ago_total:
            facts.append(_fact("F007", "上年同月任务数", f"{report.year_ago_total.task_count} 项", f"月度汇总[{report.year_ago_total.period_start:%Y-%m}].task_count"))
        if year_change is not None:
            facts.append(_fact("F008", "任务数同比", f"{year_change:+.2f}%", "(当月-上年同月)/上年同月"))
        if projects:
            facts.append(_fact("F009", "任务最多工程", f"{projects[0]['project_name']}（{projects[0]['task_count']} 项）", "当月工程分布.task_count 最大值"))
        facts.append(_fact("F010", "高产量异常日", f"{len(daily_anomalies)} 天", "日任务数 > 均值 + 1.5×标准差"))
        if length_month_change is not None:
            facts.append(_fact("F011", "切割长度环比", f"{length_month_change:+.2f}%", "(当月-上月)/上月"))
        if projects and current.task_count:
            share = projects[0]["task_count"] / current.task_count * 100
            facts.append(_fact("F012", "主要工程任务占比", f"{share:.2f}%", "任务最多工程任务数/当月任务数"))
        context = {
            "facts": facts,
            "report_month": report.report_month.isoformat()[:7],
            "comparisons": {
                "task_mom_percent": round(month_change, 2) if month_change is not None else None,
                "cut_length_mom_percent": round(length_month_change, 2) if length_month_change is not None else None,
                "task_yoy_percent": round(year_change, 2) if year_change is not None else None,
            },
            "project_distribution": projects,
            "anomalies": daily_anomalies,
            "limitations": "未提供生产计划、产能目标、停机和班次数据，不能判断计划达成率。",
        }
        return self._analyze(
            report_type="leadership",
            report_date=report.report_month.isoformat(),
            context=context,
            simulator=self._simulate_leadership,
            force=force,
        )

    @staticmethod
    def _simulate_leadership(context: dict) -> AnalysisResult:
        fact = {item["id"]: item["value"] for item in context["facts"]}
        change = context["comparisons"].get("task_mom_percent")
        year_change = context["comparisons"].get("task_yoy_percent")
        comparison = (
            f"任务数较上月变化 {change:+.2f}% [F006]。"
            if change is not None
            else "缺少可比较的上月任务数据。"
        )
        yoy = f"同比变化 {year_change:+.2f}% [F008]。" if year_change is not None else "缺少上年同月可比数据。"
        peak = f"任务峰值为 {fact['F005']} [F005]。" if "F005" in fact else "当月没有可识别的任务峰值日期。"
        project = f"任务最多工程为 {fact['F009']} [F009]。" if "F009" in fact else "未获取工程分布。"
        review_targets = [identifier for identifier in ("F005", "F009") if identifier in fact]
        review_text = " ".join(f"[{identifier}]" for identifier in review_targets)
        return AnalysisResult(
            status="simulated",
            mode="simulated",
            overview=f"{context['report_month']} 共完成 {fact['F001']} [F001]，切割长度 {fact['F002']} [F002]，累计切割时长 {fact['F003']} [F003]。",
            features=f"{comparison}{yoy}{project}{peak}",
            alerts=(
                f"检出 {fact['F010']} [F010]，需核对异常日的任务组成。"
                if context.get("anomalies")
                else f"未按当前统计阈值识别到高产量异常日（{fact['F010']}）[F010]；仍不能排除未接入的停机或质量问题。"
            ),
            suggestions=(
                f"建议优先复核关键日期和工程 {review_text}，并结合生产计划、班次与停机记录解释变化。"
                if review_text
                else "建议补充生产计划、班次与停机记录后再进行原因分析。"
            ),
            limitations=context["limitations"],
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
        longest = max(records, key=lambda row: row.work_time or 0, default=None)
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
            _fact("F002", "已确认任务", f"{confirmed} 项", f"车间日报[{report_date}].confirmed_by 非空"),
            _fact("F003", "未确认任务", f"{len(records) - confirmed} 项", "任务总数-已确认任务"),
            _fact("F004", "总切割长度", f"{_number(total_cut)} 米", f"车间日报[{report_date}].cut_length 汇总"),
            _fact("F005", "总切割时长", _duration(total_work), f"车间日报[{report_date}].work_time 汇总"),
            _fact("F006", "工人确认率", f"{confirmed / len(records) * 100:.2f}%" if records else "0.00%", "已确认任务/任务总数"),
        ]
        if longest:
            facts.append(_fact("F007", "最长任务", f"{longest.program_no or '--'}（{_duration(longest.work_time or 0)}）", "当日任务.work_time 最大值"))
        if previous_count is not None:
            facts.append(_fact("F008", "对比日任务数", f"{previous_count} 项", f"车间日报[{comparison_date or '对比日'}].任务数"))
        if task_change is not None:
            facts.append(_fact("F009", "任务数日环比", f"{task_change:+.2f}%", "(当日-对比日)/对比日"))
        if year_ago_count is not None:
            facts.append(_fact("F010", "上年同日任务数", f"{year_ago_count} 项", f"车间日报[{year_ago_date or '上年同日'}].任务数"))
        if year_change is not None:
            facts.append(_fact("F011", "任务数同比", f"{year_change:+.2f}%", "(当日-上年同日)/上年同日"))
        if projects:
            facts.append(_fact("F012", "任务最多工程", f"{projects[0]['project_name']}（{projects[0]['task_count']} 项）", "当日工程分布.task_count 最大值"))
        facts.append(_fact("F013", "工时异常任务", f"{len(anomalies)} 项", "work_time > Q3 + 1.5×IQR"))
        context = {
            "facts": facts,
            "report_date": report_date,
            "confirmation_rate": round(confirmed / len(records) * 100, 2) if records else 0,
            "comparisons": {
                "comparison_date": comparison_date,
                "task_change_percent": round(task_change, 2) if task_change is not None else None,
                "cut_length_change_percent": round(cut_change, 2) if cut_change is not None else None,
                "year_ago_date": year_ago_date,
                "task_yoy_percent": round(year_change, 2) if year_change is not None else None,
            },
            "project_distribution": projects[:10],
            "anomalies": anomalies,
            "limitations": "未提供班组、计划工期、人员排班和质量结果，不能据此评价个人绩效。",
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
        longest = f"最长任务为 {fact['F007']} [F007]。" if "F007" in fact else "没有可识别的最长任务。"
        comparison = context["comparisons"].get("task_change_percent")
        comparison_text = f"任务数较对比日变化 {comparison:+.2f}% [F009]。" if comparison is not None else "暂无可用的前一日对比数据。"
        yoy = context["comparisons"].get("task_yoy_percent")
        yoy_text = f"任务数同比变化 {yoy:+.2f}% [F011]。" if yoy is not None else "暂无可用的上年同日数据。"
        project = f"任务最多工程为 {fact['F012']} [F012]。" if "F012" in fact else ""
        return AnalysisResult(
            status="simulated",
            mode="simulated",
            overview=f"{context['report_date']} 共 {fact['F001']} [F001]，累计切割长度 {fact['F004']} [F004]，切割时长 {fact['F005']} [F005]。",
            features=f"当前已确认 {fact['F002']} [F002]，确认率 {context['confirmation_rate']:.2f}% [F006]。{comparison_text}{yoy_text}{project}{longest}",
            alerts=(f"检出 {fact['F013']} [F013]，同时仍有 {fact['F003']} 未完成工人确认 [F003]。" if context.get("anomalies") else f"未按 IQR 阈值检出工时异常（{fact['F013']}）[F013]；仍有 {fact['F003']} 未完成工人确认 [F003]。"),
            suggestions=("建议先完成未确认任务 [F003]，并优先核对最长任务 [F007] 的材料、程序和现场记录。" if "F007" in fact else "建议先完成未确认任务 [F003]，再结合现场记录复核。"),
            limitations=context["limitations"],
        )

    def analyze_equipment(
        self,
        report_date: str,
        records: list[EquipmentRecord],
        *,
        energy_data_mode: str = "mock",
        comparison_records: list[EquipmentRecord] | None = None,
        comparison_date: str | None = None,
        year_ago_records: list[EquipmentRecord] | None = None,
        year_ago_date: str | None = None,
        force: bool = False,
    ) -> AnalysisResult:
        total_work = sum((row.work_time or Decimal("0") for row in records), Decimal("0"))
        total_gas = sum((row.gas_consumption or Decimal("0") for row in records), Decimal("0"))
        total_electricity = sum((row.electricity_consumption or Decimal("0") for row in records), Decimal("0"))
        longest = max(records, key=lambda row: row.work_time or 0, default=None)
        peak_electricity = max(records, key=lambda row: row.electricity_consumption or 0, default=None)
        projects = _project_distribution(records)
        anomalies = _work_time_outliers(records)
        previous_count = len(comparison_records) if comparison_records is not None else None
        task_change = _change(len(records), previous_count) if previous_count is not None else None
        year_ago_count = len(year_ago_records) if year_ago_records is not None else None
        year_change = _change(len(records), year_ago_count) if year_ago_count is not None else None
        facts = [
            _fact("F001", "任务总数", f"{len(records)} 项", f"设备日报[{report_date}].任务数"),
            _fact("F002", "总切割时长", _duration(total_work), f"设备日报[{report_date}].work_time 汇总"),
            _fact("F003", "总气耗", f"{_number(total_gas)} Nm³" + ("（模拟）" if energy_data_mode == "mock" else ""), "设备日报.gas_consumption 汇总"),
            _fact("F004", "总电耗", f"{_number(total_electricity)} kWh" + ("（模拟）" if energy_data_mode == "mock" else ""), "设备日报.electricity_consumption 汇总"),
        ]
        if longest:
            facts.append(_fact("F005", "最长切割任务", f"{longest.production_time.strftime('%Y-%m-%d %H:%M') if longest.production_time else '--'}（{_duration(longest.work_time or 0)}）", "当日设备任务.work_time 最大值"))
        if peak_electricity:
            facts.append(_fact("F006", "单任务最高电耗", f"{_number(peak_electricity.electricity_consumption or 0)}" + ("（模拟）" if energy_data_mode == "mock" else ""), "electricity_consumption 最大值"))
        if previous_count is not None:
            facts.append(_fact("F007", "对比日任务数", f"{previous_count} 项", f"设备日报[{comparison_date or '对比日'}].任务数"))
        if task_change is not None:
            facts.append(_fact("F008", "任务数日环比", f"{task_change:+.2f}%", "(当日-对比日)/对比日"))
        if year_ago_count is not None:
            facts.append(_fact("F009", "上年同日任务数", f"{year_ago_count} 项", f"设备日报[{year_ago_date or '上年同日'}].任务数"))
        if year_change is not None:
            facts.append(_fact("F010", "任务数同比", f"{year_change:+.2f}%", "(当日-上年同日)/上年同日"))
        if projects:
            facts.append(_fact("F011", "任务最多工程", f"{projects[0]['project_name']}（{projects[0]['task_count']} 项）", "当日工程分布.task_count 最大值"))
        facts.append(_fact("F012", "工时异常任务", f"{len(anomalies)} 项", "work_time > Q3 + 1.5×IQR"))
        context = {
            "facts": facts,
            "report_date": report_date,
            "energy_data_mode": energy_data_mode,
            "comparisons": {
                "comparison_date": comparison_date,
                "task_change_percent": round(task_change, 2) if task_change is not None else None,
                "year_ago_date": year_ago_date,
                "task_yoy_percent": round(year_change, 2) if year_change is not None else None,
            },
            "project_distribution": projects[:10],
            "anomalies": anomalies,
            "limitations": (
                "气耗和电耗为模拟数据，不能用于能耗异常、设备故障、结算或考核判断。"
                if energy_data_mode == "mock"
                else "未提供设备额定参数、负载、停机和维修记录。"
            ),
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
        longest = f"最长切割任务为 {fact['F005']} [F005]。" if "F005" in fact else "没有可识别的最长任务。"
        comparison = context["comparisons"].get("task_change_percent")
        comparison_text = f"任务数较对比日变化 {comparison:+.2f}% [F008]。" if comparison is not None else "暂无可用的前一日对比数据。"
        yoy = context["comparisons"].get("task_yoy_percent")
        yoy_text = f"任务数同比变化 {yoy:+.2f}% [F010]。" if yoy is not None else "暂无可用的上年同日数据。"
        project = f"任务最多工程为 {fact['F011']} [F011]。" if "F011" in fact else ""
        energy_note = (
            "气耗、电耗当前为模拟值，本次不进行能耗异常判定 [F003][F004]。"
            if context.get("energy_data_mode") == "mock"
            else f"记录总气耗 {fact['F003']} [F003]、总电耗 {fact['F004']} [F004]。"
        )
        return AnalysisResult(
            status="simulated",
            mode="simulated",
            overview=f"{context['report_date']} 共 {fact['F001']} [F001]，总切割时长 {fact['F002']} [F002]。",
            features=f"{comparison_text}{yoy_text}{project}{longest}{energy_note}",
            alerts=(f"检出 {fact['F012']} [F012]；能耗为模拟值，不做能耗异常判定 [F003][F004]。" if context.get("anomalies") else f"未按 IQR 阈值检出工时异常（{fact['F012']}）[F012]；缺少真实计量和设备状态 [F003][F004]。"),
            suggestions=("建议优先复核最长切割任务 [F005]，并接入真实电表、气表及设备状态数据后建立单位工时能耗基线。" if "F005" in fact else "建议接入真实电表、气表及设备状态数据后建立能耗基线 [F003][F004]。"),
            limitations=context["limitations"],
        )
