"""Automation responses, including nullable and state-dependent mail fields."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue


class AutomationConfiguration(BaseModel):
    automation_enabled: bool
    automation_scan_seconds: float
    email_retry_base_seconds: int
    smtp_enabled: bool
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password_configured: bool
    smtp_from_email: str
    smtp_from_name: str
    smtp_use_tls: bool
    smtp_use_ssl: bool
    smtp_timeout_seconds: float


class SmtpStatus(BaseModel):
    enabled: bool
    configured: bool
    host: str | None
    port: int
    from_email: str | None
    use_tls: bool
    use_ssl: bool


class AutomationSummary(BaseModel):
    schedule_total: int
    schedule_enabled: int
    delivery_total: int
    delivery_sent: int
    delivery_pending: int
    delivery_failed: int
    delivery_unknown: int


class AutomationStatus(BaseModel):
    automation_enabled: bool
    smtp: SmtpStatus
    summary: AutomationSummary


class AutomationSchedule(BaseModel):
    schedule_id: str
    device_id: str
    name: str
    report_type: Literal['leadership', 'workshop', 'equipment']
    frequency: Literal['daily', 'weekly', 'monthly']
    run_time: str
    weekday: int | None
    month_day: int | None
    report_period: Literal['current_day', 'previous_day', 'current_month', 'previous_month']
    recipients: list[str]
    subject_template: str
    chart_type: Literal['bar_line', 'bar', 'line', 'area']
    attach_report: bool
    max_attempts: int
    enabled: bool
    next_run_at: str
    last_run_at: str | None
    last_status: str | None
    created_by_id: str
    created_by_name: str
    created_at: str
    updated_at: str
    run_count: int | None = None
    sent_count: int | None = None
    failed_count: int | None = None


class ScheduledRun(BaseModel):
    run_id: str
    device_id: str
    schedule_id: str | None
    schedule_name: str
    report_type: str
    report_date: str
    scheduled_for: str
    trigger_type: str
    report_id: str | None
    status: str
    recipients: list[str]
    subject_template: str
    chart_type: str
    attach_report: bool
    max_attempts: int
    created_by_id: str
    created_by_name: str
    last_error: str | None
    created_at: str
    updated_at: str


class RecipientResult(BaseModel):
    # SMTP replies and manual resolutions add different, genuinely dynamic details.
    __pydantic_extra__: dict[str, JsonValue]
    model_config = ConfigDict(extra='allow')
    status: str
    message: str | None = None
    code: int | None = None


class EmailDelivery(BaseModel):
    delivery_id: str
    run_id: str
    report_id: str
    recipients: list[str]
    recipient_results: dict[str, RecipientResult]
    subject: str
    status: str
    attempt_count: int
    max_attempts: int
    next_attempt_at: str
    last_error: str | None
    message_id: str | None
    created_at: str
    updated_at: str
    sent_at: str | None
    schedule_name: str
    report_type: str
    report_date: str
    trigger_type: str
    filename: str
    relative_path: str
    generated_at: str


class EmailResolution(BaseModel):
    resolution_id: str
    delivery_id: str
    run_id: str
    message_id: str
    actor_id: str
    actor_name: str
    evidence_note: str
    before_results: dict[str, RecipientResult]
    after_results: dict[str, RecipientResult]
    resolved_status: str
    created_at: str


class Deleted(BaseModel):
    deleted: bool


class Queued(BaseModel):
    queued: bool


class Resolved(BaseModel):
    resolved: bool
