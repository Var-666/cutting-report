from __future__ import annotations

import calendar
import json
import logging
import uuid
from datetime import date, datetime
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlencode
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from app.api.runtime import application_state, request_settings
from app.api.workloads import pdf_requests
from app.api.devices import device_state, device_settings
from app.api.auth import require_admin
from app.core.operations_logging import event, operation_context
from app.models.api import ReportCreateRequest
from app.models.contracts import ApiEnvelope, Page
from app.models.report_contracts import ReportAccepted, ReportJob
from app.modules.reports.pdf import PdfRenderError
from app.modules.reports.service import archived_report_path
from app.modules.reports.custom import PARAMETER_PATTERN


router = APIRouter(
    prefix="/api/v1/reports",
    tags=["reports"],
    dependencies=[Depends(require_admin)],
)
view_router = APIRouter(tags=["reports"], dependencies=[Depends(require_admin)])
# Persisted operations incident fingerprints include the logger name.
logger = logging.getLogger('app.api.reports')


def _request_id(request: Request) -> str:
    return request.state.request_id


def _serialize_job(job: dict, reports_dir: Path) -> dict:
    report_id = job["report_id"]
    period_start = job.get("period_start") or job["report_date"]
    period_end = job.get("period_end") or period_start
    if (job.get("report_type") or "workshop") == "leadership" and not job.get(
        "period_start"
    ):
        selected = date.fromisoformat(job["report_date"])
        period_start = selected.replace(day=1).isoformat()
        period_end = selected.replace(
            day=calendar.monthrange(selected.year, selected.month)[1]
        ).isoformat()
    result = {
        "report_id": report_id,
        "status": job["status"],
        "report_type": job.get("report_type") or "workshop",
        "definition_id": job.get("definition_id"),
        "report_title": job.get("report_title"),
        "stage_message": job["stage_message"],
        "report_date": job["report_date"],
        "period_start": period_start,
        "period_end": period_end,
        "project_code": job["project_code"],
        "ship_no": job["ship_no"],
        "created_at": job["created_at"],
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "ai_status": job.get("ai_status"),
        "error": None,
    }
    result['device_id'] = job['device_id']
    if job["status"] == "completed":
        try:
            archived_report_path(reports_dir, job)
        except OSError:
            message = "归档 HTML 文件缺失或不完整，请重新生成报告。"
            result.update(status="failed", stage_message=message, error={"code": "REPORT_FILE_UNAVAILABLE", "message": message})
            return result
        result.update(
            {
                "filename": job.get("filename"),
                "task_count": job.get("task_count"),
                "file_size": job.get("file_size"),
                "view_url": f"/reports/{report_id}/view",
                "download_url": f"/api/v1/reports/{report_id}/download",
                "generated_at": job.get("generated_at"),
            }
        )
    if job["status"] == "failed":
        result["error"] = {
            "code": job.get("error_code") or "REPORT_GENERATION_FAILED",
            "message": job.get("stage_message") or "报告生成失败",
        }
    for field in ('view_url', 'download_url'):
        if result.get(field):
            result[field] += '?' + urlencode({'device_id': result['device_id']})
    return result


router.dependencies.append(Depends(device_state))
view_router.dependencies.append(Depends(device_state))


@router.post("", status_code=202, response_model=None, responses={202: {"model": ApiEnvelope[ReportAccepted]}})
def create_report(payload: ReportCreateRequest, request: Request,
                  user: Annotated[dict, Depends(require_admin)],
                  idempotency_key: Annotated[uuid.UUID | None, Header()] = None) -> dict:
    now = datetime.now(ZoneInfo(request_settings(request).timezone))
    line_id = device_settings(request).device_id
    report_id = f"rpt_{now:%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}"
    if idempotency_key is not None:
        identity = json.dumps([user["user_id"], line_id, idempotency_key.hex])
        report_id = f"rpt_{sha256(identity.encode()).hexdigest()}"
    explicit_period = bool({"period_start", "period_end"} & payload.model_fields_set)
    fields = {
        "report_date": payload.report_date.isoformat(),
        "project_code": payload.project_code, "ship_no": payload.ship_no,
        "report_type": payload.report_type, "definition_id": payload.definition_id,
        "period_start": payload.period_start.isoformat() if explicit_period else None,
        "period_end": payload.period_end.isoformat() if explicit_period else None,
    }
    metadata = device_state(request).metadata
    existing = metadata.get_job(report_id) if idempotency_key is not None else None
    definition = None
    if existing is None and payload.report_type == "custom":
        definition = application_state(request).definitions.get_definition(payload.definition_id)
        if not definition or definition["is_builtin"] or not definition["enabled"]:
            raise HTTPException(status_code=400, detail="报表定义不存在或尚未启用")
        query = application_state(request).definitions.get_query_task(definition['query_task_id'])
        if query is None or 'machine_no' not in PARAMETER_PATTERN.findall(query['sql_text']):
            raise HTTPException(status_code=400, detail='该自定义报表尚未声明设备参数，请更新 SQL 定义')
    if existing is None:
        created = metadata.create_job(
            report_id=report_id, request_id=_request_id(request), created_at=now.isoformat(timespec="seconds"),
            report_title=definition["name"] if definition else None, parameters=payload.parameters,
            **({"allow_existing": True} if idempotency_key is not None else {}), **fields,
        )
        if idempotency_key is not None and not created:
            existing = metadata.get_job(report_id)
            if existing is None:
                raise HTTPException(status_code=409, detail="暂时无法确认任务，请到生成与历史查看")
    if existing is not None and (
        any(existing[key] != value for key, value in fields.items())
        or json.dumps(json.loads(existing["parameters_json"]), sort_keys=True) != json.dumps(payload.parameters, sort_keys=True)
    ):
        raise HTTPException(status_code=409, detail="此创建请求已用于不同报表条件")
    return {
        "success": True,
        "data": {
            "report_id": report_id,
            "status": "pending",
            "status_url": f"/api/v1/reports/{report_id}/status?" + urlencode({"device_id": line_id}),
        },
        "request_id": _request_id(request),
    }


