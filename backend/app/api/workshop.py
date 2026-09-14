from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator, model_validator

from app.api.auth import require_admin, require_user, require_worker
from app.config import settings
from app.database.sqlserver import SqlServerConfigurationError
from app.database.workshop import (
    ConfirmationConflictError,
    ConfirmationNotFoundError,
    ConfirmationPermissionError,
    DailyReportExistsError,
)
from app.models.domain import to_primitive
from app.services.statistics_service import build_worker_work_summaries
from app.services.report_analysis_service import generate_data_version
from app.services.workshop_sorting import sort_workshop_by_recency


router = APIRouter(prefix="/api/v1/workshop", tags=["workshop-confirmation"])


def _workshop_analysis_source_version(report_date: str, records: list) -> str:
    # Query order is not business data, so records are canonicalized by task ID.
    ordered = sorted(records, key=lambda row: str(row.task_id))
    return generate_data_version(
        {
            "report_date": report_date,
            "records": to_primitive(ordered),
        }
    )


class ConfirmTaskRequest(BaseModel):
    report_date: date


class ConfirmTasksRequest(ConfirmTaskRequest):
    task_ids: list[str] = Field(min_length=1, max_length=100)

    @field_validator("task_ids")
    @classmethod
    def normalize_task_ids(cls, values: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(str(value).strip() for value in values))
        if any(not value or len(value) > 128 for value in normalized):
            raise ValueError("任务编号不能为空且不能超过128个字符")
        return normalized


class RevokeTaskRequest(ConfirmTaskRequest):
    reason: str = Field(min_length=2, max_length=200)

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 2:
            raise ValueError("请填写至少 2 个字的原因")
        return cleaned


class CorrectTaskRequest(RevokeTaskRequest):
    action: Literal["revoke", "reassign"]
    worker_id: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def validate_target_worker(self):
        if self.action == "reassign" and not self.worker_id:
            raise ValueError("改派时必须选择目标工人")
        if self.action == "revoke":
            self.worker_id = None
        return self


def _envelope(request: Request, data: object) -> dict:
    return {
        "success": True,
        "data": data,
        "request_id": request.state.request_id,
    }


def _records_for_date(request: Request, report_date: date):
    report, records = request.app.state.workshop.get_daily_report_records(
        report_date.isoformat()
    )
    if report and report.get("scope_machine_no") != settings.report_machine_no:
        return None, []
    return report, records


def sort_workshop_records(records: list, sort_mode: Literal["manager", "worker"]):
    """Keep both manager and worker tables in newest-to-oldest order."""
    return sort_workshop_by_recency(records, settings.timezone)


@router.get("/latest-date")
def latest_production_date(
    request: Request,
    on_or_before: date,
    user: dict = Depends(require_user),
) -> dict:
    latest = request.app.state.query_service.latest_production_date(on_or_before)
    return _envelope(
        request,
        {"report_date": latest.isoformat() if latest else None},
    )


@router.post("/reports")
def generate_daily_report(
    payload: ConfirmTaskRequest,
    request: Request,
    user: dict = Depends(require_user),
) -> dict:
    existing = request.app.state.workshop.get_daily_report(
        payload.report_date.isoformat()
    )
    if (
        existing is not None
        and existing["item_count"] > 0
        and existing.get("scope_machine_no") == settings.report_machine_no
    ):
        return _envelope(request, existing)
    bundle = request.app.state.query_service.prepare_workshop_data(payload.report_date)
    now = datetime.now(ZoneInfo(settings.timezone)).isoformat(timespec="seconds")
    if existing is not None:
        report = request.app.state.workshop.populate_empty_daily_report(
            report_date=payload.report_date.isoformat(),
            source_mode=settings.effective_data_source_mode,
            records=bundle.workshop_records,
            user=user,
            now=now,
            scope_machine_no=settings.report_machine_no,
        )
        return _envelope(request, report or existing)
    try:
        report = request.app.state.workshop.create_daily_report(
            report_date=payload.report_date.isoformat(),
            source_mode=settings.effective_data_source_mode,
            records=bundle.workshop_records,
            user=user,
            now=now,
            scope_machine_no=settings.report_machine_no,
        )
    except DailyReportExistsError as exc:
        report = request.app.state.workshop.get_daily_report(
            payload.report_date.isoformat()
        )
        if report is None:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _envelope(request, report)


