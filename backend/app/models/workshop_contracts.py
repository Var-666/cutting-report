"""Workshop response models shared with generated frontend types."""
from datetime import date
from pydantic import BaseModel
from app.models.contracts import Page


class WorkshopTask(BaseModel):
    task_id: str
    project_code: str | None
    ship_no: str | None
    segment_name: str | None
    program_no: str | None
    cut_length: float | None
    groove_length: float | None
    work_time: float | None
    remark: str | None
    production_time: str | None
    production_date: str | None
    snapshot_revision: int
    confirmed_by_id: str | None
    confirmed_by: str | None
    confirmed_by_company_no: str | None
    confirmed_at: str | None


class WorkshopReportInfo(BaseModel):
    report_id: str | None = None
    report_date: str
    source_mode: str | None = None
    scope_machine_no: str | None
    item_count: int
    confirmed_count: int
    generated_by_id: str | None = None
    generated_by_name: str | None = None
    generated_at: str | None
    period_start: str | None = None
    period_end: str | None = None


class DistributionItem(BaseModel):
    key: str
    label: str
    count: int


class TopCutTask(BaseModel):
    segment_name: str | None
    task_id: str
    program_no: str | None
    cut_length_m: float | None


class WorkerSummary(BaseModel):
    worker_id: str
    company_no: str | None
    worker_name: str
    confirmed_task_count: int
    total_cut_length: float
    total_work_time_seconds: float
    last_confirmed_at: str | None
    work_time_share: float


class WorkshopTrend(BaseModel):
    report_date: date
    task_count: int
    pending_count: int
    confirmed_count: int
    project_count: int
    confirmation_rate: float


class WorkshopUser(BaseModel):
    user_id: str
    username: str
    display_name: str
    role: str
    active: bool
    auth_source: str | None = None
    external_id: str | None = None
    group_no: str | None = None
    company_no: str | None = None
    created_at: str | None = None


class WorkshopResult(Page[WorkshopTask]):
    report: WorkshopReportInfo | None
    project_names: list[str]
    confirmed_count: int
    worker_summaries: list[WorkerSummary]
    duration_distribution: list[DistributionItem]
    status_distribution: list[DistributionItem]
    top_cut_length_tasks: list[TopCutTask]
    analysis_source_version: str
    trend: list[WorkshopTrend] | None = None
    current_user: WorkshopUser | None = None


class LatestProductionDate(BaseModel):
    report_date: str | None


class TaskConfirmation(BaseModel):
    task_id: str
    report_id: str
    worker_id: str
    worker_name: str
    worker_company_no: str | None = None
    confirmed_at: str
    scope_machine_no: str
    created_at: str
    updated_at: str


class ConfirmationBatch(BaseModel):
    items: list[TaskConfirmation]
    confirmed_count: int


class ConfirmationAudit(BaseModel):
    audit_id: str
    task_id: str
    report_id: str | None
    action: str
    previous_worker_id: str | None
    previous_worker_name: str | None
    new_worker_id: str | None
    new_worker_name: str | None
    reason: str
    operated_by_id: str
    operated_by_name: str
    operated_by_role: str
    operated_at: str
    scope_machine_no: str
    action_label: str