@router.get("/{report_id}/status", response_model=None, responses={200: {"model": ApiEnvelope[ReportJob]}})
def get_report_status(report_id: str, request: Request) -> dict:
    job = device_state(request).metadata.get_job(report_id)
    if job is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    return {
        "success": True,
        "data": _serialize_job(job, request_settings(request).reports_dir),
        "request_id": _request_id(request),
    }


@router.get("", response_model=None, responses={200: {"model": ApiEnvelope[Page[ReportJob]]}})
def list_reports(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    report_date: date | None = None,
    project_code: str | None = Query(None, max_length=128),
    ship_no: str | None = Query(None, max_length=128),
    report_type: Literal["leadership", "workshop", "equipment", "custom"] | None = None,
) -> dict:
    items, total = device_state(request).metadata.list_reports(
        page=page,
        page_size=page_size,
        report_date=report_date.isoformat() if report_date else None,
        project_code=project_code,
        ship_no=ship_no,
        report_type=report_type,
    )
    reports_dir = request_settings(request).reports_dir
    return {
        "success": True,
        "data": {
            "items": [_serialize_job(item, reports_dir) for item in items],
            "page": page,
            "page_size": page_size,
            "total": total,
        },
        "request_id": _request_id(request),
    }


def _resolve_report_file(request: Request, report_id: str) -> tuple[dict, Path]:
    job = device_state(request).metadata.get_job(report_id)
    if job is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    relative_path = job.get("relative_path")
    if job["status"] != "completed" or not relative_path:
        raise HTTPException(status_code=404, detail="报告文件尚未生成")
    try:
        path = archived_report_path(request_settings(request).reports_dir, job, verify_hash=True)
    except OSError as exc:
        event(logger, logging.WARNING, "归档 HTML 无法下载", error_code="REPORT_FILE_UNAVAILABLE",
              report_id=report_id, line_id=job.get("device_id"), stage="download", related_kind="reports")
        raise HTTPException(status_code=404, detail="归档 HTML 文件缺失或不完整，请重新生成报告") from exc
    return job, path


@router.get("/{report_id}/download", response_class=FileResponse, responses={200: {"content": {media: {"schema": {"type": "string", "format": "binary"}} for media in ("text/html", "application/pdf")}}})
async def download_report(
    report_id: str,
    request: Request,
    format: Literal["html", "pdf"] = Query("html"),
) -> FileResponse:
    job, path = await run_in_threadpool(_resolve_report_file, request, report_id)
    if format == "pdf":
        try:
            with operation_context(report_id=report_id, line_id=job.get("device_id"), related_kind="reports"):
                path = await pdf_requests.run_sync(application_state(request).pdf_service.render, path, _request=request)
        except PdfRenderError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        filename = Path(job["filename"]).with_suffix(".pdf").name
        media_type = "application/pdf"
    else:
        filename = job["filename"]
        media_type = "text/html; charset=utf-8"
    return FileResponse(
        path,
        media_type=media_type,
        filename=filename,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@view_router.get("/reports/{report_id}/view")
def view_report(report_id: str, request: Request) -> FileResponse:
    _, path = _resolve_report_file(request, report_id)
    return FileResponse(
        path,
        media_type="text/html; charset=utf-8",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )
