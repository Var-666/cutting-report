"""JSON report contracts: numbers follow domain.to_primitive, not Decimal strings."""
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue

from app.models.domain import LeadershipSnapshotState


class AnalysisFact(BaseModel):
    __pydantic_extra__: dict[str, JsonValue]
    model_config = ConfigDict(extra='allow')
    label: str
    value: str | float | None
    id: str | None = None
    fact_id: str | None = None
    source: str | None = None
    description: str | None = None
    unit: str | None = None


class ReportAnalysis(BaseModel):
    status: str
    overview: str
    features: str
    alerts: str
    suggestions: str
    mode: str
    facts: list[AnalysisFact]
    data_version: str | None
    generated_at: str | None
    cached: bool
    error: str | None = None


class AnalysisVersion(BaseModel):
    data_version: str
    updated_at: str


class LeadershipPeriodRecord(BaseModel):
    period_start: date
    task_count: int
    cut_length_m: float
    groove_length_m: float
    work_time_seconds: int
    remark: str | None


class ProjectDistribution(BaseModel):
    project_name: str
    task_count: int
    cut_length_m: float
    work_time_seconds: int


class LeadershipReport(BaseModel):
    report_month: date
    source_mode: str
    daily_records: list[LeadershipPeriodRecord]
    monthly_records: list[LeadershipPeriodRecord]
    current_month_total: LeadershipPeriodRecord
    twelve_month_total: LeadershipPeriodRecord
    year_ago_total: LeadershipPeriodRecord | None
    project_distribution: list[ProjectDistribution]
    snapshot: LeadershipSnapshotState
    period_start: date | None
    period_end: date | None
    comparison_total: LeadershipPeriodRecord | None
    comparison_start: date | None
    comparison_end: date | None
    analysis_source_version: str
    analysis_summary_source_version: str
    aggregation: Literal['day', 'month'] | None = None


ReportType = Literal['leadership', 'workshop', 'equipment', 'custom']
ReportStatus = Literal['pending', 'querying', 'calculating', 'analyzing', 'rendering', 'completed', 'failed']


class ReportAccepted(BaseModel):
    report_id: str
    status: Literal['pending']
    status_url: str


class ReportError(BaseModel):
    code: str
    message: str


class ReportJob(BaseModel):
    report_id: str
    device_id: str
    status: ReportStatus
    report_type: ReportType
    definition_id: str | None
    report_title: str | None
    stage_message: str
    report_date: date
    period_start: date
    period_end: date
    project_code: str | None
    ship_no: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    ai_status: str | None
    error: ReportError | None
    filename: str | None = None
    task_count: int | None = None
    file_size: int | None = None
    view_url: str | None = None
    download_url: str | None = None
    generated_at: str | None = None
