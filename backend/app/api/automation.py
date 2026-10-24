from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

from app.api.auth import require_admin
from app.services.process_lock import settings_file_lock
from app.services.runtime_settings_store import read_runtime_env, write_runtime_env
from app.config import DEVICE_CATALOG, Settings, settings


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


class AutomationConfigurationRequest(BaseModel):
    automation_enabled: bool = True
    automation_scan_seconds: float = Field(default=15, ge=1, le=3600)
    email_retry_base_seconds: int = Field(default=60, ge=1, le=86400)
    smtp_enabled: bool = False
    smtp_host: str = Field(default="", max_length=255)
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str = Field(default="", max_length=255)
    smtp_password: SecretStr | None = Field(default=None, max_length=256)
    smtp_from_email: str = Field(default="", max_length=320)
    smtp_from_name: str = Field(default="切割生产智能产报系统", max_length=100)
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    smtp_timeout_seconds: float = Field(default=20, ge=1, le=300)

    @field_validator(
        "smtp_host", "smtp_username", "smtp_from_email", "smtp_from_name"
    )
    @classmethod
    def normalize_configuration_text(cls, value: str) -> str:
        cleaned = value.strip()
        if any(character in cleaned for character in ("\r", "\n", "\x00")):
            raise ValueError("不能包含换行或空字符")
        return cleaned

    @model_validator(mode="after")
    def validate_smtp_configuration(self):
        if self.smtp_use_tls and self.smtp_use_ssl:
            raise ValueError("STARTTLS 与 SSL 不能同时启用")
        if self.smtp_enabled:
            if not self.smtp_host:
                raise ValueError("启用 SMTP 后必须填写服务器地址")
            if (
                not self.smtp_from_email
                or "@" not in self.smtp_from_email
                or self.smtp_from_email.startswith("@")
                or self.smtp_from_email.endswith("@")
            ):
                raise ValueError("启用 SMTP 后必须填写有效的发件人邮箱")
        return self


class SchedulePayload(BaseModel):
    device_id: str = "Cutter01"

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


def _automation_configuration_data(current_settings: Settings | None = None) -> dict:
    current_settings = current_settings or settings
    return {
        "automation_enabled": current_settings.automation_enabled,
        "automation_scan_seconds": current_settings.automation_scan_seconds,
        "email_retry_base_seconds": current_settings.email_retry_base_seconds,
        "smtp_enabled": current_settings.smtp_enabled,
        "smtp_host": current_settings.smtp_host,
        "smtp_port": current_settings.smtp_port,
        "smtp_username": current_settings.smtp_username,
        "smtp_password_configured": bool(current_settings.smtp_password),
        "smtp_from_email": current_settings.smtp_from_email,
        "smtp_from_name": current_settings.smtp_from_name,
        "smtp_use_tls": current_settings.smtp_use_tls,
        "smtp_use_ssl": current_settings.smtp_use_ssl,
        "smtp_timeout_seconds": current_settings.smtp_timeout,
    }


def _write_automation_runtime_env(values: dict[str, object]) -> None:
    write_runtime_env(settings.runtime_settings_env_path, values)

@router.get("/configuration")
def get_automation_configuration(request: Request) -> dict:
    current_settings, _ = request.app.state.automation_runtime.snapshot()
    return _envelope(request, _automation_configuration_data(current_settings))


@router.put("/configuration")
def save_automation_configuration(
    payload: AutomationConfigurationRequest, request: Request
) -> dict:
    runtime = request.app.state.automation_runtime
    with runtime.update_lock, settings_file_lock(settings.runtime_settings_env_path):
        current_settings, _ = runtime.snapshot()
        submitted_password = (
            payload.smtp_password.get_secret_value()
            if payload.smtp_password is not None
            else None
        )
        saved_password = None
        if settings.runtime_settings_env_path.is_file():
            saved_password = read_runtime_env(settings.runtime_settings_env_path).get("SMTP_PASSWORD")
        password = submitted_password
        if submitted_password in {None, ""}:
            password = (
                saved_password if saved_password is not None else current_settings.smtp_password
            )
        values: dict[str, object] = {
            "AUTOMATION_ENABLED": str(payload.automation_enabled).lower(),
            "AUTOMATION_SCAN_SECONDS": payload.automation_scan_seconds,
            "EMAIL_RETRY_BASE_SECONDS": payload.email_retry_base_seconds,
            "SMTP_ENABLED": str(payload.smtp_enabled).lower(),
            "SMTP_HOST": payload.smtp_host,
            "SMTP_PORT": payload.smtp_port,
            "SMTP_USERNAME": payload.smtp_username,
            "SMTP_PASSWORD": password,
            "SMTP_FROM_EMAIL": payload.smtp_from_email,
            "SMTP_FROM_NAME": payload.smtp_from_name,
            "SMTP_USE_TLS": str(payload.smtp_use_tls).lower(),
            "SMTP_USE_SSL": str(payload.smtp_use_ssl).lower(),
            "SMTP_TIMEOUT_SECONDS": payload.smtp_timeout_seconds,
        }
        try:
            _write_automation_runtime_env(values)
        except (OSError, ValueError) as exc:
            logger.exception("failed to save report automation configuration")
            raise HTTPException(
                status_code=500,
                detail="配置保存失败，请检查 data 目录的写入权限。",
            ) from exc
        runtime.apply(
            automation_enabled=payload.automation_enabled,
            automation_scan_seconds=payload.automation_scan_seconds,
            email_retry_base_seconds=payload.email_retry_base_seconds,
            smtp_enabled=payload.smtp_enabled,
            smtp_host=payload.smtp_host,
            smtp_port=payload.smtp_port,
            smtp_username=payload.smtp_username,
            smtp_password=password,
            smtp_from_email=payload.smtp_from_email,
            smtp_from_name=payload.smtp_from_name,
            smtp_use_tls=payload.smtp_use_tls,
            smtp_use_ssl=payload.smtp_use_ssl,
            smtp_timeout=payload.smtp_timeout_seconds,
        )
    return _envelope(
        request,
        {
            "saved": True,
            "restart_required": False,
            "path": "data/settings.env",
            "message": "报表自动化配置已保存并生效。",
        },
    )


@router.get("/status")
def status(request: Request) -> dict:
    current_settings, _ = request.app.state.automation_runtime.snapshot()
    return _envelope(
        request,
        {
            "automation_enabled": current_settings.automation_enabled,
            "smtp": request.app.state.email_service.status(
                settings_snapshot=current_settings
            ),
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
    if request.app.state.automation.delivery_state(delivery_id) is None:
        raise HTTPException(status_code=404, detail="邮件发送记录不存在")
    if not request.app.state.automation.retry_delivery(delivery_id, now=now):
        raise HTTPException(status_code=409, detail="仅明确失败的邮件可重试；发送中、已发送或结果待核对的邮件不可重发")
    return _envelope(request, {"queued": True})


@router.post("/deliveries/{delivery_id}/resolve")
def resolve_delivery(delivery_id: str, payload: DeliveryResolutionRequest, request: Request,
                     user: dict = Depends(require_admin)) -> dict:
    try:
        changed = request.app.state.automation.resolve_unknown_delivery(
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


@router.get("/deliveries/{delivery_id}/resolutions")
def delivery_resolutions(delivery_id: str, request: Request) -> dict:
    return _envelope(request, {"items": request.app.state.automation.delivery_resolutions(delivery_id)})
