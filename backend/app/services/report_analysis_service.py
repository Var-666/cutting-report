from __future__ import annotations

import hashlib
import json
import logging
import statistics
import threading
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
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
from app.services.statistics_service import build_worker_work_summaries


logger = logging.getLogger(__name__)
PROMPT_VERSION = "report-analysis-v22-full-report-tables"

PLAIN_LANGUAGE_TERMS = (
    ("未标注工程", "工程号未填写"),
    ("未标注设备", "设备编号未填写"),
    ("高位生产日", "任务较多的日期"),
    ("高位日", "任务较多的日期"),
    ("工时异常", "切割时间过长"),
    ("数据边界", "数据不足之处"),
    ("环比", "比上一周期"),
    ("同比", "比去年同期"),
)


def _plain_language(value: str) -> str:
    for term, replacement in PLAIN_LANGUAGE_TERMS:
        value = value.replace(term, replacement)
    return value


def _present_analysis(result: AnalysisResult) -> AnalysisResult:
    """Translate display copy without changing source statistics."""
    for field in ("overview", "features", "alerts", "suggestions"):
        setattr(result, field, _plain_language(getattr(result, field)))
    result.facts = [
        {
            key: _plain_language(value) if key in {"label", "value", "source"} and isinstance(value, str) else value
            for key, value in fact.items()
        }
        for fact in result.facts
    ]
    return result


def analysis_period_label(start: date, end: date) -> str:
    return start.isoformat() if start == end else f"{start.isoformat()} 至 {end.isoformat()}"


class AnalysisDataVersionMismatch(ValueError):
    """The report shown by the browser is no longer the authoritative version."""


class AnalysisUnavailableError(RuntimeError):
    """The model did not produce a usable analysis result."""


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


def analysis_table_source_version(rows: list) -> str:
    """Return a stable version for one complete analysis table."""
    return generate_data_version({"rows": to_primitive(rows)})


def combined_analysis_source_version(primary: str, secondary: str | None = None) -> str:
    """Join both visible report-table versions into one analysis version."""
    return f"{primary}.{secondary or primary}"


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


def _table(name: str, rows: list[dict]) -> dict:
    return {"name": name, "row_count": len(rows), "rows": rows}


def _workshop_task_rows(records: list[WorkshopRecord]) -> list[dict]:
    return [
        {
            "project_code": row.project_code,
            "ship_no": row.ship_no,
            "segment_name": row.segment_name,
            "program_no": row.program_no,
            "cut_length": row.cut_length,
            "groove_length": row.groove_length,
            "work_time": row.work_time,
            "production_time": row.production_time,
            "confirmed_by": row.confirmed_by,
            "confirmed_at": row.confirmed_at,
            "remark": row.remark,
        }
        for row in records
    ]


def _worker_summary_rows(records: list[WorkshopRecord]) -> list[dict]:
    return [
        {
            key: row.get(key)
            for key in (
                "company_no",
                "worker_name",
                "confirmed_task_count",
                "total_cut_length",
                "total_work_time_seconds",
                "work_time_share",
                "last_confirmed_at",
            )
        }
        for row in build_worker_work_summaries(records)
    ]


