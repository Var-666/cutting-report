from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator, model_validator

from app.api.runtime import application_state
from app.api.auth import require_admin
from app.services.automation_configuration import AutomationConfigurationRequest, automation_configuration_data
from app.services.runtime_settings_store import RuntimeSettingsSaveError
from app.config import DEVICE_CATALOG, settings
from app.models.contracts import ApiEnvelope, Items, Page
from app.models.automation_contracts import (AutomationConfiguration, AutomationStatus, AutomationSchedule, ScheduledRun, EmailDelivery, EmailResolution, Deleted, Queued, Resolved)
from app.models.configuration_contracts import ConfigurationSaved


logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/v1/automation",
    tags=["report-automation"],
    dependencies=[Depends(require_admin)],
)


class RecipientResolution(BaseModel):
    recipient: str = Field(min_length=1, max_length=320)
    outcome: Literal["accepted", "not_accepted"]


class DeliveryResolutionRequest(BaseModel):
    message_id: str = Field(min_length=1, max_length=255)
    outcomes: list[RecipientResolution] = Field(min_length=1, max_length=100)
    evidence_note: str = Field(min_length=1, max_length=1000)

    @field_validator("evidence_note")
    @classmethod
    def require_evidence(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("必须填写邮件服务器核对依据")
        return value.strip()


class SchedulePayload(BaseModel):
    device_id: str

    @field_validator("device_id")
    @classmethod
    def validate_device(cls, value: str) -> str:
        if value not in {item.id for item in DEVICE_CATALOG.devices}:
            raise ValueError("设备不存在")
        return value

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


@router.get("/configuration", response_model=None, responses={200: {"model": ApiEnvelope[AutomationConfiguration]}})
def get_automation_configuration(request: Request) -> dict:
    current_settings, _ = application_state(request).automation_runtime.snapshot()
    return _envelope(request, automation_configuration_data(current_settings))


@router.put("/configuration", response_model=None, responses={200: {"model": ApiEnvelope[ConfigurationSaved]}})
def save_automation_configuration(
    payload: AutomationConfigurationRequest, request: Request
) -> dict:
    try:
        application_state(request).automation_runtime.save_configuration(payload)
    except (RuntimeSettingsSaveError, ValueError, OSError):
        logger.error("failed to save report automation configuration")
        raise HTTPException(status_code=500, detail="配置保存失败，请检查 data 目录的写入权限。") from None
    return _envelope(
        request,
        {
            "saved": True,
            "restart_required": False,
            "path": "data/settings.env",
            "message": "报表自动化配置已保存并生效。",
        },
    )


@router.get("/status", response_model=None, responses={200: {"model": ApiEnvelope[AutomationStatus]}})
def status(request: Request) -> dict:
    current_settings, _ = application_state(request).automation_runtime.snapshot()
    return _envelope(
        request,
        {
            "automation_enabled": current_settings.automation_enabled,
            "smtp": application_state(request).email_service.status(
                settings_snapshot=current_settings
            ),
            "summary": application_state(request).automation.summary(),
        },
    )


@router.get("/schedules", response_model=None, responses={200: {"model": ApiEnvelope[Items[AutomationSchedule]]}})
def list_schedules(request: Request) -> dict:
    return _envelope(request, {"items": application_state(request).automation.list_schedules()})


@router.post("/schedules", status_code=201, response_model=None, responses={201: {"model": ApiEnvelope[AutomationSchedule]}})
def create_schedule(
    payload: SchedulePayload,
    request: Request,
    user: dict = Depends(require_admin),
) -> dict:
    now = _now()
    data = application_state(request).automation_service.enrich_schedule(
        payload.model_dump(), now
    )
    item = application_state(request).automation.create_schedule(
        data, user=user, now=now.isoformat(timespec="seconds")
    )
    return _envelope(request, item)


@router.put("/schedules/{schedule_id}", response_model=None, responses={200: {"model": ApiEnvelope[AutomationSchedule]}})
def update_schedule(
    schedule_id: str,
    payload: SchedulePayload,
    request: Request,
) -> dict:
    now = _now()
    data = application_state(request).automation_service.enrich_schedule(
        payload.model_dump(), now
    )
    item = application_state(request).automation.update_schedule(
        schedule_id, data, now=now.isoformat(timespec="seconds")
    )
    if item is None:
        raise HTTPException(status_code=404, detail="定时任务不存在")
    return _envelope(request, item)


@router.delete("/schedules/{schedule_id}", response_model=None, responses={200: {"model": ApiEnvelope[Deleted]}})
def delete_schedule(schedule_id: str, request: Request) -> dict:
    if not application_state(request).automation.delete_schedule(schedule_id):
        raise HTTPException(status_code=404, detail="定时任务不存在")
    return _envelope(request, {"deleted": True})


@router.post("/schedules/{schedule_id}/run", status_code=202, response_model=None, responses={202: {"model": ApiEnvelope[ScheduledRun]}})
def run_schedule(schedule_id: str, request: Request) -> dict:
    run = application_state(request).automation_service.run_now(schedule_id, _now())
    if run is None:
        raise HTTPException(status_code=404, detail="定时任务不存在或无法执行")
    return _envelope(request, run)


@router.get("/deliveries", response_model=None, responses={200: {"model": ApiEnvelope[Page[EmailDelivery]]}})
def list_deliveries(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict:
    items, total = application_state(request).automation.list_deliveries(
        page=page, page_size=page_size
    )
    return _envelope(
        request,
        {"items": items, "total": total, "page": page, "page_size": page_size},
    )


@router.post("/deliveries/{delivery_id}/retry", status_code=202, response_model=None, responses={202: {"model": ApiEnvelope[Queued]}})
def retry_delivery(delivery_id: str, request: Request) -> dict:
    now = _now().isoformat(timespec="seconds")
    if application_state(request).automation.delivery_state(delivery_id) is None:
        raise HTTPException(status_code=404, detail="邮件发送记录不存在")
    if not application_state(request).automation.retry_delivery(delivery_id, now=now):
        raise HTTPException(status_code=409, detail="仅明确失败的邮件可重试；发送中、已发送或结果待核对的邮件不可重发")
    return _envelope(request, {"queued": True})


@router.post("/deliveries/{delivery_id}/resolve", response_model=None, responses={200: {"model": ApiEnvelope[Resolved]}})
def resolve_delivery(delivery_id: str, payload: DeliveryResolutionRequest, request: Request,
                     user: dict = Depends(require_admin)) -> dict:
    try:
        changed = application_state(request).automation.resolve_unknown_delivery(
            delivery_id, message_id=payload.message_id,
            outcomes=[item.model_dump() for item in payload.outcomes],
            evidence_note=payload.evidence_note, user=user,
            now=_now().isoformat(timespec="seconds"),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not changed:
        raise HTTPException(status_code=409, detail="仅结果待核对的邮件可处理，请刷新当前状态")
    return _envelope(request, {"resolved": True})


@router.get("/deliveries/{delivery_id}/resolutions", response_model=None, responses={200: {"model": ApiEnvelope[Items[EmailResolution]]}})
def delivery_resolutions(delivery_id: str, request: Request) -> dict:
    return _envelope(request, {"items": application_state(request).automation.delivery_resolutions(delivery_id)})
