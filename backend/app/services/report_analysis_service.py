from __future__ import annotations

import hashlib
import json
import logging
import statistics
import threading
from contextlib import contextmanager
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
from app.services.llm_service import AnalysisOutputValidationError, LlmService


logger = logging.getLogger(__name__)
PROMPT_VERSION = "report-analysis-v10-grounded-units-and-trends"


class AnalysisDataVersionMismatch(ValueError):
    """The report shown by the browser is no longer the authoritative version."""


def leadership_analysis_source_version(report: LeadershipReportResult | dict) -> str:
    data = to_primitive(report)
    relevant = {
        key: data.get(key)
        for key in (
            "report_month",
            "period_start",
            "period_end",
            "daily_records",
            "monthly_records",
            "current_month_total",
            "comparison_total",
            "comparison_start",
            "comparison_end",
            "year_ago_total",
            "project_distribution",
        )
    }
    return generate_data_version(relevant)


def workshop_analysis_source_version(report_date: str, records: list) -> str:
    def task_id(row) -> str:
        if isinstance(row, dict):
            return str(row.get("task_id") or "")
        return str(getattr(row, "task_id", ""))

    ordered = sorted(records, key=task_id)
    return generate_data_version(
        {"report_date": report_date, "records": to_primitive(ordered)}
    )


def equipment_analysis_source_version(task_report: dict) -> str:
    relevant = {
        key: task_report.get(key)
        for key in (
            "report_date",
            "period_start",
            "period_end",
            "records",
            "summary",
            "units",
            "equipment_scope",
            "energy_notice",
        )
    }
    relevant["records"] = sorted(
        task_report.get("records") or [],
        key=lambda row: str(row.get("task_id") or ""),
    )
    relevant["equipment_scope"] = sorted(task_report.get("equipment_scope") or [])
    return generate_data_version(relevant)


def _require_current_data_version(expected: str | None, actual: str) -> None:
    if expected and expected != actual:
        raise AnalysisDataVersionMismatch(
            "报表数据已更新，请刷新当前报表后重新生成智能分析。"
        )


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


def _change_label(value: float | None) -> str:
    if value is None:
        return "无法比较"
    if value > 0:
        return f"上升 {value:.1f}%"
    if value < 0:
        return f"下降 {abs(value):.1f}%"
    return "持平 0.0%"


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


def normalize_analysis_context(context: dict) -> tuple[dict, str]:
    """Return the exact model input and its stable, business-data-only version."""
    normalized = _round_context_numbers(to_primitive(context))
    canonical = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return normalized, canonical


def generate_data_version(context: dict) -> str:
    _, canonical = normalize_analysis_context(context)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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


def _equipment_breakdown(
    records: list[EquipmentRecord],
    *,
    dimension: str,
) -> list[dict]:
    buckets: dict[str, dict[str, Decimal | int]] = {}
    for row in records:
        if dimension == "equipment":
            name = row.equipment_id or "未标注设备"
        else:
            name = row.project_code or row.ship_no or "未标注工程"
        bucket = buckets.setdefault(
            name,
            {
                "task_count": 0,
                "work_time": Decimal("0"),
                "gas": Decimal("0"),
                "electricity": Decimal("0"),
                "gas_count": 0,
                "electricity_count": 0,
            },
        )
        bucket["task_count"] = int(bucket["task_count"]) + 1
        bucket["work_time"] = Decimal(bucket["work_time"]) + (
            row.work_time or Decimal("0")
        )
        if row.gas_consumption is not None:
            bucket["gas"] = Decimal(bucket["gas"]) + row.gas_consumption
            bucket["gas_count"] = int(bucket["gas_count"]) + 1
        if row.electricity_consumption is not None:
            bucket["electricity"] = (
                Decimal(bucket["electricity"]) + row.electricity_consumption
            )
            bucket["electricity_count"] = int(bucket["electricity_count"]) + 1
    return [
        {
            f"{dimension}_name": name,
            "task_count": int(values["task_count"]),
            "work_time": float(values["work_time"]),
            "gas_consumption": float(values["gas"]),
            "electricity_consumption": float(values["electricity"]),
            "gas_record_count": int(values["gas_count"]),
            "electricity_record_count": int(values["electricity_count"]),
        }
        for name, values in sorted(
            buckets.items(),
            key=lambda item: (-int(item[1]["task_count"]), item[0]),
        )
    ]


