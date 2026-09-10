from __future__ import annotations

import logging
from dataclasses import replace
from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.api.auth import require_admin
from app.config import settings
from app.database.sqlserver import SqlServerConfigurationError
from app.models.domain import to_primitive


logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/v1/leadership-report",
    tags=["leadership-report"],
    dependencies=[Depends(require_admin)],
)


class LeadershipRefreshRequest(BaseModel):
    report_month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")


def _parse_month(value: str | None) -> date:
    if value is None:
        now = datetime.now(ZoneInfo(settings.timezone))
        return date(now.year, now.month, 1)
    try:
        return date.fromisoformat(f"{value}-01")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="月份格式必须为 YYYY-MM") from exc


def _result_with_refresh_state(request: Request, report_month: date, result):
    worker = getattr(request.app.state, "leadership_refresh_worker", None)
    refreshing = bool(worker and worker.is_refreshing(report_month))
    return replace(
        result,
        snapshot=replace(result.snapshot, refreshing=refreshing),
    )


@router.get("")
def get_leadership_report(
    request: Request,
    report_month: str | None = Query(
        default=None,
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
        description="报表月份，格式 YYYY-MM",
    ),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    refresh: bool = Query(default=False, description="提交后台刷新并返回现有快照"),
) -> dict:
    try:
        if start_date is not None or end_date is not None:
            selected_start = start_date or end_date
            selected_end = end_date or start_date
            assert selected_start is not None and selected_end is not None
            result = request.app.state.range_report_service.leadership(
                selected_start, selected_end
            )
            data = to_primitive(result)
            data.update(
                {
                    "period_start": selected_start.isoformat(),
                    "period_end": selected_end.isoformat(),
                    "aggregation": (
                        "day"
                        if (selected_end - selected_start).days < 90
                        else "month"
                    ),
                }
            )
            return {
                "success": True,
                "data": data,
                "request_id": request.state.request_id,
            }
        month_start = _parse_month(report_month)
        service = request.app.state.leadership_report_service
        result = service.fetch(month_start)
        worker = getattr(request.app.state, "leadership_refresh_worker", None)
        if worker is not None and (refresh or result.snapshot.stale):
            worker.schedule(
                month_start,
                bootstrap=service.needs_bootstrap(month_start),
            )
        result = _result_with_refresh_state(request, month_start, result)
    except (SqlServerConfigurationError, ValueError) as exc:
        if isinstance(exc, ValueError):
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("leadership report query failed")
        raise HTTPException(status_code=500, detail="领导报表查询失败。") from exc
    return {
        "success": True,
        "data": to_primitive(result),
        "request_id": request.state.request_id,
    }


@router.post("/refresh", status_code=status.HTTP_202_ACCEPTED)
def refresh_leadership_report(
    payload: LeadershipRefreshRequest,
    request: Request,
) -> dict:
    try:
        month_start = _parse_month(payload.report_month)
        service = request.app.state.leadership_report_service
        worker = getattr(request.app.state, "leadership_refresh_worker", None)
        if worker is None:
            result = service.fetch(month_start, force=True)
        else:
            worker.schedule(
                month_start,
                bootstrap=service.needs_bootstrap(month_start),
            )
            result = service.fetch(month_start)
        result = _result_with_refresh_state(request, month_start, result)
    except SqlServerConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("leadership report refresh failed")
        raise HTTPException(status_code=500, detail="领导报表刷新失败。") from exc
    return {
        "success": True,
        "data": to_primitive(result),
        "request_id": request.state.request_id,
    }