@router.get("/tasks")
def list_tasks(
    request: Request,
    report_date: date,
    project_name: str | None = Query(default=None, max_length=128),
    confirmation_status: Literal["all", "confirmed", "unconfirmed"] = "all",
    sort_mode: Literal["manager", "worker"] = "manager",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=10, le=100),
    warmup: bool = False,
    user: dict = Depends(require_user),
) -> dict:
    report, records = _records_for_date(request, report_date)
    if warmup and (report is None or not report["item_count"]):
        request.app.state.query_service.prepare_workshop_data(report_date)
    if report is None:
        return _envelope(
            request,
            {
                "report": None,
                "items": [],
                "page": 1,
                "page_size": page_size,
                "total": 0,
                "project_names": [],
                "confirmed_count": 0,
                "worker_summaries": [],
                "analysis_source_version": generate_data_version(
                    {"report_date": report_date.isoformat(), "records": []}
                ),
                "current_user": user,
            },
        )
    analysis_source_version = _workshop_analysis_source_version(
        report_date.isoformat(), records
    )
    project_names = sorted(
        {row.project_code for row in records if row.project_code}
    )
    if project_name:
        records = [row for row in records if row.project_code == project_name]
    confirmed_count = sum(1 for row in records if row.confirmed_by)
    worker_summaries = (
        build_worker_work_summaries(records) if user.get("role") == "admin" else []
    )
    if confirmation_status == "confirmed":
        records = [row for row in records if row.confirmed_by]
    elif confirmation_status == "unconfirmed":
        records = [row for row in records if not row.confirmed_by]
    records = sort_workshop_records(records, sort_mode)

    total = len(records)
    start = (page - 1) * page_size
    items = to_primitive(records[start : start + page_size])
    return _envelope(
        request,
        {
            "report": report,
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
            "project_names": project_names,
            "confirmed_count": confirmed_count,
            "worker_summaries": to_primitive(worker_summaries),
            "analysis_source_version": analysis_source_version,
            "current_user": user,
        },
    )


