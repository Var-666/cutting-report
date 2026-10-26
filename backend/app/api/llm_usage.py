from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.auth import require_admin
from app.config import settings


router = APIRouter(
    prefix="/api/v1/llm-usage",
    tags=["llm-usage"],
    dependencies=[Depends(require_admin)],
)


@router.get("")
def llm_usage(request: Request) -> dict:
    return {
        "success": True,
        "data": request.app.state.llm_usage.summary(),
        "request_id": request.state.request_id,
    }


@router.get("/report")
def usage_report(
    request: Request, start_date: date, end_date: date,
    granularity: Literal["day", "month"] = "day",
    scene: Literal["", "leadership", "workshop", "equipment", "alarm", "custom", "welding", "other"] = "",
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
) -> dict:
    if not 1900 <= start_date.year <= end_date.year <= 2100 or end_date < start_date:
        raise HTTPException(status_code=422, detail="统计日期范围无效")
    if (end_date - start_date).days >= 366:
        raise HTTPException(status_code=422, detail="统计区间最长 366 天")
    data = request.app.state.llm_usage.report(
        start_date, end_date, granularity=granularity, scene=scene, page=page, page_size=page_size,
        timezone_name=settings.timezone,
    )
    return {"success": True, "data": {**data, "timezone": settings.timezone,
            "start_date": start_date.isoformat(), "end_date": end_date.isoformat(),
            "granularity": granularity, "scene": scene}, "request_id": request.state.request_id}
