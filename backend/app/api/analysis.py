from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.devices import device_state
from app.api.auth import require_admin
from app.database.sqlserver import SqlServerConfigurationError
from app.models.domain import ReportFilter, to_primitive
from app.services.energy_period_service import restore_task_energy_records
from app.services.range_report_service import previous_equal_range, validate_report_range
from app.services.report_analysis_service import (
    AnalysisDataVersionMismatch,
    AnalysisUnavailableError,
    analysis_period_label as _period_label,
    equipment_analysis_source_version,
)
from app.services.request_cancellation import SqlRequestCancelled
from app.services.analysis_execution import analysis_operation
from app.services.report_deadline import ReportOperationTimeout


logger = logging.getLogger(__name__)
ANALYSIS_DATA_VERSION_PATTERN = r"^[0-9a-f]{64}\.[0-9a-f]{64}$"
router = APIRouter(
    prefix="/api/v1/analysis",
    tags=["report-analysis"],
    dependencies=[Depends(require_admin)],
)


def _year_ago(value: date) -> date:
    try:
        return value.replace(year=value.year - 1)
    except ValueError:
        return value.replace(year=value.year - 1, day=28)


def _summary_period(payload: "AnalysisRequest", fallback_end: date) -> tuple[date, date]:
    end = payload.summary_period_end or fallback_end
    if payload.summary_period_start is not None:
        return payload.summary_period_start, end
    absolute_month = end.year * 12 + end.month - 1 - 11
    return date(absolute_month // 12, absolute_month % 12 + 1, 1), end


class AnalysisRequest(BaseModel):
    report_type: Literal["leadership", "workshop", "equipment"]
    report_date: date
    period_start: date | None = None
    period_end: date | None = None
    summary_period_start: date | None = None
    summary_period_end: date | None = None
    data_version: str | None = Field(
        default=None,
        pattern=ANALYSIS_DATA_VERSION_PATTERN,
    )
    force: bool = False


router.dependencies.append(Depends(device_state))


def _is_default_view(payload: AnalysisRequest) -> bool:
    if payload.report_type == "workshop":
        return payload.period_start is None or payload.period_start == payload.period_end == payload.report_date
    if payload.period_start is not None:
        return False
    summary_start, summary_end = _summary_period(payload, payload.report_date)
    absolute_month = summary_end.year * 12 + summary_end.month - 1 - 11
    if summary_start != date(absolute_month // 12, absolute_month % 12 + 1, 1):
        return False
    if payload.report_type == "leadership":
        return summary_end.replace(day=1) == payload.report_date.replace(day=1)
    return summary_end == payload.report_date


@router.get("/latest")
def get_latest_analysis(
    report_type: Literal["leadership", "workshop", "equipment"],
    report_date: date,
    request: Request,
) -> dict:
    analysis = device_state(request).report_analysis_service.latest_default_analysis(report_type, report_date)
    return {
        "success": True,
        "data": to_primitive(analysis),
        "request_id": request.state.request_id,
    }


@router.get("/version")
def get_analysis_version(
    report_type: Literal["leadership", "workshop", "equipment"],
    report_date: date,
    request: Request,
) -> dict:
    lookup_date = (
        report_date.strftime("%Y-%m")
        if report_type == "leadership"
        else report_date.isoformat()
    )
    version = device_state(request).metadata.get_analysis_source_version(
        report_type=report_type,
        report_date=lookup_date,
    )
    if version is not None and not re.fullmatch(
        ANALYSIS_DATA_VERSION_PATTERN, version["data_version"]
    ):
        version = None
    return {
        "success": True,
        "data": version,
        "request_id": request.state.request_id,
    }


@router.post("")
def analyze_report(payload: AnalysisRequest, request: Request) -> dict:
    settings = device_state(request).report_analysis_service.settings
    try:
        with analysis_operation(settings, payload.report_type, payload.report_date.isoformat(),
                                request_id=request.state.request_id):
            return _analyze_report(payload, request)
    except ReportOperationTimeout as exc:
        raise HTTPException(status_code=503, detail="智能分析超过总等待时限，请稍后重试。") from exc


def _analyze_report(payload: AnalysisRequest, request: Request) -> dict:
    try:
        if (payload.period_start is None) != (payload.period_end is None):
            raise HTTPException(status_code=422, detail="开始日期和结束日期必须同时提供。")
        if (payload.summary_period_start is None) != (payload.summary_period_end is None):
            raise HTTPException(status_code=422, detail="汇总开始日期和结束日期必须同时提供。")
        if (
            payload.summary_period_start is not None
            and payload.summary_period_end is not None
            and payload.summary_period_end < payload.summary_period_start
        ):
            raise HTTPException(status_code=422, detail="汇总结束日期不能早于开始日期。")
        if payload.period_start is not None and payload.period_end is not None:
            try:
                validate_report_range(payload.period_start, payload.period_end)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        service = device_state(request).report_analysis_service
        report_date = payload.report_date.isoformat()
        if payload.report_type == "leadership":
            if payload.period_start is not None and payload.period_end is not None:
                report = device_state(request).range_report_service.leadership(
                    payload.period_start, payload.period_end
                )
            else:
                report = device_state(request).leadership_report_service.fetch(
                    payload.report_date.replace(day=1)
                )
                if report.source_mode == "sqlserver" and report.snapshot.data_as_of is None:
                    # Analysis must never be generated from the empty placeholder used
                    # while the first page snapshot is warming in the background.
                    report = device_state(request).leadership_report_service.fetch(
                        payload.report_date.replace(day=1), force=True
                    )
            summary_start, summary_end = _summary_period(
                payload, payload.period_end or payload.report_date
            )
            monthly_report = device_state(request).leadership_report_service.fetch(
                summary_end.replace(day=1)
            )
            summary_month_start = summary_start.replace(day=1)
            monthly_records = [
                row
                for row in monthly_report.monthly_records
                if summary_month_start <= row.period_start <= summary_end
            ]
            analysis = service.analyze_leadership(
                report,
                monthly_records=monthly_records,
                expected_data_version=payload.data_version,
                force=payload.force,
                publish_latest=_is_default_view(payload),
            )
        elif payload.report_type == "workshop":
            if payload.period_start is not None and payload.period_end is not None:
                period_start = payload.period_start
                period_end = payload.period_end
                _, records = device_state(request).range_report_service.workshop_records(
                    period_start, period_end
                )
                comparison_start, comparison_end = previous_equal_range(
                    period_start, period_end
                )
                _, previous_records = device_state(request).range_report_service.workshop_records(
                    comparison_start, comparison_end
                )
                year_ago_start = _year_ago(period_start)
                year_ago_end = _year_ago(period_end)
                _, year_ago_records = device_state(request).range_report_service.workshop_records(
                    year_ago_start, year_ago_end
                )
                analysis = service.analyze_workshop(
                    _period_label(period_start, period_end),
                    records,
                    comparison_records=previous_records,
                    comparison_date=_period_label(comparison_start, comparison_end),
                    year_ago_records=year_ago_records,
                    year_ago_date=_period_label(year_ago_start, year_ago_end),
                    expected_data_version=payload.data_version,
                    force=payload.force,
                    publish_latest=_is_default_view(payload),
                )
            else:
                report, records = device_state(request).workshop.get_daily_report_records(
                    report_date
                )
                if report is None:
                    raise HTTPException(status_code=404, detail="当日车间报表尚未生成。")
                comparison_day = payload.report_date - timedelta(days=1)
                previous_report, previous_records = (
                    device_state(request).workshop.get_daily_report_records(
                        comparison_day.isoformat()
                    )
                )
                year_ago_day = _year_ago(payload.report_date)
                year_ago_report, year_ago_records = (
                    device_state(request).workshop.get_daily_report_records(
                        year_ago_day.isoformat()
                    )
                )
                analysis = service.analyze_workshop(
                    report_date,
                    records,
                    comparison_records=previous_records if previous_report else None,
                    comparison_date=comparison_day.isoformat() if previous_report else None,
                    year_ago_records=year_ago_records if year_ago_report else None,
                    year_ago_date=year_ago_day.isoformat() if year_ago_report else None,
                    expected_data_version=payload.data_version,
                    force=payload.force,
                    publish_latest=_is_default_view(payload),
                )
        else:
            period_start = payload.period_start or payload.report_date
            period_end = payload.period_end or payload.report_date
            range_mode = payload.period_start is not None and payload.period_end is not None
            if range_mode:
                range_data = device_state(request).range_report_service.equipment(
                    period_start, period_end
                )
                task_report = range_data["task_report"]
                equipment_data = device_state(request).query_service.fetch_equipment_data(
                    ReportFilter(
                        report_date=period_start,
                        period_start=period_start,
                        period_end=period_end,
                    )
                )
                records = restore_task_energy_records(
                    equipment_data.records,
                    task_report,
                )
                comparison_start, comparison_end = previous_equal_range(
                    period_start, period_end
                )
                previous_data = device_state(request).query_service.fetch_equipment_data(
                    ReportFilter(
                        report_date=comparison_start,
                        period_start=comparison_start,
                        period_end=comparison_end,
                    ),
                )
                year_ago_start = _year_ago(period_start)
                year_ago_end = _year_ago(period_end)
                year_ago_data = device_state(request).query_service.fetch_equipment_data(
                    ReportFilter(
                        report_date=year_ago_start,
                        period_start=year_ago_start,
                        period_end=year_ago_end,
                    ),
                )
                comparison_label = _period_label(comparison_start, comparison_end)
                year_ago_label = _period_label(year_ago_start, year_ago_end)
            else:
                equipment_data = device_state(request).query_service.fetch_equipment_data(
                    ReportFilter(report_date=payload.report_date)
                )
                task_report = device_state(request).energy_period_service.get_task_day(
                    payload.report_date,
                    equipment_data.records,
                )
                records = restore_task_energy_records(
                    equipment_data.records,
                    task_report,
                )
                comparison_day = payload.report_date - timedelta(days=1)
                previous_data = device_state(request).query_service.fetch_equipment_data(
                    ReportFilter(report_date=comparison_day)
                )
                year_ago_day = _year_ago(payload.report_date)
                year_ago_data = device_state(request).query_service.fetch_equipment_data(
                    ReportFilter(report_date=year_ago_day)
                )
                comparison_label = comparison_day.isoformat()
                year_ago_label = year_ago_day.isoformat()

            previous_records = previous_data.records
            year_ago_records = year_ago_data.records
            previous_records = device_state(request).energy_period_service.filter_task_records(
                previous_records
            )
            year_ago_records = device_state(request).energy_period_service.filter_task_records(
                year_ago_records
            )
            summary_start, summary_end = _summary_period(payload, period_end)
            energy_summary = device_state(request).energy_period_service.get_monthly_range(
                summary_start, summary_end
            )
            analysis = service.analyze_equipment(
                _period_label(period_start, period_end),
                records,
                energy_data_mode=(
                    task_report["summary"].get("energy_data_mode") or "unavailable"
                ),
                energy_units=task_report["units"],
                energy_notice=task_report.get("energy_notice") or "",
                energy_summary_rows=energy_summary["rows"],
                comparison_records=previous_records,
                comparison_date=comparison_label,
                year_ago_records=year_ago_records,
                year_ago_date=year_ago_label,
                source_data_version=equipment_analysis_source_version(task_report),
                expected_data_version=payload.data_version,
                force=payload.force,
                publish_latest=_is_default_view(payload),
            )
    except (HTTPException, SqlRequestCancelled, ReportOperationTimeout):
        raise
    except AnalysisDataVersionMismatch as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AnalysisUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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
