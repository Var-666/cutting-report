from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse

from app.api.auth import require_admin
from app.config import settings


router = APIRouter(
    prefix="/api/v1/alarms",
    tags=["alarms"],
    dependencies=[Depends(require_admin)],
)


@router.get("/status")
def alarm_status(request: Request) -> dict:
    repository = request.app.state.alarm_repository
    return {
        "success": True,
        "data": {
            "enabled": settings.alarm_monitor_enabled,
            "machine_nos": list(settings.alarm_monitor_machine_nos),
            "interval_seconds": settings.alarm_monitor_interval_seconds,
            "reopen_debounce_minutes": settings.alarm_reopen_debounce_minutes,
            "worker_running": request.app.state.alarm_worker.thread.is_alive(),
            **repository.get_sync_state(),
        },
        "request_id": request.state.request_id,
    }


@router.get("/incidents")
def list_alarm_incidents(
    request: Request,
    status: Literal["open", "closed"] | None = None,
    limit: int = Query(100, ge=1, le=500),
) -> dict:
    return {
        "success": True,
        "data": request.app.state.alarm_repository.list_incidents(status, limit),
        "request_id": request.state.request_id,
    }


@router.get("/reports")
def list_alarm_reports(
    request: Request, limit: int = Query(50, ge=1, le=200)
) -> dict:
    reports = request.app.state.alarm_repository.list_reports(limit)
    for report in reports:
        if report["status"] == "completed":
            report["download_url"] = f"/api/v1/alarms/reports/{report['report_id']}/pdf"
            report["view_url"] = f"/api/v1/alarms/reports/{report['report_id']}/view"
    return {
        "success": True,
        "data": reports,
        "request_id": request.state.request_id,
    }


def _report_path(request: Request, report_id: str, field: str) -> tuple[dict, Path]:
    report = request.app.state.alarm_repository.get_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="告警报告不存在")
    relative_path = report.get(field)
    if report["status"] != "completed" or not relative_path:
        raise HTTPException(status_code=404, detail="告警报告文件尚未生成")
    root = settings.reports_dir.resolve()
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(status_code=404, detail="告警报告文件不存在")
    return report, path


@router.get("/reports/{report_id}/pdf")
def download_alarm_report(report_id: str, request: Request) -> FileResponse:
    report, path = _report_path(request, report_id, "pdf_relative_path")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=report["pdf_filename"],
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"},
    )


@router.get("/reports/{report_id}/view")
def view_alarm_report(report_id: str, request: Request) -> FileResponse:
    _, path = _report_path(request, report_id, "html_relative_path")
    return FileResponse(
        path,
        media_type="text/html; charset=utf-8",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"},
    )
