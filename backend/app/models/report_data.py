"""Freshness metadata shared by statistics responses."""
from typing import Literal

from pydantic import BaseModel


class ReportDataMeta(BaseModel):
    line_id: str
    report_type: str
    data_version: str
    data_as_of: str
    refreshed_at: str
    expires_at: str
    source: Literal['snapshot', 'cache', 'database']
    stale: bool
    refreshing: bool
    refresh_failed: bool
    details_source: Literal['snapshot', 'cache', 'database'] | None = None
    today_data_as_of: str | None = None


class ReportMeta(BaseModel):
    report_data: ReportDataMeta
