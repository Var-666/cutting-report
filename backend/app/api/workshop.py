from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.api.auth import require_user, require_worker
from app.config import settings
from app.database.workshop import (
    ConfirmationConflictError,
    DailyReportExistsError,
)
from app.models.domain import to_primitive


router = APIRouter(prefix="/api/v1/workshop", tags=["workshop-confirmation"])


class ConfirmTaskRequest(BaseModel):
    report_date: date


def _envelope(request: Request, data: object) -> dict:
    return {
        "success": True,
        "data": data,
        "request_id": request.state.request_id,
    }


def _records_for_date(request: Request, report_date: date):
    return request.app.state.workshop.get_daily_report_records(report_date.isoformat())


def _timestamp(value: datetime | None, *, missing: float) -> float:
    if value is None:
        return missing
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo(settings.timezone))
    return value.timestamp()


def sort_workshop_records(records: list, sort_mode: Literal["manager", "worker"]):
    """Put the rows that matter most to each page first."""
    production_missing = float("inf")

    if sort_mode == "worker":
        return sorted(
            records,
            key=lambda row: (
                1 if row.confirmed_by else 0,
                -_timestamp(row.confirmed_at, missing=0.0)
                if row.confirmed_by
                else _timestamp(row.production_time, missing=production_missing),
                _timestamp(row.production_time, missing=production_missing),
                row.task_id,
            ),
        )

    return sorted(
        records,
        key=lambda row: (
            0 if row.confirmed_by else 1,
            -_timestamp(row.confirmed_at, missing=0.0)
            if row.confirmed_by
            else _timestamp(row.production_time, missing=production_missing),
            _timestamp(row.production_time, missing=production_missing),
            row.task_id,
        ),
    )


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
    if existing is not None and existing["item_count"] > 0:
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
        )
        return _envelope(request, report or existing)
    try:
        report = request.app.state.workshop.create_daily_report(
            report_date=payload.report_date.isoformat(),
            source_mode=settings.effective_data_source_mode,
            records=bundle.workshop_records,
            user=user,
            now=now,
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
                "current_user": user,
            },
        )
    project_names = sorted(
        {row.project_code for row in records if row.project_code}
    )
    if project_name:
        records = [row for row in records if row.project_code == project_name]
    confirmed_count = sum(1 for row in records if row.confirmed_by)
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
            "current_user": user,
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
