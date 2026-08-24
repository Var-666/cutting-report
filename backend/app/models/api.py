from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class ReportCreateRequest(BaseModel):
    report_type: Literal["leadership", "workshop", "equipment", "custom"] = "workshop"
    report_date: date
    project_code: str | None = Field(default=None, max_length=128)
    ship_no: str | None = Field(default=None, max_length=128)
    definition_id: str | None = Field(default=None, max_length=80)
    parameters: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @field_validator("project_code", "ship_no")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        if any(ord(char) < 32 for char in cleaned):
            raise ValueError("不能包含控制字符")
        return cleaned

    @model_validator(mode="after")
    def validate_custom_definition(self):
        if self.report_type == "custom" and not self.definition_id:
            raise ValueError("自定义报表必须选择报表定义")
        if self.report_type != "custom" and self.definition_id:
            raise ValueError("内置报表不能携带自定义定义")
        return self


class ApiEnvelope(BaseModel):
    success: bool = True
    data: Any
    request_id: str


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorEnvelope(BaseModel):
    success: bool = False
    error: ErrorDetail
    request_id: str
