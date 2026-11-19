"""Contracts for global, administrator-only operations queries."""
from datetime import UTC, timedelta, datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.core.operations_logging import log_request_id, safe_id
from app.models.contracts import Items

Role = Literal['web', 'scheduler', 'worker', 'automation']
Level = Literal['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
IncidentStatus = Literal['open', 'acknowledged', 'recovered']
Health = Literal['healthy', 'abnormal', 'unknown', 'paused']


class StackFrame(BaseModel):
    file: str
    function: str
    line: int


class SafeException(BaseModel):
    type: str
    frames: list[StackFrame]


class LogRecord(BaseModel):
    id: str
    timestamp: AwareDatetime
    level: Level
    role: Role
    instance_id: str
    logger: str
    component: str
    message: str
    request_id: str | None = None
    analysis_id: str | None = None
    task_id: str | None = None
    report_id: str | None = None
    line_id: str | None = None
    source_id: str | None = None
    resource_id: str | None = None
    related_kind: str | None = None
    stage: str | None = None
    error_code: str | None = None
    duration_ms: float | None = None
    exception: SafeException | None = None
    data_quality: dict[Literal['state', 'period_start', 'period_end', 'data_as_of', 'missing_count', 'estimated_count',
                              'data_version', 'summary_count', 'daily_count', 'grouped_count'], str] | None = None


class LogPage(Items[LogRecord]):
    next_cursor: str | None
    coverage_warnings: list[Literal['retention', 'scan_limit', 'rotation', 'segment', 'coverage', 'unavailable']]
    earliest_scanned_at: AwareDatetime | None
    latest_scanned_at: AwareDatetime | None


class TimeFilters(BaseModel):
    start: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC) - timedelta(days=1))
    end: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    level: Level | None = None
    component: str | None = Field(None, max_length=128)
    line_id: str | None = Field(None, max_length=128)
    error_code: str | None = Field(None, max_length=128)
    keyword: str | None = Field(None, min_length=2, max_length=100)

    @model_validator(mode='after')
    def check_range(self):
        if self.start is not None and (self.end <= self.start or self.end - self.start > timedelta(days=90, seconds=1)):
            raise ValueError('请选择最长 90 天的有效时间范围')
        return self


class LogSelection(TimeFilters):
    role: Role | None = None
    instance_id: str | None = Field(None, max_length=128)
    resource_id: str | None = Field(None, max_length=128)
    source_id: str | None = Field(None, max_length=128)
    request_id: str | None = Field(None, max_length=128)
    analysis_id: str | None = Field(None, max_length=128)
    task_id: str | None = Field(None, max_length=128)
    report_id: str | None = Field(None, max_length=128)

    def matches(self, item: LogRecord):
        for name in ('level', 'role', 'instance_id', 'component', 'line_id', 'error_code', 'resource_id', 'source_id',
                     'request_id', 'analysis_id', 'task_id', 'report_id'):
            value = getattr(self, name)
            if name == 'request_id' and value is not None:
                value = log_request_id(value) or value
            if value is not None and getattr(item, name) != value:
                return False
        return self.keyword is None or self.keyword.casefold() in item.message.casefold()


class LogFilters(LogSelection):
    cursor: str | None = Field(None, max_length=110)
    limit: int = Field(50, ge=1, le=100)

    @model_validator(mode='after')
    def check_cursor(self):
        if self.cursor:
            instant, _, identifier = self.cursor.partition('/')
            value = datetime.fromisoformat(instant)
            if value.tzinfo is None or not self.start <= value <= self.end or not safe_id(identifier):
                raise ValueError('日志游标无效或不在查询时间范围内')
        return self


class IncidentFilters(TimeFilters):
    start: AwareDatetime | None = None
    status: IncidentStatus | None = None
    min_level: Literal['ERROR'] | None = Field(None, description='ERROR 及以上，包括 ERROR 和 CRITICAL')
    page: int = Field(1, ge=1, le=300)
    page_size: int = Field(30, ge=1, le=100)

    @model_validator(mode='after')
    def check_page_limit(self):
        if self.page_size != 10 and self.page > 100:
            raise ValueError('每页 10 条时最多查询 300 页，其他页长最多查询 100 页')
        return self


class Incident(BaseModel):
    incident_id: str
    fingerprint: str
    error_code: str
    level: Level
    component: str
    role: Role
    instance_id: str | None
    line_id: str | None
    source_id: str | None
    resource_id: str | None
    message: str
    context: dict[str, str]
    first_seen_at: AwareDatetime
    last_seen_at: AwareDatetime
    occurrences: int
    status: IncidentStatus
    acknowledged_at: AwareDatetime | None
    acknowledged_by: str | None
    note: str | None
    recovered_at: AwareDatetime | None
    recovery_evidence: str | None


class AcknowledgeIncident(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    note: str | None = Field(None, max_length=240)


class BrowserIssue(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['vue', 'javascript', 'promise', 'api']
    request_id: str | None = Field(None, max_length=80, pattern=r'^(web|req)_[A-Za-z0-9_-]+$')


class ReportedIssue(BaseModel):
    accepted: bool


class RoleStatus(BaseModel):
    role: Role
    instance_id: str | None = None
    state: Health
    detail: str
    heartbeat_at: AwareDatetime | None = None
    heartbeat_age_seconds: float | None = None
    stalled_workers: list[str] = Field(default_factory=list)
    configuration_state: Literal['healthy', 'abnormal', 'unknown'] = 'unknown'


class MonitorStatus(BaseModel):
    monitor_id: str
    name: str
    state: Literal['unknown', 'healthy', 'suspect', 'down', 'recovering', 'paused']
    last_checked_at: float | None


class WorkStatus(BaseModel):
    group: Literal['background', 'reports', 'automation', 'email']
    status: str
    count: int


class OperationsStatus(BaseModel):
    observed_at: AwareDatetime
    roles: list[RoleStatus]
    postgres: Health
    consumers_ready: int | None
    consumers_expected: int
    monitors: list[MonitorStatus] | None
    work: list[WorkStatus] | None


class LogTotals(BaseModel):
    errors: int
    warnings: int
    complete: bool


class OperationsOverview(BaseModel):
    open_incidents: int | None
    log_totals: LogTotals | None
    recent: list[Incident] | None
    recurring: list[Incident] | None
    runtime: OperationsStatus


RelatedKind = Literal['reports', 'background', 'automation', 'email', 'database-monitor', 'dispatch']


class RelatedOperation(BaseModel):
    kind: RelatedKind
    id: str
    status: str
    line_id: str | None
    report_id: str | None
    stage: str | None
