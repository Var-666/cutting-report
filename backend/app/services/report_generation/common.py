"""Shared report periods, metadata and the prepared report contract."""
from __future__ import annotations

import calendar
import json
from dataclasses import dataclass
from datetime import date, datetime

from app.config import Settings


@dataclass(frozen=True)
class PreparedReport:
    report: dict
    generated_at: datetime
    task_count: int
    analysis_status: str


REPORT_TITLES = {
    "leadership": "切割生产领导月报",
    "workshop": "切割生产车间日报",
    "equipment": "切割生产设备报表",
}


CHART_TYPES = {"bar", "line", "area", "bar_line"}


def _job_parameters(job: dict) -> dict:
    try:
        value = json.loads(job.get("parameters_json") or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _chart_type(job: dict) -> str:
    value = str(_job_parameters(job).get("chart_type") or "bar_line")
    return value if value in CHART_TYPES else "bar_line"


def _year_ago(value: date) -> date:
    try:
        return value.replace(year=value.year - 1)
    except ValueError:
        return value.replace(year=value.year - 1, day=28)


def _job_period(job: dict) -> tuple[date, date]:
    start = date.fromisoformat(job.get("period_start") or job["report_date"])
    end = date.fromisoformat(job.get("period_end") or job.get("period_start") or job["report_date"])
    if job.get("report_type") == "leadership" and not _is_range_job(job):
        start = start.replace(day=1)
        end = start.replace(day=calendar.monthrange(start.year, start.month)[1])
    return start, end


def _is_range_job(job: dict) -> bool:
    return bool(job.get("period_start") and job.get("period_end"))


def _period_label(start: date, end: date) -> str:
    return start.isoformat() if start == end else f"{start.isoformat()} 至 {end.isoformat()}"


def _recent_twelve_month_period(job: dict, fallback_end: date) -> tuple[date, date]:
    """Return the independent monthly-summary window used by report exports."""
    raw_end = _job_parameters(job).get("summary_period_end")
    try:
        end = date.fromisoformat(str(raw_end)) if raw_end else fallback_end
    except ValueError:
        end = fallback_end
    absolute_month = end.year * 12 + end.month - 1 - 11
    start = date(absolute_month // 12, absolute_month % 12 + 1, 1)
    return start, end


def report_meta(
    settings: Settings,
    *,
    job: dict,
    report_type: str,
    generated_at: datetime,
    data_updated_at: datetime | None,
    title: str | None = None,
) -> dict:
    start, end = _job_period(job)
    period_start, period_end = start.isoformat(), end.isoformat()
    custom_period = _is_range_job(job)
    resolved_title = title or REPORT_TITLES.get(report_type, report_type)
    if custom_period:
        resolved_title = {
            "leadership": "切割生产领导报表",
            "workshop": "切割生产车间报表",
            "equipment": "切割生产设备报表",
        }.get(report_type, resolved_title)
    return {
        "report_id": job["report_id"],
        "report_type": report_type,
        "title": resolved_title,
        "report_date": job["report_date"],
        "period_start": period_start,
        "period_end": period_end,
        "custom_period": custom_period,
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "system_version": settings.app_version,
        "data_updated_at": data_updated_at.isoformat(timespec="seconds")
        if data_updated_at
        else None,
    }
