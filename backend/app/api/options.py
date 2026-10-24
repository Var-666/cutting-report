from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query, Request

from app.api.workloads import query_requests
from app.api.devices import device_state
from app.api.auth import require_admin
from app.models.domain import ReportFilter


router = APIRouter(
    prefix="/api/v1/options",
    tags=["options"],
    dependencies=[Depends(require_admin)],
)


router.dependencies.append(Depends(device_state))


@query_requests.route(router.get("/projects"))
def list_projects(report_date: date, request: Request) -> dict:
    filters = ReportFilter(report_date=report_date)
    return {
        "success": True,
        "data": {"items": device_state(request).query_service.list_projects(filters)},
        "request_id": request.state.request_id,
    }


@query_requests.route(router.get("/ships"))
def list_ships(
    report_date: date,
    request: Request,
    project_code: str | None = Query(None, max_length=128),
) -> dict:
    filters = ReportFilter(report_date=report_date, project_code=project_code)
    return {
        "success": True,
        "data": {"items": device_state(request).query_service.list_ships(filters)},
        "request_id": request.state.request_id,
    }
