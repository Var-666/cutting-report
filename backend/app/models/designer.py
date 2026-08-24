from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ColumnDefinition(BaseModel):
    field: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=64)
    label: str = Field(min_length=1, max_length=64)
    type: Literal["text", "number", "date", "datetime", "duration"] = "text"
    unit: str = Field(default="", max_length=24)


class QueryTaskUpsert(BaseModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    sql_text: str = Field(min_length=1, max_length=50_000)
    columns: list[ColumnDefinition] = Field(min_length=1, max_length=80)
    enabled: bool = True


class TemplateConfig(BaseModel):
    title_prefix: str = Field(default="切割生产", max_length=50)
    logo_text: str = Field(default="PRODUCTION REPORT", max_length=50)
    primary_color: str = "#1f4e78"
    accent_color: str = "#56a3a6"
    background_color: str = "#eef3f7"
    layout: Literal["split", "chart_first", "table_first"] = "split"
    table_density: Literal["compact", "normal", "comfortable"] = "normal"
    chart_height: int = Field(default=300, ge=220, le=520)
    show_meta: bool = True

    @field_validator("primary_color", "accent_color", "background_color")
    @classmethod
    def validate_color(cls, value: str) -> str:
        if len(value) != 7 or not value.startswith("#"):
            raise ValueError("颜色必须是 #RRGGBB 格式")
        try:
            int(value[1:], 16)
        except ValueError as exc:
            raise ValueError("颜色必须是 #RRGGBB 格式") from exc
        return value.lower()


class TemplateUpsert(BaseModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    config: TemplateConfig
    enabled: bool = True


class MetricDefinition(BaseModel):
    field: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=64)
    label: str = Field(min_length=1, max_length=64)
    aggregation: Literal["sum", "avg", "count", "max", "min"] = "sum"
    unit: str = Field(default="", max_length=24)


class ChartDefinition(BaseModel):
    type: Literal["bar", "line", "area", "bar_line"] = "bar"
    category_field: str = Field(default="", max_length=64)
    value_fields: list[str] = Field(default_factory=list, max_length=6)
    title: str = Field(default="数据趋势", max_length=80)


class VisualConfig(BaseModel):
    columns: list[str] = Field(default_factory=list, max_length=80)
    metrics: list[MetricDefinition] = Field(default_factory=list, max_length=8)
    chart: ChartDefinition = Field(default_factory=ChartDefinition)


class DefinitionUpsert(BaseModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    query_task_id: str = Field(min_length=1, max_length=80)
    template_id: str = Field(min_length=1, max_length=80)
    period_type: Literal["day", "month"] = "day"
    visual_config: VisualConfig
    enabled: bool = True


class QueryPreviewRequest(BaseModel):
    report_date: date
    project_code: str | None = Field(default=None, max_length=64)
    ship_no: str | None = Field(default=None, max_length=64)
    parameters: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    max_rows: int = Field(default=20, ge=1, le=100)
