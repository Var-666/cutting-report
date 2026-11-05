"""Typed contracts for PDM browsing and cutting dispatch."""
from datetime import date as Date
from datetime import datetime
import ipaddress
from typing import Literal
from zoneinfo import ZoneInfo
from pydantic import BaseModel, Field, field_validator

from app.models.contracts import Items, Page


class CuttingProject(BaseModel):
    project_id: str
    project_name: str
    ship_no: str | None
    state: str | int | None = None


class CuttingCatalog(BaseModel):
    catalog_id: str
    project_id: str | None = None
    parent_id: str | None = None
    title: str
    catalog_type: str | int | None = None
    sort_sn: int | float | None = None
    level: int | None = None
    children: list['CuttingCatalog'] = []


class CatalogResult(Items[CuttingCatalog]):
    tree: list[CuttingCatalog]


class DispatchStatus(BaseModel):
    status: str
    status_label: str
    stage_label: str


class CuttingFile(BaseModel):
    document_id: str
    document_no: str | None
    project_id: str | None = None
    project_name: str | None = None
    ship_no: str | None = None
    catalog_id: str | None = None
    catalog_title: str | None = None
    catalog_parent_id: str | None = None
    catalog_type: str | int | None = None
    classification_type: str | int | None = None
    version_id: str | None
    version: str | None
    document_cname: str | None = None
    document_ename: str | None = None
    document_state: str | int | None = None
    pbs_info: str | None
    operator: str | None
    operator_job_no: str | None
    operate_time: str | None
    operation_type: str | None = None
    downloaded_by: str | None = None
    downloaded_by_job_no: str | None = None
    downloaded_at: str | None = None
    downloaded_version: str | None = None
    custom6: str | None = None
    physical_file_id: str | None = None
    physical_version_id: str | None = None
    physical_version_no: str | int | None
    physical_file_type: str | int | None = None
    physical_file_name: str | None
    physical_file_date: str | None
    physical_file_operator: str | None
    physical_file_operator_job_no: str | None
    physical_page_size: str | None = None
    physical_version_code: str | None
    physical_file_count: int | None = None
    physical_conflict: bool | int | None = None
    file_path: str | None = None
    normalized_path: str | None
    status: str
    status_label: str
    path_available: bool
    path_error: str | None
    dispatch_status: DispatchStatus | None = None


class FileSourceStatus(BaseModel):
    enabled: bool
    mode: str
    label: str
    message: str


class PdmStatus(BaseModel):
    mode: str
    enabled: bool
    label: str


class FileServerStatus(BaseModel):
    host: str | None
    physical_root: str | None


class MagicBoxStatus(BaseModel):
    configured: bool
    machine_ips: list[str]
    implemented: bool
    gateway: str | None = None
    message: str | None = None


class CuttingStatus(BaseModel):
    can_push: bool
    can_download: bool
    can_query: bool
    safe_mode: bool
    pdm: PdmStatus
    file_source: FileSourceStatus
    file_server: FileServerStatus
    magic_box: MagicBoxStatus


class MachineApplication(BaseModel):
    name: str
    version: str


class CuttingMachine(BaseModel):
    machine_ip: str
    server_name: str | None = None
    alive_time: str | None = None
    applications: list[MachineApplication] = []
    online: bool
    configured: bool


class MachineTime(BaseModel):
    box_time: str
    offset_seconds: float


class MachineResult(Items[CuttingMachine]):
    time: MachineTime


class FileCleanup(BaseModel):
    error: str | None = None
    deleted: bool | None = None
    already_deleted: bool | None = None


class CuttingDispatch(BaseModel):
    file_location: str | None = None
    processing_finished_at: str | None = None
    processing_finish_by: str | None = None
    completion_received_at: str | None = None
    processing_status: str
    processing_status_label: str
    dispatch_id: str
    document_id: str
    project_id: str
    project_name: str | None
    catalog_id: str | None
    catalog_title: str | None
    version_id: str | None
    version: str | None
    document_no: str
    relative_path: str
    machine_ip: str
    material: str | None
    thickness: float | None
    quantity: int
    status: str
    source_mode: str
    is_simulation: bool
    remote_task_id: str | None
    last_error: str | None
    created_by_id: str
    created_by_name: str
    created_at: str
    updated_at: str
    downloaded_at: str | None
    sent_at: str | None
    ship_no: str | None
    segment_name: str | None
    task_identifier: str | None
    download_id: str | None
    file_size: int | None
    file_sha256: str | None
    stage: str
    attempt_count: int
    attempted_at: str | None
    status_label: str
    stage_label: str
    can_execute: bool
    can_cancel: bool
    local_file_cleanup: FileCleanup | None = None


class DispatchDailyCount(BaseModel):
    date: Date
    sent_count: int
    completed_count: int


class DispatchTrend(BaseModel):
    start_date: Date
    end_date: Date
    timezone: str
    machine_ips: list[str]
    items: list[DispatchDailyCount]


class DispatchCreationResult(BaseModel):
    document_id: str | None
    download_id: str | None = None
    status: str
    dispatch: CuttingDispatch | None
    error: str | None


class BatchCreationResult(Items[DispatchCreationResult]):
    created_count: int
    duplicate_count: int
    failed_count: int


class MagicBoxTaskFinishedRequest(BaseModel):
    task_identifier: str = Field(default="", alias="taskIdentifier", max_length=128, strict=True)
    finish_by: str = Field(alias="FinishBy", min_length=1, max_length=256, strict=True)
    finish_time: datetime = Field(alias="finishTime", description="完成时间，YYYY-MM-DD HH:mm:ss，GMT+8")
    task_name: str = Field(default="", alias="taskName", max_length=512)
    material: str = Field(default="", max_length=80)
    thickness: float = Field(default=0, ge=0, le=1000, allow_inf_nan=False)

    @field_validator("task_identifier")
    @classmethod
    def clean_identifier(cls, value: str) -> str:
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("任务标识不能包含控制字符")
        return value.strip()

    @field_validator("finish_by")
    @classmethod
    def valid_machine(cls, value: str) -> str:
        ipaddress.ip_address(value.rsplit("(", 1)[-1].removesuffix(")").strip())
        return value

    @field_validator("finish_time", mode="before")
    @classmethod
    def local_finish_time(cls, value: object) -> datetime:
        if not isinstance(value, str):
            raise ValueError("完成时间格式无效")
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo("Asia/Shanghai"))

    @property
    def machine_ip(self) -> str:
        return str(ipaddress.ip_address(self.finish_by.rsplit("(", 1)[-1].removesuffix(")").strip()))


class MagicBoxCallbackResponse(BaseModel):
    status: Literal[0] = 0
    msg: Literal["OK"] = "OK"
