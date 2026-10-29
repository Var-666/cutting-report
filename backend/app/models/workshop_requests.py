"""Validated workshop HTTP inputs."""
from datetime import date
from typing import Literal
from pydantic import BaseModel, Field, field_validator, model_validator

class ConfirmTaskRequest(BaseModel):
    report_date: date

class ConfirmSnapshotTaskRequest(ConfirmTaskRequest):
    snapshot_revision: int | None = Field(default=None, ge=1)

class ConfirmTasksRequest(ConfirmTaskRequest):
    task_ids: list[str] = Field(min_length=1, max_length=100)
    snapshot_revisions: dict[str, int] = Field(default_factory=dict, max_length=100)

    @field_validator("snapshot_revisions")
    @classmethod
    def validate_revisions(cls, values: dict[str, int]) -> dict[str, int]:
        if any(not key or len(key) > 128 or value < 1 for key, value in values.items()):
            raise ValueError("任务修订号必须为正整数")
        return values

    @field_validator("task_ids")
    @classmethod
    def normalize_task_ids(cls, values: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(str(value).strip() for value in values))
        if any(not value or len(value) > 128 for value in normalized):
            raise ValueError("任务编号不能为空且不能超过128个字符")
        return normalized

class RevokeTaskRequest(ConfirmTaskRequest):
    reason: str = Field(min_length=2, max_length=200)

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 2:
            raise ValueError("请填写至少 2 个字的原因")
        return cleaned

class CorrectTaskRequest(RevokeTaskRequest):
    action: Literal["revoke", "reassign"]
    worker_id: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def validate_target_worker(self):
        if self.action == "reassign" and not self.worker_id:
            raise ValueError("改派时必须选择目标工人")
        if self.action == "revoke":
            self.worker_id = None
        return self
