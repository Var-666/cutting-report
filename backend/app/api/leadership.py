from __future__ import annotations

import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request

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


def _parse_month(value: str | None) -> date:
    if value is None:
        now = datetime.now(ZoneInfo(settings.timezone))
        return date(now.year, now.month, 1)
    try:
        return date.fromisoformat(f"{value}-01")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="月份格式必须为 YYYY-MM") from exc


@router.get("")
def get_leadership_report(
    request: Request,
    report_month: str | None = Query(
        default=None,
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
        description="报表月份，格式 YYYY-MM",
    ),
) -> dict:
    try:
        result = request.app.state.leadership_report_service.fetch(
            _parse_month(report_month)
        )
    except SqlServerConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("leadership report query failed")
        raise HTTPException(status_code=500, detail="领导报表查询失败。") from exc
    return {
        "success": True,
        "data": to_primitive(result),
        "request_id": request.state.request_id,
    }
