from __future__ import annotations

import logging
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.api.auth import require_admin
from app.database.sqlserver import SqlServerConfigurationError
from app.models.domain import ReportFilter, to_primitive
from app.services.equipment_service import mock_energy, sort_equipment_records


logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/v1/analysis",
    tags=["report-analysis"],
    dependencies=[Depends(require_admin)],
)


class AnalysisRequest(BaseModel):
    report_type: Literal["leadership", "workshop", "equipment"]
    report_date: date
    force: bool = False


@router.post("")
def analyze_report(payload: AnalysisRequest, request: Request) -> dict:
    try:
        service = request.app.state.report_analysis_service
        report_date = payload.report_date.isoformat()
        if payload.report_type == "leadership":
            report = request.app.state.leadership_report_service.fetch(
                payload.report_date.replace(day=1)
            )
            analysis = service.analyze_leadership(report, force=payload.force)
        elif payload.report_type == "workshop":
            report, records = request.app.state.workshop.get_daily_report_records(
                report_date
            )
            if report is None:
                raise HTTPException(status_code=404, detail="当日车间报表尚未生成。")
            analysis = service.analyze_workshop(
                report_date, records, force=payload.force
            )
        else:
            bundle = request.app.state.query_service.fetch_report_data(
                ReportFilter(report_date=payload.report_date)
            )
            records = sort_equipment_records(mock_energy(bundle.equipment_records))
            analysis = service.analyze_equipment(
                report_date,
                records,
                energy_data_mode="mock",
                force=payload.force,
            )
    except HTTPException:
        raise
    except SqlServerConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("report analysis failed")
        raise HTTPException(status_code=500, detail="智能分析生成失败。") from exc

    return {
        "success": True,
        "data": to_primitive(analysis),
        "request_id": request.state.request_id,
    }
