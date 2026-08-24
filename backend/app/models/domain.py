from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any


@dataclass(slots=True)
class ReportFilter:
    report_date: date
    project_code: str | None = None
    ship_no: str | None = None


@dataclass(slots=True)
class SummaryBaseRecord:
    task_id: str
    project_code: str | None
    ship_no: str | None
    segment_name: str | None
    program_no: str | None
    cut_length: Decimal | None
    groove_length: Decimal | None
    work_time: Decimal | None
    production_time: datetime | None = None
    source_updated_at: datetime | None = None


@dataclass(slots=True)
class WorkshopRecord:
    task_id: str
    project_code: str | None
    ship_no: str | None
    segment_name: str | None
    program_no: str | None
    cut_length: Decimal | None
    groove_length: Decimal | None
    work_time: Decimal | None
    remark: str | None = None
    production_time: datetime | None = None
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None


@dataclass(slots=True)
class EquipmentRecord:
    task_id: str
    equipment_id: str | None
    project_code: str | None
    ship_no: str | None
    segment_name: str | None
    cut_length: Decimal | None
    work_time: Decimal | None
    gas_consumption: Decimal | None
    electricity_consumption: Decimal | None
    production_time: datetime | None = None
    remark: str | None = None


@dataclass(slots=True)
class QueryBundle:
    summary_records: list[SummaryBaseRecord]
    workshop_records: list[WorkshopRecord]
    equipment_records: list[EquipmentRecord]
    data_updated_at: datetime | None = None


@dataclass(slots=True)
class LeadershipPeriodRecord:
    period_start: date
    task_count: int
    cut_length_m: Decimal
    groove_length_m: Decimal
    work_time_seconds: int
    remark: str | None = None


@dataclass(slots=True)
class LeadershipReportResult:
    report_month: date
    source_mode: str
    daily_records: list[LeadershipPeriodRecord]
    monthly_records: list[LeadershipPeriodRecord]
    current_month_total: LeadershipPeriodRecord
    twelve_month_total: LeadershipPeriodRecord


@dataclass(slots=True)
class SummaryMetrics:
    task_count: int = 0
    total_cut_length: Decimal = Decimal("0")
    total_groove_length: Decimal = Decimal("0")
    total_work_time: Decimal = Decimal("0")
    avg_cut_length: Decimal | None = None
    avg_groove_length: Decimal | None = None
    avg_work_time: Decimal | None = None
    max_cut_length: Decimal | None = None
    min_cut_length: Decimal | None = None
    max_groove_length: Decimal | None = None
    min_groove_length: Decimal | None = None
    max_work_time: Decimal | None = None
    min_work_time: Decimal | None = None


@dataclass(slots=True)
class AttentionItem:
    level: str
    category: str
    task_id: str | None
    message: str


@dataclass(slots=True)
class AnalysisResult:
    status: str
    overview: str
    features: str
    alerts: str
    suggestions: str
    mode: str = "model"
    limitations: str = ""
    facts: list[dict[str, Any]] = field(default_factory=list)
    generated_at: str | None = None
    cached: bool = False


@dataclass(slots=True)
class StatisticsResult:
    summary: SummaryMetrics
    valid_summary_records: list[SummaryBaseRecord]
    data_quality: list[AttentionItem] = field(default_factory=list)
    attention_items: list[AttentionItem] = field(default_factory=list)


def to_primitive(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return {key: to_primitive(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {key: to_primitive(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_primitive(item) for item in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value
