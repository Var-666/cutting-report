from __future__ import annotations

import hashlib
import json
import logging
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
PROMPT_VERSION = "report-analysis-v1"


def _duration(value: Decimal | int | float) -> str:
    seconds = max(0, int(round(float(value or 0))))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _number(value: Decimal | int | float, digits: int = 3) -> str:
    return f"{float(value or 0):,.{digits}f}"


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
        change = None
        if previous and previous.task_count:
            change = (current.task_count - previous.task_count) / previous.task_count * 100
        facts = [
            {"id": "F001", "label": "当月任务数", "value": f"{current.task_count} 项"},
            {"id": "F002", "label": "当月切割长度", "value": f"{_number(current.cut_length_m)} 米"},
            {"id": "F003", "label": "当月切割时长", "value": _duration(current.work_time_seconds)},
        ]
        if previous:
            facts.append({"id": "F004", "label": "上月任务数", "value": f"{previous.task_count} 项"})
        if peak:
            facts.append({"id": "F005", "label": "任务峰值日期", "value": f"{peak.period_start.isoformat()}（{peak.task_count} 项）"})
        if change is not None:
            facts.append({"id": "F006", "label": "任务数环比", "value": f"{change:+.2f}%"})
        context = {
            "facts": facts,
            "report_month": report.report_month.isoformat()[:7],
            "task_change_percent": round(change, 2) if change is not None else None,
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
        change = context.get("task_change_percent")
        comparison = (
            f"较上月任务数变化 {change:+.2f}%。"
            if change is not None
            else "缺少可比较的上月任务数据。"
        )
        peak = f"任务峰值为 {fact['F005']}。" if "F005" in fact else "当月没有可识别的任务峰值日期。"
        return AnalysisResult(
            status="simulated",
            mode="simulated",
            overview=f"{context['report_month']} 共完成 {fact['F001']}，切割长度 {fact['F002']}，累计切割时长 {fact['F003']}。",
            features=f"{comparison}{peak}",
            alerts="当前结果未结合计划目标和停机原因，不宜仅凭产量波动判断管理异常。",
            suggestions="建议优先复核峰值日期的任务构成，并结合生产计划、班次与停机记录解释月度变化。",
            limitations=context["limitations"],
        )

    def analyze_workshop(
        self,
        report_date: str,
        records: list[WorkshopRecord],
        *,
        force: bool = False,
    ) -> AnalysisResult:
        confirmed = sum(1 for row in records if row.confirmed_by)
        total_work = sum((row.work_time or Decimal("0") for row in records), Decimal("0"))
        total_cut = sum((row.cut_length or Decimal("0") for row in records), Decimal("0"))
        longest = max(records, key=lambda row: row.work_time or 0, default=None)
        facts = [
            {"id": "F001", "label": "任务总数", "value": f"{len(records)} 项"},
            {"id": "F002", "label": "已确认任务", "value": f"{confirmed} 项"},
            {"id": "F003", "label": "未确认任务", "value": f"{len(records) - confirmed} 项"},
            {"id": "F004", "label": "总切割长度", "value": f"{_number(total_cut)} 米"},
            {"id": "F005", "label": "总切割时长", "value": _duration(total_work)},
            {"id": "F006", "label": "工人确认率", "value": f"{confirmed / len(records) * 100:.2f}%" if records else "0.00%"},
        ]
        if longest:
            facts.append({"id": "F007", "label": "最长任务", "value": f"{longest.program_no or '--'}（{_duration(longest.work_time or 0)}）"})
        context = {
            "facts": facts,
            "report_date": report_date,
            "confirmation_rate": round(confirmed / len(records) * 100, 2) if records else 0,
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
        longest = f"最长任务为 {fact['F007']}。" if "F007" in fact else "没有可识别的最长任务。"
        return AnalysisResult(
            status="simulated",
            mode="simulated",
            overview=f"{context['report_date']} 共 {fact['F001']}，累计切割长度 {fact['F004']}，切割时长 {fact['F005']}。",
            features=f"当前已确认 {fact['F002']}，确认率 {context['confirmation_rate']:.2f}%；{longest}",
            alerts=f"仍有 {fact['F003']} 未完成工人确认；确认结果变化后应重新分析。",
            suggestions="建议先完成任务认领，再结合最长任务的程序、材料和现场记录核对是否存在等待或返工。",
            limitations=context["limitations"],
        )

    def analyze_equipment(
        self,
        report_date: str,
        records: list[EquipmentRecord],
        *,
        energy_data_mode: str = "mock",
        force: bool = False,
    ) -> AnalysisResult:
        total_work = sum((row.work_time or Decimal("0") for row in records), Decimal("0"))
        total_gas = sum((row.gas_consumption or Decimal("0") for row in records), Decimal("0"))
        total_electricity = sum((row.electricity_consumption or Decimal("0") for row in records), Decimal("0"))
        longest = max(records, key=lambda row: row.work_time or 0, default=None)
        peak_electricity = max(records, key=lambda row: row.electricity_consumption or 0, default=None)
        facts = [
            {"id": "F001", "label": "任务总数", "value": f"{len(records)} 项"},
            {"id": "F002", "label": "总切割时长", "value": _duration(total_work)},
            {"id": "F003", "label": "总气耗", "value": f"{_number(total_gas)} Nm³" + ("（模拟）" if energy_data_mode == "mock" else "")},
            {"id": "F004", "label": "总电耗", "value": f"{_number(total_electricity)} kWh" + ("（模拟）" if energy_data_mode == "mock" else "")},
        ]
        if longest:
            facts.append({"id": "F005", "label": "最长切割任务", "value": f"{longest.production_time.strftime('%Y-%m-%d %H:%M') if longest.production_time else '--'}（{_duration(longest.work_time or 0)}）"})
        if peak_electricity:
            facts.append({"id": "F006", "label": "单任务最高电耗", "value": f"{_number(peak_electricity.electricity_consumption or 0)}"})
        context = {
            "facts": facts,
            "report_date": report_date,
            "energy_data_mode": energy_data_mode,
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
        longest = f"最长切割任务为 {fact['F005']}。" if "F005" in fact else "没有可识别的最长任务。"
        energy_note = (
            "气耗、电耗当前为模拟值，本次不进行能耗异常判定。"
            if context.get("energy_data_mode") == "mock"
            else f"记录总气耗 {fact['F003']}、总电耗 {fact['F004']}。"
        )
        return AnalysisResult(
            status="simulated",
            mode="simulated",
            overview=f"{context['report_date']} 共 {fact['F001']}，总切割时长 {fact['F002']}。",
            features=f"{longest}{energy_note}",
            alerts="缺少真实计量和设备运行状态，暂不能识别高能耗、空转或故障风险。",
            suggestions="建议接入真实电表、气表及设备状态数据后，再建立单位工时能耗基线和异常阈值。",
            limitations=context["limitations"],
        )
