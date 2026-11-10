"""Cutting brief contracts; welding retains its four-section analysis."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class BriefFindingInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    candidate_id: str
    title: str = Field(min_length=1)
    detail: str = Field(min_length=1)
    evidence_ids: list[str]


class BriefAction(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    candidate_id: str
    title: str = Field(min_length=1)
    detail: str = Field(min_length=1)


class BriefInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    headline: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    key_metric_ids: list[str] = Field(max_length=3)
    findings: list[BriefFindingInput]
    actions: list[BriefAction]


class BriefScope(BaseModel):
    report_type: Literal['leadership', 'workshop', 'equipment']
    device_id: str = ''
    line_name: str = ''
    period_start: str
    period_end: str
    period_mode: Literal['month', 'day', 'range']
    comparison_start: str | None = None
    comparison_end: str | None = None


class BusinessReference(BaseModel):
    kind: Literal['task', 'equipment', 'project', 'date']
    id: str
    label: str
    device_id: str = ''
    period_start: str = ''
    period_end: str = ''
    production_date: str | None = None
    project_code: str | None = None
    program_no: str | None = None
    work_time: float | None = None
    confirmation: str | None = None
    energy_status: str | None = None


class BriefFinding(BriefFindingInput):
    references: list[BusinessReference]
    reference_count: int


class AnalysisBrief(BaseModel):
    headline: str
    summary: str
    key_metric_ids: list[str]
    findings: list[BriefFinding]
    actions: list[BriefAction]
    scope: BriefScope
    data_quality: list[str]
