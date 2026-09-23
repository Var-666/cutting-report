from datetime import date
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.auth import require_admin
from app.database.sqlserver import SqlServerConfigurationError
from app.models.domain import ReportFilter, to_primitive
from app.services.report_analysis_service import equipment_analysis_source_version
from app.services.request_cancellation import SqlRequestCancelled


router = APIRouter(prefix="/api/v1/energy-report", tags=["energy-report"], dependencies=[Depends(require_admin)])
logger = logging.getLogger(__name__)


def _add_analysis_source_version(task_report: dict) -> dict:
    task_report["analysis_source_version"] = equipment_analysis_source_version(
        task_report
    )
    return task_report


@router.get("/day")
def get_energy_day(request: Request, report_date: date | None = None, refresh: bool = False):
    service = request.app.state.energy_period_service
    selected = report_date or service.now().date()
    if not 1900 <= selected.year <= 2100:
        raise HTTPException(status_code=422, detail="日期需在 1900—2100 年之间")
    data = service.get_day(selected, force=refresh)
    return {"success": True, "data": data, "request_id": request.state.request_id}


@router.get("/tasks")
def get_task_energy_day(request: Request, report_date: date | None = None, refresh: bool = False):
    service = request.app.state.energy_period_service
    selected = report_date or service.now().date()
    if not 1900 <= selected.year <= 2100:
        raise HTTPException(status_code=422, detail="日期需在 1900—2100 年之间")
    try:
        equipment = request.app.state.query_service.fetch_equipment_data(
            ReportFilter(report_date=selected), force=refresh
        )
        data = service.get_task_day(selected, equipment.records, force=refresh)
        data["data_updated_at"] = to_primitive(equipment.data_updated_at)
        _add_analysis_source_version(data)
    except SqlRequestCancelled:
        raise
    except SqlServerConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("task energy report query failed")
        raise HTTPException(status_code=500, detail="任务能耗查询失败。") from exc
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


@router.get("/monthly-range")
def get_energy_monthly_range(request: Request, start_date: date, end_date: date):
    try:
        data = request.app.state.energy_period_service.get_monthly_range(
            start_date, end_date
        )
        worker = getattr(request.app.state, "energy_history_worker", None)
        if worker is not None and data.get("missing_day_count", 0):
            worker.schedule_range(start_date, end_date)
            data["syncing"] = worker.is_syncing_range(start_date, end_date)
        else:
            data["syncing"] = False
    except SqlRequestCancelled:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SqlServerConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("equipment monthly range report query failed")
        raise HTTPException(status_code=500, detail="设备月度汇总查询失败。") from exc
    return {"success": True, "data": data, "request_id": request.state.request_id}


@router.get("/range")
def get_energy_range(
    request: Request,
    start_date: date,
    end_date: date,
    refresh: bool = False,
):
    try:
        data = request.app.state.range_report_service.equipment(
            start_date, end_date, force=refresh
        )
        _add_analysis_source_version(data["task_report"])
    except SqlRequestCancelled:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SqlServerConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("equipment range report query failed")
        raise HTTPException(status_code=500, detail="设备区间报表查询失败。") from exc
    return {"success": True, "data": data, "request_id": request.state.request_id}
