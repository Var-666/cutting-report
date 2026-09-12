from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class ReportCreateRequest(BaseModel):
    report_type: Literal["leadership", "workshop", "equipment", "custom"] = "workshop"
    report_date: date | None = None
    period_start: date | None = None
    period_end: date | None = None
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
        start = self.period_start or self.report_date
        end = self.period_end or start
        if start is None:
            raise ValueError("请选择报表开始日期")
        if end < start:
            raise ValueError("结束日期不能早于开始日期")
        if (end - start).days >= 366:
            raise ValueError("报表查询区间最长为 366 天")
        if self.report_type == "equipment" and (end - start).days >= 31:
            raise ValueError(
                "设备任务能耗查询区间最长为 31 天；"
                "近 12 个月趋势请使用设备报表默认月度汇总"
            )
        self.report_date = start
        self.period_start = start
        self.period_end = end
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
