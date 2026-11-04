from typing import Literal

from pydantic import BaseModel, Field


class ResourceUsage(BaseModel):
    resource_type: Literal['global', 'source', 'line', 'http']
    resource: str
    executing: int
    execution_capacity: int
    waiting: int
    waiting_capacity: int
    timeouts: int
    rejected: int
    configured_capacity: int | None = None
    restart_required: bool = False


class SqlResourceUsage(BaseModel):
    scope: Literal['process']
    coordination: Literal['shared_local_directory']
    items: list[ResourceUsage]


class ResourceReport(BaseModel):
    web: SqlResourceUsage
    http: list[ResourceUsage]
    worker: SqlResourceUsage | None
    worker_state: str
    roles: dict[str, SqlResourceUsage | None] = Field(default_factory=dict)