def _equipment_task_rows(records: list[EquipmentRecord]) -> list[dict]:
    return [
        {
            "project_code": row.project_code,
            "ship_no": row.ship_no,
            "segment_name": row.segment_name,
            "production_time": row.production_time,
            "work_time": row.work_time,
            "electricity_consumption": row.electricity_consumption,
            "gas_consumption": row.gas_consumption,
        }
        for row in records
    ]


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
        return _present_analysis(AnalysisResult(**allowed))

    def _analyze(
        self,
        *,
        report_type: str,
        report_date: str,
        context: dict,
        force: bool,
        source_data_version: str | None = None,
    ) -> AnalysisResult:
        if not self.settings.llm_enabled:
            raise AnalysisUnavailableError(
                "大模型智能分析未启用，请先完成配置。"
            )
        primitive_context, canonical = normalize_analysis_context(context)
        input_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        data_version = source_data_version or input_hash
        engine = f"model:{self.settings.llm_model}"
        cache_key = hashlib.sha256(
            f"{PROMPT_VERSION}|{report_type}|{report_date}|{engine}|{data_version}|{input_hash}".encode(
                "utf-8"
            )
        ).hexdigest()
        self.metadata.save_analysis_source_version(
            report_type=report_type,
            report_date=report_date,
            data_version=data_version,
            updated_at=datetime.now(self.timezone).isoformat(timespec="seconds"),
        )
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

            try:
                result = self.llm.analyze_context(report_type, primitive_context)
                if result.mode != "model":
                    raise AnalysisOutputValidationError(
                        "LLM 分析结果未标记为模型输出"
                    )
            except AnalysisOutputValidationError as exc:
                logger.warning("LLM analysis response cannot be displayed: %s", exc)
                raise AnalysisUnavailableError(
                    "大模型未返回可展示的分析结果，请重试。"
                ) from exc
            except Exception as exc:
                logger.exception("LLM analysis failed")
                raise AnalysisUnavailableError(
                    "大模型智能分析暂不可用，请稍后重试。"
                ) from exc

            result.facts = primitive_context.get("facts", [])
            _present_analysis(result)
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
                mode="model",
                model_name=self.settings.llm_model,
                prompt_version=PROMPT_VERSION,
                result=payload,
                generated_at=result.generated_at,
            )
            return result

    def analyze_leadership(
        self,
        report: LeadershipReportResult,
        *,
        monthly_records: list | None = None,
        expected_data_version: str | None = None,
        force: bool = False,
    ) -> AnalysisResult:
        summary_rows = report.monthly_records if monthly_records is None else monthly_records
        source_data_version = combined_analysis_source_version(
            leadership_analysis_source_version(report),
            analysis_table_source_version(summary_rows),
        )
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
            f"{'工程号未填写' if projects[0]['project_name'] == '未标注工程' else projects[0]['project_name']} "
            f"{projects[0]['task_count']} 项"
            if projects and current.task_count
            else "暂无工程数据"
        )
        anomaly_value = (
            f"{len(daily_anomalies)} 天"
            if len(daily_counts) >= 5
            else "记录太少，暂无法判断"
        )
        anomaly_source = (
            f"每日任务数参考值 {daily_threshold:.1f} 项；任务较多的日期 "
            + (
                "、".join(
                    f"{item['date']} {item['task_count']} 项"
                    for item in daily_anomalies
                )
                if daily_anomalies
                else "0 天"
            )
            if daily_threshold is not None
            else f"当前仅 {len(daily_counts)} 天有任务记录，少于 5 天，暂无法判断哪些日期任务较多"
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
                "任务数比上一周期",
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
                "任务数比去年同期",
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
                "工程号与任务较多的日期",
                f"{project_value}，任务较多的日期 {anomaly_value}",
                f"任务最多工程：{project_value}；{anomaly_source}",
            ),
        ]
        context = {
            "facts": facts,
            "report_month": period_label,
            "tables": [
                _table("区间每日明细", to_primitive(report.daily_records)),
                _table(
                    "近12个月月度汇总",
                    to_primitive(summary_rows),
                ),
            ],
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
            force=force,
            source_data_version=source_data_version,
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
        source_data_version = combined_analysis_source_version(
            workshop_analysis_source_version(report_date, records)
        )
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
            f"{_plain_language(projects[0]['project_name'])} · {projects[0]['task_count']} 项"
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
                "待确认任务与数量变化",
                f"{unconfirmed} 项待确认，比上一周期{_change_label(task_change)}",
                "；".join(trend_evidence),
            ),
            _fact(
                "F006",
                "切割时间与工程",
                f"{len(anomalies)} 项切割时间较长，{primary_project}",
                f"按切割时间分布筛出的较长任务共 {len(anomalies)} 项，最多展示 5 项："
                f"{anomaly_details}；任务最多工程：{primary_project}",
            ),
        ]
        context = {
            "facts": facts,
            "report_date": report_date,
            "tables": [
                _table("任务明细", _workshop_task_rows(records)),
                _table("工人工时汇总", _worker_summary_rows(records)),
            ],
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
            force=force,
            source_data_version=source_data_version,
        )

    def analyze_equipment(
        self,
        report_date: str,
        records: list[EquipmentRecord],
        *,
        energy_data_mode: str = "unavailable",
        energy_units: dict[str, str] | None = None,
        energy_notice: str = "",
        energy_summary_rows: list[dict] | None = None,
        comparison_records: list[EquipmentRecord] | None = None,
        comparison_date: str | None = None,
        year_ago_records: list[EquipmentRecord] | None = None,
        year_ago_date: str | None = None,
        source_data_version: str | None = None,
        expected_data_version: str | None = None,
        force: bool = False,
    ) -> AnalysisResult:
        if source_data_version is not None:
            source_data_version = combined_analysis_source_version(
                source_data_version,
                analysis_table_source_version(energy_summary_rows or []),
            )
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
            "partial": "实际仪表数据",
            "mock": "模拟",
            "unavailable": "不可用",
        }.get(energy_data_mode, "未知")

        def energy_source(channel: str, count: int) -> str:
            if not count:
                return f"无有效{channel}记录"
            coverage = "（仅汇总有记录的任务）" if energy_data_mode == "partial" and count < len(records) else ""
            return f"数据来源 {energy_mode_label}{coverage}"
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
                f"{energy_source('气耗', gas_count)}；有气耗记录 {gas_count} 项，共 {len(records)} 项；已记录任务气耗合计 "
                f"{energy_fact(total_gas, gas_count, units['gas'])}；根据前后读数估算 {gas_estimates} 项",
            ),
            _fact(
                "F004",
                "已匹配任务电耗",
                energy_fact(total_electricity, electric_count, units["electricity"]),
                f"{energy_source('电耗', electric_count)}；有电耗记录 {electric_count} 项，共 {len(records)} 项；已记录任务电耗合计 "
                f"{energy_fact(total_electricity, electric_count, units['electricity'])}；根据前后读数估算 {electric_estimates} 项",
            ),
            _fact(
                "F005",
                "任务变化与切割时间",
                f"比上一周期{_change_label(task_change)}，{len(anomalies)} 项切割时间较长",
                "；".join(task_trend_evidence)
                + f"；按切割时间分布筛出的较长任务共 {len(anomalies)} 项，最多展示 5 项：{anomaly_details}",
            ),
            _fact(
                "F006",
                "能耗数据待核对任务",
                f"{quality_count} 项",
                f"气耗或电耗缺失或异常的任务共 {quality_count} 项，同一任务只计一次；"
                f"有气耗记录 {gas_count}/{len(records)} 项，有电耗记录 {electric_count}/{len(records)} 项；"
                f"仪表记录时间重叠 {overlap_count} 项，仪表记录不完整 {sampling_issue_count} 项，"
                f"任务起止时间有问题 {invalid_interval_count} 项；另含未设置仪表或未关联设备的记录",
            ),
        ]
        context = {
            "facts": facts,
            "report_date": report_date,
            "tables": [
                _table("任务能耗明细", _equipment_task_rows(records)),
                _table("近12个月月度能耗汇总", energy_summary_rows or []),
            ],
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
            force=force,
            source_data_version=source_data_version,
        )
