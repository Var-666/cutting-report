from __future__ import annotations

import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.auth import require_admin
from app.config import settings
from app.database.sqlserver import SqlServerConfigurationError
from app.models.domain import ReportFilter, to_primitive
from app.services.equipment_service import (
    mock_energy,
    sort_equipment_records,
    summarize_equipment,
)


logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/v1/equipment-report",
    tags=["equipment-report"],
    dependencies=[Depends(require_admin)],
)


@router.get("")
def get_equipment_report(
    request: Request,
    report_date: date | None = Query(default=None),
    refresh: bool = Query(default=False, description="忽略缓存并重新查询"),
) -> dict:
    try:
        selected_date = report_date
        if selected_date is None:
            today = datetime.now(ZoneInfo(settings.timezone)).date()
            selected_date = request.app.state.query_service.latest_production_date(
                today, force=refresh
            )
        if selected_date is None:
            return {
                "success": True,
                "data": {
                    "report_date": None,
                    "records": [],
                    "summary": summarize_equipment([], "mock"),
                    "data_updated_at": None,
                    "energy_notice": "气耗和电耗为模拟数据，仅用于联调。",
                },
                "request_id": request.state.request_id,
            }

        equipment_data = request.app.state.query_service.fetch_equipment_data(
            ReportFilter(report_date=selected_date), force=refresh
        )
        records = sort_equipment_records(mock_energy(equipment_data.records))
    except SqlServerConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("equipment report query failed")
        raise HTTPException(status_code=500, detail="设备报表查询失败。") from exc

    return {
        "success": True,
        "data": {
            "report_date": selected_date.isoformat(),
            "records": to_primitive(records[: settings.report_max_detail_rows]),
            "summary": to_primitive(summarize_equipment(records, "mock")),
            "data_updated_at": to_primitive(equipment_data.data_updated_at),
            "energy_notice": "气耗和电耗为系统生成的稳定模拟数据，仅用于联调，不可用于结算或考核。",
            "truncated": len(records) > settings.report_max_detail_rows,
        },
        "request_id": request.state.request_id,
    }
