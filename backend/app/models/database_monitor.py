"""Administrator-managed TCP availability monitoring contracts."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, IPvAnyAddress, field_validator
from app.models.contracts import Items


class DatabaseMonitorInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    name: str = Field(min_length=1, max_length=80)
    ip: IPvAnyAddress
    port: int = Field(ge=1, le=65535, strict=True)
    enabled: bool = True

    @field_validator('ip')
    @classmethod
    def concrete_ip(cls, value):
        if value.is_unspecified or value.is_multicast:
            raise ValueError('请输入单个服务器 IP 地址')
        return value


class DatabaseMonitor(BaseModel):
    monitor_id: str
    name: str
    ip: str
    port: int
    enabled: bool
    state: Literal['unknown', 'healthy', 'suspect', 'down', 'recovering', 'paused']
    last_checked_at: float | None
    last_success_at: float | None
    failure_started_at: float | None
    failures: int
    successes: int
    last_error: str | None


class DatabaseMonitorIncident(BaseModel):
    incident_id: str
    monitor_id: str
    name: str
    ip: str
    port: int
    started_at: float
    confirmed_at: float
    ended_at: float | None
    end_reason: Literal['recovered', 'paused', 'changed', 'deleted'] | None
    last_error: str


class DatabaseMonitorList(Items[DatabaseMonitor]):
    timezone: str


class DatabaseMonitorSaved(BaseModel):
    monitor_id: str


class DatabaseMonitorDeleted(BaseModel):
    deleted: bool
