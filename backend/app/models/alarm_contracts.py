"""Alarm HTTP contracts; generated frontend types must come from these models."""
from typing import Literal

from pydantic import BaseModel

class AlarmStatus(BaseModel):
    source_name: str
    last_alarm_id: int
    last_success_at: str | None
    monitor_start_at: str | None
    open_recheck_after_id: int
    history_recheck_after_id: int
    enabled: bool
    machine_nos: list[str]
    worker_running: bool
    interval_seconds: int
    reopen_debounce_minutes: int


class AlarmIncident(BaseModel):
    incident_id: str
    fingerprint: str
    machine_no: str
    alarm_type: str
    alarm_code: str
    alarm_class: str
    alarm_desc: str
    severity: str
    status: Literal['open', 'closed']
    first_seen_at: str
    last_seen_at: str
    closed_at: str | None
    first_start_time: str | None
    latest_remove_time: str | None
    first_alarm_id: int
    latest_alarm_id: int
    pdf_report_id: str | None
    report_status: str | None = None
    report_view_url: str | None = None
    report_error_message: str | None = None


class AlarmFact(BaseModel):
    id: str
    incident_id: str
    machine_no: str
    alarm_type: str
    alarm_code: str
    alarm_class: str
    alarm_desc: str
    severity: str
    status: str
    start_time: str | None
    remove_time: str | None


class AlarmFinding(BaseModel):
    title: str
    severity: str
    confidence: float
    analysis: str
    possible_causes: list[str]
    evidence_ids: list[str]


class AlarmAction(BaseModel):
    priority: int
    action: str
    evidence_ids: list[str]


class AlarmAnalysis(BaseModel):
    summary: str
    overall_risk: str
    limitations: list[str]
    facts: list[AlarmFact]
    findings: list[AlarmFinding]
    actions: list[AlarmAction]


class AlarmReport(BaseModel):
    report_id: str
    emission_key: str
    status: Literal['generating', 'completed', 'failed']
    machine_no: str
    incident_ids: list[str]
    analysis: AlarmAnalysis | None
    html_relative_path: str | None
    pdf_relative_path: str | None
    pdf_filename: str | None
    file_size: int | None
    sha256: str | None
    model_status: str | None
    error_message: str | None
    created_at: str
    completed_at: str | None
    execution_owner: str | None = None
    download_url: str | None = None
    view_url: str | None = None


class DominantAlarm(BaseModel):
    label: str
    count: int


class AlarmIncidentSummary(BaseModel):
    total: int
    all_total: int
    open_count: int
    page_count: int
    severity_counts: dict[str, int]
    dominant_alarm: DominantAlarm | None


class AlarmIncidentPage(AlarmIncidentSummary):
    items: list[AlarmIncident]
    page: int
    page_size: int


class AlarmReportPage(BaseModel):
    items: list[AlarmReport]
    total: int
    page: int
    page_size: int
    completed_count: int
    page_count: int
