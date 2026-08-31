from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.auth import require_admin


router = APIRouter(prefix="/api/v1/energy-report", tags=["energy-report"], dependencies=[Depends(require_admin)])


@router.get("/day")
def get_energy_day(request: Request, report_date: date | None = None, refresh: bool = False):
    service = request.app.state.energy_period_service
    selected = report_date or service.now().date()
    if not 1900 <= selected.year <= 2100:
        raise HTTPException(status_code=422, detail="日期需在 1900—2100 年之间")
    data = service.get_day(selected, force=refresh)
    return {"success": True, "data": data, "request_id": request.state.request_id}


@router.get("/month")
def get_energy_month(request: Request, report_month: str = Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")):
    try:
        selected = date.fromisoformat(report_month + "-01")
        if not 1900 <= selected.year <= 2100:
            raise ValueError()
    except ValueError:
        raise HTTPException(status_code=422, detail="月份无效") from None
    data = request.app.state.energy_period_service.get_month(report_month)
    return {"success": True, "data": data, "request_id": request.state.request_id}
