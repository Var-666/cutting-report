from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator, model_validator

from app.api.workloads import query_requests
from app.api.devices import device_state, device_settings
from app.api.auth import require_admin, require_user, require_worker
from app.config import settings
from app.database.sqlserver import SqlServerConfigurationError
from app.database.workshop import (
    ConfirmationConflictError,
    ConfirmationNotFoundError,
    ConfirmationPermissionError,
    DailyReportRecordMismatchError,
    DailyReportScopeMismatchError,
    DailyReportSourceMismatchError,
)
from app.models.domain import to_primitive, production_day
from app.services.statistics_service import (
    build_top_cut_length_tasks,
    build_worker_work_summaries,
    build_workshop_task_distributions,
)
from app.services.report_analysis_service import (
    analysis_period_label,
    workshop_analysis_source_version,
)
from app.services.workshop_sorting import sort_workshop_by_recency


router = APIRouter(prefix="/api/v1/workshop", tags=["workshop-confirmation"], dependencies=[Depends(require_user)])


def _workshop_analysis_source_version(report_date: str, records: list) -> str:
    return workshop_analysis_source_version(report_date, records)


def _workshop_trend(records: list) -> list[dict]:
    """Aggregate the filtered report rows into truthful daily KPI samples."""
    days: dict[str, list] = defaultdict(list)
    for row in records:
        stamp = production_day(row)
        if stamp is None:
            continue
        days[stamp.isoformat()].append(row)
    result = []
    for stamp, rows in sorted(days.items()):
        task_count = len(rows)
        confirmed_count = sum(bool(getattr(row, "confirmed_by", None)) for row in rows)
        project_count = len(
            {
                row.project_code
                for row in rows
                if getattr(row, "project_code", None)
            }
        )
        result.append(
            {
                "report_date": stamp,
                "task_count": task_count,
                "pending_count": task_count - confirmed_count,
                "confirmed_count": confirmed_count,
                "project_count": project_count,
                "confirmation_rate": (
                    round(confirmed_count / task_count * 100, 1) if task_count else 0
                ),
            }
        )
    return result


class ConfirmTaskRequest(BaseModel):
    report_date: date


class ConfirmSnapshotTaskRequest(ConfirmTaskRequest):
    snapshot_revision: int | None = Field(default=None, ge=1)


class ConfirmTasksRequest(ConfirmTaskRequest):
    task_ids: list[str] = Field(min_length=1, max_length=100)
    snapshot_revisions: dict[str, int] = Field(default_factory=dict, max_length=100)

    @field_validator("snapshot_revisions")
    @classmethod
    def validate_revisions(cls, values: dict[str, int]) -> dict[str, int]:
        if any(not key or len(key) > 128 or value < 1 for key, value in values.items()):
            raise ValueError("任务修订号必须为正整数")
        return values

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
    report, records = device_state(request).workshop.get_daily_report_records(
        report_date.isoformat()
    )
    if report and report.get("scope_machine_no") != device_settings(request).report_machine_no:
        return None, []
    return report, records


def sort_workshop_records(records: list, sort_mode: Literal["manager", "worker"]):
    """Keep both manager and worker tables in newest-to-oldest order."""
    return sort_workshop_by_recency(records, settings.timezone)


router.dependencies.append(Depends(device_state))


@query_requests.route(router.get("/latest-date"))
def latest_production_date(
    request: Request,
    on_or_before: date,
    user: dict = Depends(require_user),
) -> dict:
    latest = device_state(request).query_service.latest_production_date(on_or_before)
    return _envelope(
        request,
        {"report_date": latest.isoformat() if latest else None},
    )


@query_requests.route(router.post("/reports"))
def generate_daily_report(
    payload: ConfirmTaskRequest,
    request: Request,
    user: dict = Depends(require_user),
) -> dict:
    bundle = device_state(request).query_service.prepare_workshop_data(payload.report_date)
    now = datetime.now(ZoneInfo(settings.timezone)).isoformat(timespec="seconds")
    try:
        report = device_state(request).workshop.sync_daily_report(
            report_date=payload.report_date.isoformat(),
            source_mode=device_settings(request).effective_data_source_mode,
            records=bundle.workshop_records,
            user=user,
            now=now,
            scope_machine_no=device_settings(request).report_machine_no,
        )
    except (
        DailyReportRecordMismatchError,
        DailyReportScopeMismatchError,
        DailyReportSourceMismatchError,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _envelope(request, report)


@query_requests.route(router.get("/tasks"))
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
        device_state(request).query_service.prepare_workshop_data(report_date)
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
                "duration_distribution": [],
                "status_distribution": [],
                "top_cut_length_tasks": [],
                "analysis_source_version": workshop_analysis_source_version(
                    report_date.isoformat(), []
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
    distributions = build_workshop_task_distributions(records)
    top_cut_length_tasks = build_top_cut_length_tasks(records)
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
            **distributions,
            "top_cut_length_tasks": to_primitive(top_cut_length_tasks),
            "analysis_source_version": analysis_source_version,
            "current_user": user,
        },
    )


@query_requests.route(router.get("/range"))
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
        bundle, records = device_state(request).range_report_service.workshop_records(
            start_date, end_date
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SqlServerConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    analysis_source_version = _workshop_analysis_source_version(
        analysis_period_label(start_date, end_date), records
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
    distributions = build_workshop_task_distributions(records)
    top_cut_length_tasks = build_top_cut_length_tasks(records)
    trend = _workshop_trend(records)
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
                "scope_machine_no": device_settings(request).report_machine_no,
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
            "trend": trend,
            "worker_summaries": to_primitive(worker_summaries),
            **distributions,
            "top_cut_length_tasks": to_primitive(top_cut_length_tasks),
            "analysis_source_version": analysis_source_version,
        },
    )


@router.post("/tasks/{task_id}/confirm")
def confirm_task(
    task_id: str,
    payload: ConfirmSnapshotTaskRequest,
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
        confirmation = device_state(request).workshop.confirm_task(
            task_id=task_id,
            report_id=report["report_id"],
            user=user,
            now=now,
            snapshot_revision=payload.snapshot_revision,
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
        confirmations = device_state(request).workshop.confirm_tasks(
            task_ids=payload.task_ids,
            report_id=report["report_id"],
            user=user,
            now=now,
            snapshot_revisions=payload.snapshot_revisions,
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
    # 以当前设备的既有确认记录为准，保留其原始日报关联。
    now = datetime.now(ZoneInfo(settings.timezone)).isoformat(timespec="seconds")
    try:
        audit = device_state(request).workshop.revoke_confirmation(
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
    # 管理员更正沿用当前设备确认记录的原始日报关联。
    now = datetime.now(ZoneInfo(settings.timezone)).isoformat(timespec="seconds")
    try:
        audit = device_state(request).workshop.correct_confirmation(
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
        report = device_state(request).workshop.get_daily_report(report_date.isoformat())
        if report is None:
            return _envelope(request, {"items": []})
        report_id = report["report_id"]
    items = device_state(request).workshop.list_confirmation_audits(
        task_id=task_id,
        report_id=report_id,
        limit=limit,
    )
    return _envelope(request, {"items": items})
