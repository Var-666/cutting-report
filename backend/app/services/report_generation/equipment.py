"""Equipment report content preparation using the pipeline's existing dependencies."""
from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.config import Settings
from app.models.domain import AnalysisResult, EquipmentRecord, ReportFilter, to_primitive
from app.services.chart_service import ChartService
from app.services.energy_period_service import EnergyPeriodService, restore_task_energy_records
from app.services.query_service import QueryService
from app.services.range_report_service import RangeReportService, previous_equal_range
from app.services.report_analysis_service import ReportAnalysisService
from app.services.report_generation.common import (
    PreparedReport,
    _chart_type,
    _is_range_job,
    _job_parameters,
    _job_period,
    _period_label,
    _recent_twelve_month_period,
    _year_ago,
    report_meta,
)


def analyze_legacy_daily(
    report_date: date, records: list[EquipmentRecord], *,
    comparison_date: date, energy_data_mode: str, energy_units: dict[str, str], energy_notice: str,
    query_service: QueryService, analysis_service: ReportAnalysisService,
) -> AnalysisResult:
    previous_bundle = query_service.fetch_report_data(
        ReportFilter(report_date=comparison_date)
    )
    # Comparisons currently concern task counts, not energy: don't make
    # needless historical API calls or synthesize comparison energy.
    previous_equipment = previous_bundle.equipment_records
    year_ago_date = _year_ago(report_date)
    year_ago_bundle = query_service.fetch_report_data(
        ReportFilter(report_date=year_ago_date)
    )
    year_ago_equipment = year_ago_bundle.equipment_records
    return analysis_service.analyze_equipment(
        report_date.isoformat(),
        records,
        energy_data_mode=energy_data_mode,
        energy_units=energy_units,
        energy_notice=energy_notice,
        comparison_records=previous_equipment,
        comparison_date=comparison_date.isoformat(),
        year_ago_records=year_ago_equipment,
        year_ago_date=year_ago_date.isoformat(),
    )


def prepare_report(
    job: dict, *,
    settings: Settings,
    range_service: RangeReportService,
    query_service: QueryService,
    energy_period_service: EnergyPeriodService,
    chart_service: ChartService,
    analysis_service: ReportAnalysisService,
    ensure_active: Callable[[], None],
    update_status: Callable[[str, str], None],
    analyze: Callable[[Callable[[], AnalysisResult]], AnalysisResult],
) -> PreparedReport:
    ensure_active()
    timezone = ZoneInfo(settings.timezone)
    period_start, period_end = _job_period(job)
    legacy_day = not _is_range_job(job)
    report_month = None
    if legacy_day:
        report_month = str(
            _job_parameters(job).get("report_month") or period_start.isoformat()[:7]
        )
        try:
            month_date = date.fromisoformat(report_month + "-01")
            if not 1900 <= month_date.year <= 2100:
                raise ValueError()
        except ValueError:
            raise ValueError("设备报表导出月份无效") from None

    update_status("calculating", "正在计算任务与能耗")
    equipment, task_report = range_service.equipment_tasks(period_start, period_end)
    if legacy_day:
        # The old daily format includes daily totals for one selected month.
        month_report = energy_period_service.get_month(report_month)
    else:
        summary_start, summary_end = _recent_twelve_month_period(job, period_end)
        month_report = energy_period_service.get_monthly_range(summary_start, summary_end)
    chart_type = _chart_type(job)
    charts = chart_service.build_energy_period(task_report, month_report, chart_type)
    summary = task_report["summary"]
    update_status("analyzing", "正在生成智能分析")

    def analyze_equipment() -> AnalysisResult:
        calculated_records = restore_task_energy_records(equipment.records, task_report)
        comparison_start, comparison_end = previous_equal_range(period_start, period_end)
        comparison_records = query_service.fetch_equipment_data(
            ReportFilter(
                report_date=comparison_start,
                period_start=comparison_start,
                period_end=comparison_end,
            )
        ).records
        year_ago_date = None
        year_ago_records = None
        if legacy_day:
            comparison_records = energy_period_service.filter_task_records(comparison_records)
            year_ago_date = _year_ago(period_start)
            year_ago_records = energy_period_service.filter_task_records(
                query_service.fetch_equipment_data(
                    ReportFilter(report_date=year_ago_date)
                ).records
            )
        return analysis_service.analyze_equipment(
            _period_label(period_start, period_end),
            calculated_records,
            energy_data_mode=summary.get("energy_data_mode") or "unavailable",
            energy_units=task_report["units"],
            energy_notice=task_report.get("energy_notice") or "",
            energy_summary_rows=month_report["rows"],
            comparison_records=comparison_records,
            comparison_date=_period_label(comparison_start, comparison_end),
            year_ago_records=year_ago_records,
            year_ago_date=year_ago_date.isoformat() if year_ago_date else None,
        )

    analysis = analyze(analyze_equipment)
    ensure_active()
    generated_at = datetime.now(timezone)
    filters = {
        "report_date": period_start.isoformat(),
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "project_code": None,
        "ship_no": None,
    }
    if legacy_day:
        filters["report_month"] = report_month
    report = {
        "meta": report_meta(
            settings=settings,
            job=job,
            report_type="equipment",
            generated_at=generated_at,
            data_updated_at=equipment.data_updated_at,
        ),
        "filters": filters,
        "units": task_report["units"],
        "energy_notice": task_report.get("energy_notice") or "",
        "equipment_summary": summary,
        "equipment_records": task_report.get("records") or [],
        "energy_month": month_report,
        "charts": charts,
        "visualization": {"chart_type": chart_type},
        "analysis": to_primitive(analysis),
    }
    return PreparedReport(
        report=report,
        generated_at=generated_at,
        task_count=summary["task_count"],
        analysis_status=analysis.status,
    )