@router.get("/range")
def list_range_tasks(
    request: Request,
    start_date: date,
    end_date: date,
    project_name: str | None = Query(default=None, max_length=128),
    confirmation_status: Literal["all", "confirmed", "unconfirmed"] = "all",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=10, le=100),
    _: dict = Depends(require_admin),
) -> dict:
    try:
        bundle, records = request.app.state.range_report_service.workshop_records(
            start_date, end_date
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SqlServerConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    analysis_source_version = _workshop_analysis_source_version(
        f"{start_date.isoformat()} 至 {end_date.isoformat()}", records
    )
    project_names = sorted(
        {row.project_code for row in records if row.project_code}
    )
    if project_name:
        records = [row for row in records if row.project_code == project_name]
    worker_summaries = build_worker_work_summaries(records)
    if confirmation_status == "confirmed":
        records = [row for row in records if row.confirmed_by]
    elif confirmation_status == "unconfirmed":
        records = [row for row in records if not row.confirmed_by]
    records = sort_workshop_records(records, "manager")
    total = len(records)
    start = (page - 1) * page_size
    visible = records[start : start + page_size]
    return _envelope(
        request,
        {
            "report": {
                "report_date": start_date.isoformat(),
                "period_start": start_date.isoformat(),
                "period_end": end_date.isoformat(),
                "scope_machine_no": settings.report_machine_no,
                "item_count": total,
                "confirmed_count": sum(bool(row.confirmed_by) for row in records),
                "generated_at": to_primitive(bundle.data_updated_at),
            },
            "items": to_primitive(visible),
            "total": total,
            "page": page,
            "page_size": page_size,
            "project_names": project_names,
            "confirmed_count": sum(bool(row.confirmed_by) for row in records),
            "worker_summaries": to_primitive(worker_summaries),
            "analysis_source_version": analysis_source_version,
        },
    )


@router.post("/tasks/{task_id}/confirm")
def confirm_task(
    task_id: str,
    payload: ConfirmTaskRequest,
    request: Request,
    user: dict = Depends(require_worker),
) -> dict:
    report, records = _records_for_date(request, payload.report_date)
    if report is None:
        raise HTTPException(status_code=404, detail="当日车间报表尚未生成")
    if not any(row.task_id == task_id for row in records):
        raise HTTPException(status_code=404, detail="当日车间报表中没有找到该任务")
    now = datetime.now(ZoneInfo(settings.timezone)).isoformat(timespec="seconds")
    try:
        confirmation = request.app.state.workshop.confirm_task(
            task_id=task_id,
            report_id=report["report_id"],
            user=user,
            now=now,
        )
    except ConfirmationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _envelope(request, confirmation)


@router.post("/tasks/confirm")
def confirm_tasks(
    payload: ConfirmTasksRequest,
    request: Request,
    user: dict = Depends(require_worker),
) -> dict:
    report, records = _records_for_date(request, payload.report_date)
    if report is None:
        raise HTTPException(status_code=404, detail="当日车间报表尚未生成")
    available_task_ids = {row.task_id for row in records}
    if any(task_id not in available_task_ids for task_id in payload.task_ids):
        raise HTTPException(status_code=404, detail="当日车间报表中没有找到部分任务")
    now = datetime.now(ZoneInfo(settings.timezone)).isoformat(timespec="seconds")
    try:
        confirmations = request.app.state.workshop.confirm_tasks(
            task_ids=payload.task_ids,
            report_id=report["report_id"],
            user=user,
            now=now,
        )
    except ConfirmationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _envelope(
        request,
        {"items": confirmations, "confirmed_count": len(confirmations)},
    )


@router.post("/tasks/{task_id}/revoke")
def revoke_task_confirmation(
    task_id: str,
    payload: RevokeTaskRequest,
    request: Request,
    user: dict = Depends(require_worker),
) -> dict:
    # 撤销以已存在的确认记录为准。历史日报可能没有 scope_machine_no，
    # 但确认记录仍然有效，不能因此阻止工人撤销本人的确认。
    now = datetime.now(ZoneInfo(settings.timezone)).isoformat(timespec="seconds")
    try:
        audit = request.app.state.workshop.revoke_confirmation(
            task_id=task_id,
            report_id=None,
            user=user,
            reason=payload.reason,
            now=now,
        )
    except ConfirmationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConfirmationPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return _envelope(request, audit)


@router.post("/tasks/{task_id}/correct")
def correct_task_confirmation(
    task_id: str,
    payload: CorrectTaskRequest,
    request: Request,
    admin: dict = Depends(require_admin),
) -> dict:
    # 管理员更正操作针对既有确认记录。优先沿用确认记录自身的 report_id，
    # 避免旧日报快照因设备范围字段缺失而被误判为“尚未生成”。
    now = datetime.now(ZoneInfo(settings.timezone)).isoformat(timespec="seconds")
    try:
        audit = request.app.state.workshop.correct_confirmation(
            task_id=task_id,
            report_id=None,
            admin=admin,
            reason=payload.reason,
            now=now,
            new_worker_id=payload.worker_id,
        )
    except ConfirmationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ConfirmationConflictError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _envelope(request, audit)


@router.get("/confirmation-audits")
def list_confirmation_audits(
    request: Request,
    report_date: date | None = None,
    task_id: str | None = Query(default=None, max_length=128),
    limit: int = Query(default=30, ge=1, le=100),
    _: dict = Depends(require_admin),
) -> dict:
    report_id = None
    if report_date:
        report = request.app.state.workshop.get_daily_report(report_date.isoformat())
        if report is None:
            return _envelope(request, {"items": []})
        report_id = report["report_id"]
    items = request.app.state.workshop.list_confirmation_audits(
        task_id=task_id,
        report_id=report_id,
        limit=limit,
    )
    return _envelope(request, {"items": items})
