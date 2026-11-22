from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ReportType = Literal['daily', 'parts', 'jobs', 'equipment']
WELD_TYPES = {0: '平焊包角', 1: '平焊', 2: '立焊包角', 3: '立焊'}


class WeldingQuery(BaseModel):
    model_config = ConfigDict(extra='forbid')
    start: date
    end: date
    device_no: int | None = Field(default=None, ge=0)
    robot_no: int | None = Field(default=None, ge=0)
    weld_type: int | None = None
    state: int | None = None
    part_state: int | None = None
    serial_number: str = Field(default='', max_length=255)
    project_no: str = Field(default='', max_length=255)
    section_no: str = Field(default='', max_length=255)
    part_no: str = Field(default='', max_length=255)
    anomalies_only: bool = False
    page: int = Field(default=1, ge=1, le=100000)
    page_size: int = Field(default=20, ge=1, le=100)
    sort_by: Literal['time', 'length', 'id'] = 'time'
    descending: bool = True

    @field_validator('serial_number', 'project_no', 'section_no', 'part_no')
    @classmethod
    def clean_text(cls, value: str) -> str:
        if any(ord(char) < 32 for char in value):
            raise ValueError('筛选条件不能包含控制字符')
        return value.strip()

    @model_validator(mode='after')
    def validate_period(self):
        if self.end < self.start or (self.end - self.start).days >= 366 or self.end == date.max:
            raise ValueError('请选择有效日期区间，最长366天')
        return self

    @property
    def bounds(self) -> tuple[str, str]:
        return f'{self.start} 00:00:00', f'{self.end + timedelta(days=1)} 00:00:00'


class WeldingAnalysisRequest(BaseModel):
    report_type: ReportType = 'daily'
    query: WeldingQuery
    data_version: str = Field(pattern=r'^[0-9a-f]{64}$')