class ReportAnalysisService:
    def __init__(self, settings: Settings, metadata: MetadataRepository) -> None:
        self.settings = settings
        self.metadata = metadata
        self.llm = LlmService(settings)
        self.timezone = ZoneInfo(settings.timezone)
        self._generation_guard = threading.Lock()
        self._generation_locks: dict[str, dict] = {}

    @contextmanager
    def _single_generation(self, cache_key: str):
        """Coalesce concurrent generation for one version inside this process."""
        with self._generation_guard:
            state = self._generation_locks.get(cache_key)
            if state is None:
                state = {"lock": threading.Lock(), "users": 0}
                self._generation_locks[cache_key] = state
            was_inflight = state["lock"].locked()
            state["users"] += 1
        state["lock"].acquire()
        try:
            yield was_inflight
        finally:
            state["lock"].release()
            with self._generation_guard:
                state["users"] -= 1
                if state["users"] == 0:
                    self._generation_locks.pop(cache_key, None)

    def _get_cached_analysis(
        self,
        *,
        cache_key: str,
        report_type: str,
        report_date: str,
        data_version: str,
    ) -> AnalysisResult | None:
        cached = self.metadata.get_analysis(
            cache_key,
            report_type=report_type,
            report_date=report_date,
            data_version=data_version,
        )
        if not cached:
            return None
        allowed = {
            key: value
            for key, value in cached.items()
            if key in AnalysisResult.__dataclass_fields__
        }
        allowed["data_version"] = data_version
        allowed["cached"] = True
        return AnalysisResult(**allowed)

    def _analyze(
        self,
        *,
        report_type: str,
        report_date: str,
        context: dict,
        simulator: Callable[[dict], AnalysisResult],
        force: bool,
        source_data_version: str | None = None,
    ) -> AnalysisResult:
        primitive_context, canonical = normalize_analysis_context(context)
        input_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        data_version = source_data_version or input_hash
        engine = (
            f"model:{self.settings.llm_model}"
            if self.settings.llm_enabled
            else "simulated"
        )
        cache_key = hashlib.sha256(
            f"{PROMPT_VERSION}|{report_type}|{report_date}|{engine}|{data_version}|{input_hash}".encode(
                "utf-8"
            )
        ).hexdigest()
        if not force:
            cached = self._get_cached_analysis(
                cache_key=cache_key,
                report_type=report_type,
                report_date=report_date,
                data_version=data_version,
            )
            if cached:
                return cached

        with self._single_generation(cache_key) as waited_for_same_version:
            # Always recheck after acquiring the key lock. Normal requests share the
            # cache; simultaneous forced requests share the result generated by the
            # first waiter, while a later manual refresh still generates a new result.
            if not force or waited_for_same_version:
                cached = self._get_cached_analysis(
                    cache_key=cache_key,
                    report_type=report_type,
                    report_date=report_date,
                    data_version=data_version,
                )
                if cached:
                    return cached

            mode = "simulated"
            if self.settings.llm_enabled:
                try:
                    result = self.llm.analyze_context(report_type, primitive_context)
                    mode = "model"
                except AnalysisOutputValidationError as exc:
                    logger.warning("LLM analysis rejected by output guardrails: %s", exc)
                    result = simulator(primitive_context)
                    result.status = "degraded"
                    result.mode = "guardrail_fallback"
                    mode = "guardrail_fallback"
                except Exception:
                    logger.exception("LLM analysis failed; falling back to simulated analysis")
                    result = simulator(primitive_context)
                    result.status = "degraded"
                    result.mode = "simulated_fallback"
                    mode = "simulated_fallback"
            else:
                result = simulator(primitive_context)

            result.facts = primitive_context.get("facts", [])
            result.data_version = data_version
            result.generated_at = datetime.now(self.timezone).isoformat(timespec="seconds")
            result.cached = False
            payload = to_primitive(result)
            self.metadata.save_analysis(
                cache_key=cache_key,
                report_type=report_type,
                report_date=report_date,
                input_hash=input_hash,
                data_version=data_version,
                status=result.status,
                mode=mode,
                model_name=self.settings.llm_model if self.settings.llm_enabled else None,
                prompt_version=PROMPT_VERSION,
                result=payload,
                generated_at=result.generated_at,
            )
            return result

    def analyze_leadership(
        self,
        report: LeadershipReportResult,
        *,
        expected_data_version: str | None = None,
        force: bool = False,
    ) -> AnalysisResult:
        source_data_version = leadership_analysis_source_version(report)
        _require_current_data_version(expected_data_version, source_data_version)
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
        daily_threshold: float | None = None
        if len(daily_counts) >= 5:
            average = statistics.mean(daily_counts)
            deviation = statistics.pstdev(daily_counts)
            daily_threshold = average + 1.5 * deviation
            daily_anomalies = [
                {
                    "date": item.period_start.isoformat(),
                    "task_count": item.task_count,
                    "threshold": round(daily_threshold, 1),
                }
                for item in report.daily_records
                if deviation > 0 and item.task_count > daily_threshold
            ]
        previous_label = (
            f"{report.comparison_start.isoformat()} 至 {report.comparison_end.isoformat()}"
            if report.comparison_start and report.comparison_end
            else "上一对比周期"
        )
        year_ago_label = (
            report.report_month.replace(year=report.report_month.year - 1).isoformat()[:7]
            if report.year_ago_total
            else "上年同期"
        )
        project_value = (
            f"{projects[0]['project_name']} · {projects[0]['task_count']} 项 · "
            f"{projects[0]['task_count'] / current.task_count * 100:.1f}%"
            if projects and current.task_count
            else "暂无工程数据"
        )
        anomaly_value = (
            f"{len(daily_anomalies)} 天"
            if len(daily_counts) >= 5
            else "样本不足"
        )
        anomaly_source = (
            f"日任务数高位阈值 {daily_threshold:.1f} 项；命中 "
            + (
                "、".join(
                    f"{item['date']} {item['task_count']} 项"
                    for item in daily_anomalies
                )
                if daily_anomalies
                else "0 天"
            )
            if daily_threshold is not None
            else f"当前仅 {len(daily_counts)} 个日样本，少于 5 个，暂不判定高位生产日"
        )
        facts = [
            _fact(
                "F001",
                f"{period_name}任务数",
                f"{current.task_count} 项",
                f"报表周期 {period_label}；{period_name}汇总任务 {current.task_count} 项",
            ),
            _fact(
                "F002",
                f"{period_name}切割长度",
                f"{_number(current.cut_length_m)} 米",
                f"报表周期 {period_label}；{period_name}汇总切割长度 {_number(current.cut_length_m)} 米",
            ),
            _fact(
                "F003",
                f"{period_name}切割时长",
                _duration(current.work_time_seconds),
                f"报表周期 {period_label}；{period_name}汇总切割时长 {_duration(current.work_time_seconds)}",
            ),
            _fact(
                "F004",
                "任务数环比",
                _change_label(month_change),
                (
                    f"当前 {current.task_count} 项；{previous_label} "
                    f"{previous.task_count if previous else 0} 项；任务数环比 {_change_label(month_change)}；"
                    f"切割长度环比 {_change_label(length_month_change)}"
                    if previous
                    else "未取得上一可比周期数据，任务数和切割长度均无法比较"
                ),
            ),
            _fact(
                "F005",
                "任务数同比",
                _change_label(year_change),
                (
                    f"当前 {current.task_count} 项；{year_ago_label} "
                    f"{report.year_ago_total.task_count} 项；任务数同比 {_change_label(year_change)}"
                    if report.year_ago_total
                    else "未取得上年同期数据，任务数无法同比"
                ),
            ),
            _fact(
                "F006",
                "主要工程与高位日",
                f"{project_value}，高位生产日 {anomaly_value}",
                f"任务最多工程：{project_value}；{anomaly_source}",
            ),
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
            "daily_sample_count": len(daily_counts),
        }
        return self._analyze(
            report_type="leadership",
            report_date=period_label,
            context=context,
            simulator=self._simulate_leadership,
            force=force,
            source_data_version=source_data_version,
        )

    @staticmethod
    def _simulate_leadership(context: dict) -> AnalysisResult:
        fact = {item["id"]: item["value"] for item in context["facts"]}
        if context.get("daily_sample_count", 0) < 5:
            high_day_text = "高位生产日样本不足，当前不能形成异常日判断"
            suggestion = "建议先补足日样本，再根据任务趋势跟踪重点工程和资源安排"
        elif context.get("anomalies"):
            high_day_text = "高位生产日需要结合排产、批次与数据边界核实原因"
            suggestion = "建议复核高位日排产与批次记录，并根据任务趋势跟踪重点工程和资源安排"
        else:
            high_day_text = "现有规则未识别高位生产日，仍需结合任务趋势关注重点工程"
            suggestion = "建议按任务趋势持续跟踪重点工程和资源安排"
        return AnalysisResult(
            status="simulated",
            mode="simulated",
            overview=f"{context['report_month']} 共完成 {fact['F001']}，任务数环比{fact['F004']} [F001][F004]。",
            features=f"任务数同比{fact['F005']}，主要工程与高位日分布为 {fact['F006']} [F005][F006]。",
            alerts=f"{high_day_text}，且任务规模不能直接等同于产能利用率或计划达成率 [F001][F006]。",
            suggestions=f"{suggestion} [F004][F005][F006]。",
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
        expected_data_version: str | None = None,
        force: bool = False,
    ) -> AnalysisResult:
        source_data_version = workshop_analysis_source_version(report_date, records)
        _require_current_data_version(expected_data_version, source_data_version)
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
        unconfirmed = len(records) - confirmed
        primary_project = (
            f"{projects[0]['project_name']} · {projects[0]['task_count']} 项"
            if projects
            else "暂无工程数据"
        )
        trend_evidence = [f"待确认 {unconfirmed} 项"]
        if previous_count is not None:
            trend_evidence.append(
                f"当前 {len(records)} 项；{comparison_date or '上一可比周期'} "
                f"{previous_count} 项；任务数环比 {_change_label(task_change)}；"
                f"切割长度环比 {_change_label(cut_change)}"
            )
        else:
            trend_evidence.append("未取得上一可比周期数据，环比无法比较")
        if year_ago_count is not None:
            trend_evidence.append(
                f"{year_ago_date or '上年同期'} {year_ago_count} 项；"
                f"任务数同比 {_change_label(year_change)}"
            )
        else:
            trend_evidence.append("未取得上年同期数据，同比无法比较")
        anomaly_details = (
            "、".join(
                f"任务 {item['task_id']} 工时 {_duration(item['work_time'])}"
                for item in anomalies
            )
            if anomalies
            else "0 项"
        )
        facts = [
            _fact(
                "F001",
                "任务总数",
                f"{len(records)} 项",
                f"车间报表周期 {report_date}；共 {len(records)} 项任务",
            ),
            _fact(
                "F002",
                "总切割长度",
                f"{_number(total_cut)} 米",
                f"车间报表周期 {report_date}；任务切割长度合计 {_number(total_cut)} 米",
            ),
            _fact(
                "F003",
                "总切割时长",
                _duration(total_work),
                f"车间报表周期 {report_date}；任务切割时长合计 {_duration(total_work)}",
            ),
            _fact(
                "F004",
                "工人确认率",
                f"{confirmed / len(records) * 100:.1f}%" if records else "暂无任务",
                f"已确认 {confirmed} 项；任务总数 {len(records)} 项；确认率 "
                + (f"{confirmed / len(records) * 100:.1f}%" if records else "暂无任务"),
            ),
            _fact(
                "F005",
                "待确认与任务趋势",
                f"{unconfirmed} 项待确认，环比{_change_label(task_change)}",
                "；".join(trend_evidence),
            ),
            _fact(
                "F006",
                "工时异常与主要工程",
                f"{len(anomalies)} 项异常，{primary_project}",
                f"工时高于 Q3 加 1.5 倍 IQR 的任务共 {len(anomalies)} 项，最多展示 5 项："
                f"{anomaly_details}；任务最多工程：{primary_project}",
            ),
        ]
        context = {
            "facts": facts,
            "report_date": report_date,
            "task_count": len(records),
            "confirmation_rate": round(confirmed / len(records) * 100, 1) if records else 0,
            "unconfirmed_count": unconfirmed,
            "anomaly_count": len(anomalies),
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
            source_data_version=source_data_version,
        )

    @staticmethod
    def _simulate_workshop(context: dict) -> AnalysisResult:
        fact = {item["id"]: item["value"] for item in context["facts"]}
        task_count = int(context.get("task_count") or 0)
        unconfirmed_count = int(context.get("unconfirmed_count") or 0)
        anomaly_count = int(context.get("anomaly_count") or 0)
        if not task_count:
            alert = "当前周期暂无任务，无法判断确认闭环与工时异常"
            suggestion = "建议先核对查询范围和任务入库状态，再开展班组分析"
        elif unconfirmed_count and anomaly_count:
            alert = (
                f"仍有 {unconfirmed_count} 项未完成工人确认，另有 {anomaly_count} 项工时离群任务，"
                "可能与程序复杂度、等待、返工或记录边界有关，需逐项核实"
            )
            suggestion = "建议班组先闭环待确认任务，再对离群任务核对程序、材料、等待与现场记录"
        elif unconfirmed_count:
            alert = f"仍有 {unconfirmed_count} 项未完成工人确认，现有规则未识别工时离群任务"
            suggestion = "建议班组优先闭环待确认任务，并按工程抽查程序、材料和现场记录"
        elif anomaly_count:
            alert = (
                f"任务确认已闭环，但有 {anomaly_count} 项工时离群任务，"
                "可能与程序复杂度、等待、返工或记录边界有关，需逐项核实"
            )
            suggestion = "建议班组对离群任务核对程序、材料、等待与现场记录"
        else:
            alert = "任务确认已闭环，现有规则未识别工时离群任务"
            suggestion = "建议保持任务确认闭环，并按工程抽查程序、材料和现场记录"
        return AnalysisResult(
            status="simulated",
            mode="simulated",
            overview=f"{context['report_date']} 共纳入{fact['F001']}生产任务，任务趋势为{fact['F005']} [F001][F005]。",
            features=f"工人确认率为 {fact['F004']}，工时异常和主要工程分布为 {fact['F006']} [F004][F006]。",
            alerts=f"{alert} [F004][F005][F006]。",
            suggestions=f"{suggestion} [F004][F005][F006]。",
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
        source_data_version: str | None = None,
        expected_data_version: str | None = None,
        force: bool = False,
    ) -> AnalysisResult:
        if source_data_version is not None:
            _require_current_data_version(expected_data_version, source_data_version)
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
        energy_mode_label = {
            "metered": "实测",
            "mock": "模拟",
            "unavailable": "不可用",
        }.get(energy_data_mode, "未知")
        task_trend_evidence = [f"当前 {len(records)} 项"]
        if previous_count is not None:
            task_trend_evidence.append(
                f"{comparison_date or '上一可比周期'} {previous_count} 项；"
                f"任务数环比 {_change_label(task_change)}"
            )
        else:
            task_trend_evidence.append("未取得上一可比周期数据，环比无法比较")
        if year_ago_count is not None:
            task_trend_evidence.append(
                f"{year_ago_date or '上年同期'} {year_ago_count} 项；"
                f"任务数同比 {_change_label(year_change)}"
            )
        else:
            task_trend_evidence.append("未取得上年同期数据，同比无法比较")
        anomaly_details = (
            "、".join(
                f"任务 {item['task_id']} 工时 {_duration(item['work_time'])}"
                for item in anomalies
            )
            if anomalies
            else "0 项"
        )
        facts = [
            _fact(
                "F001",
                "任务总数",
                f"{len(records)} 项",
                f"设备报表周期 {report_date}；共 {len(records)} 项任务",
            ),
            _fact(
                "F002",
                "总切割时长",
                _duration(total_work),
                f"设备报表周期 {report_date}；任务切割时长合计 {_duration(total_work)}",
            ),
            _fact(
                "F003",
                "已匹配任务气耗",
                energy_fact(total_gas, gas_count, units["gas"]),
                f"数据模式 {energy_mode_label}；气耗有值 {gas_count} 项，共 {len(records)} 项；非空任务气耗合计 "
                f"{energy_fact(total_gas, gas_count, units['gas'])}；插值 {gas_estimates} 项",
            ),
            _fact(
                "F004",
                "已匹配任务电耗",
                energy_fact(total_electricity, electric_count, units["electricity"]),
                f"数据模式 {energy_mode_label}；电耗有值 {electric_count} 项，共 {len(records)} 项；非空任务电耗合计 "
                f"{energy_fact(total_electricity, electric_count, units['electricity'])}；插值 {electric_estimates} 项",
            ),
            _fact(
                "F005",
                "任务趋势与工时异常",
                f"环比{_change_label(task_change)}，{len(anomalies)} 项异常",
                "；".join(task_trend_evidence)
                + f"；工时高于 Q3 加 1.5 倍 IQR 的任务共 {len(anomalies)} 项，最多展示 5 项：{anomaly_details}",
            ),
            _fact(
                "F006",
                "能耗数据待核对任务",
                f"{quality_count} 项",
                f"气耗或电耗缺失或异常的任务共 {quality_count} 项，同一任务只计一次；"
                f"气耗覆盖 {gas_count}/{len(records)} 项，电耗覆盖 {electric_count}/{len(records)} 项；"
                f"计量区间重叠 {overlap_count} 项，采样或读数缺口 {sampling_issue_count} 项，"
                f"起止时间异常 {invalid_interval_count} 项；另含未配置或未绑定记录",
            ),
        ]
        context = {
            "facts": facts,
            "report_date": report_date,
            "task_count": len(records),
            "energy_data_mode": energy_data_mode,
            "energy_notice": energy_notice,
            "anomaly_count": len(anomalies),
            "quality_count": quality_count,
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
            "energy_by_project": _equipment_breakdown(
                records, dimension="project"
            )[:10],
            "equipment_distribution": _equipment_breakdown(
                records, dimension="equipment"
            )[:10],
            "anomalies": anomalies,
        }
        return self._analyze(
            report_type="equipment",
            report_date=report_date,
            context=context,
            simulator=self._simulate_equipment,
            force=force,
            source_data_version=source_data_version,
        )

    @staticmethod
    def _simulate_equipment(context: dict) -> AnalysisResult:
        fact = {item["id"]: item["value"] for item in context["facts"]}
        energy_note = (
            "气耗、电耗当前为模拟值，本次不进行能耗异常判定 [F003][F004][F006]。"
            if context.get("energy_data_mode") == "mock"
            else "气耗、电耗仅汇总已匹配任务，缺失不等于零，不进行能耗异常判定 [F003][F004][F006]。"
        )
        mode = context.get("energy_data_mode")
        task_count = int(context.get("task_count") or 0)
        anomaly_count = int(context.get("anomaly_count") or 0)
        quality_count = int(context.get("quality_count") or 0)
        if not task_count:
            alert = "当前周期暂无设备任务，无法判断工时离群和能耗数据质量"
        elif anomaly_count and quality_count:
            alert = (
                f"现有规则识别 {anomaly_count} 项工时离群任务和 {quality_count} 项能耗待核对任务，"
                "可能与采样缺口、任务边界、配置或工艺差异有关，需逐项核实"
            )
        elif quality_count:
            alert = (
                f"现有规则识别 {quality_count} 项能耗待核对任务，"
                "可能与采样缺口、任务边界或配置有关，需逐项核实"
            )
        elif anomaly_count:
            alert = (
                f"现有规则识别 {anomaly_count} 项工时离群任务，"
                "可能与任务边界或工艺差异有关，需逐项核实"
            )
        else:
            alert = "现有规则未识别工时离群或能耗数据待核对任务"
        if not task_count:
            recommendation = "先核对查询范围和设备任务入库状态"
        elif mode == "mock":
            recommendation = "接入真实电表、气表及设备状态数据"
        elif mode == "unavailable":
            recommendation = "检查内网连接、仪表参数编号和采样时间覆盖"
        elif quality_count:
            recommendation = "复核缺失采样、重叠任务和插值估算，补充设备状态与能耗基准"
        else:
            recommendation = "保持仪表采样巡检和任务边界校验"
        follow_up = (
            "并核对工时离群任务的程序、工艺和现场记录"
            if anomaly_count
            else "并按设备和工程抽查程序、工艺与现场记录"
        )
        return AnalysisResult(
            status="simulated",
            mode="simulated",
            overview=f"{context['report_date']} 共纳入{fact['F001']}设备任务，总切割时长 {fact['F002']} [F001][F002]。",
            features=energy_note,
            alerts=f"{alert}，任务趋势为{fact['F005']} [F005][F006]。",
            suggestions=f"建议{recommendation}，{follow_up} [F005][F006]。",
        )
