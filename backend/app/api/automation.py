from __future__ import annotations

from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator, model_validator

from app.api.auth import require_admin
from app.config import settings


router = APIRouter(
    prefix="/api/v1/automation",
    tags=["report-automation"],
    dependencies=[Depends(require_admin)],
)


class SchedulePayload(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    report_type: Literal["leadership", "workshop", "equipment"]
    frequency: Literal["daily", "weekly", "monthly"] = "daily"
    run_time: str = Field(default="18:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    weekday: int | None = Field(default=None, ge=1, le=7)
    month_day: int | None = Field(default=None, ge=1, le=28)
    report_period: Literal[
        "current_day", "previous_day", "current_month", "previous_month"
    ] = "previous_day"
    recipients: list[str] = Field(min_length=1, max_length=20)
    subject_template: str = Field(
        default="{report_name} - {report_date}", min_length=2, max_length=200
    )
    chart_type: Literal["bar_line", "bar", "line", "area"] = "bar_line"
    attach_report: bool = True
    max_attempts: int = Field(default=3, ge=1, le=10)
    enabled: bool = True

    @field_validator("name", "subject_template")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or any(char in cleaned for char in "\r\n"):
            raise ValueError("不能为空或包含换行符")
        return cleaned

    @field_validator("recipients")
    @classmethod
    def validate_recipients(cls, values: list[str]) -> list[str]:
        recipients: list[str] = []
        for value in values:
            email = value.strip().lower()
            if (
                not email
                or "@" not in email
                or email.startswith("@")
                or email.endswith("@")
                or any(char in email for char in "\r\n,;")
            ):
                raise ValueError(f"收件人地址无效：{value}")
            if email not in recipients:
                recipients.append(email)
        if not recipients:
            raise ValueError("至少需要一个收件人")
        return recipients

    @model_validator(mode="after")
    def validate_schedule_shape(self):
        if self.frequency == "weekly" and self.weekday is None:
            raise ValueError("每周任务必须选择星期")
        if self.frequency == "monthly" and self.month_day is None:
            raise ValueError("每月任务必须选择执行日")
        month_periods = {"current_month", "previous_month"}
        if self.report_type == "leadership" and self.report_period not in month_periods:
            raise ValueError("领导报表必须选择本月或上月")
        if self.report_type != "leadership" and self.report_period in month_periods:
            raise ValueError("日报必须选择当日或前一日")
        return self


def _now() -> datetime:
    return datetime.now(ZoneInfo(settings.timezone))


def _envelope(request: Request, data: object) -> dict:
    return {"success": True, "data": data, "request_id": request.state.request_id}


@router.get("/status")
def status(request: Request) -> dict:
    return _envelope(
        request,
        {
            "automation_enabled": settings.automation_enabled,
            "smtp": request.app.state.email_service.status(),
            "summary": request.app.state.automation.summary(),
        },
    )


@router.get("/schedules")
def list_schedules(request: Request) -> dict:
    return _envelope(request, {"items": request.app.state.automation.list_schedules()})


@router.post("/schedules", status_code=201)
def create_schedule(
    payload: SchedulePayload,
    request: Request,
    user: dict = Depends(require_admin),
) -> dict:
    now = _now()
    data = request.app.state.automation_service.enrich_schedule(
        payload.model_dump(), now
    )
    item = request.app.state.automation.create_schedule(
        data, user=user, now=now.isoformat(timespec="seconds")
    )
    return _envelope(request, item)


@router.put("/schedules/{schedule_id}")
def update_schedule(
    schedule_id: str,
    payload: SchedulePayload,
    request: Request,
) -> dict:
    now = _now()
    data = request.app.state.automation_service.enrich_schedule(
        payload.model_dump(), now
    )
    item = request.app.state.automation.update_schedule(
        schedule_id, data, now=now.isoformat(timespec="seconds")
    )
    if item is None:
        raise HTTPException(status_code=404, detail="定时任务不存在")
    return _envelope(request, item)


@router.delete("/schedules/{schedule_id}")
def delete_schedule(schedule_id: str, request: Request) -> dict:
    if not request.app.state.automation.delete_schedule(schedule_id):
        raise HTTPException(status_code=404, detail="定时任务不存在")
    return _envelope(request, {"deleted": True})


@router.post("/schedules/{schedule_id}/run", status_code=202)
def run_schedule(schedule_id: str, request: Request) -> dict:
    run = request.app.state.automation_service.run_now(schedule_id, _now())
    if run is None:
        raise HTTPException(status_code=404, detail="定时任务不存在或无法执行")
    return _envelope(request, run)


@router.get("/deliveries")
def list_deliveries(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict:
    items, total = request.app.state.automation.list_deliveries(
        page=page, page_size=page_size
    )
    return _envelope(
        request,
        {"items": items, "total": total, "page": page, "page_size": page_size},
    )


@router.post("/deliveries/{delivery_id}/retry", status_code=202)
def retry_delivery(delivery_id: str, request: Request) -> dict:
    now = _now().isoformat(timespec="seconds")
    if not request.app.state.automation.retry_delivery(delivery_id, now=now):
        raise HTTPException(status_code=404, detail="邮件发送记录不存在")
    return _envelope(request, {"queued": True})
