"""Workshop report content preparation using the pipeline's existing dependencies."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import Settings
from app.database.workshop_reports import WorkshopReportRepository
from app.models.domain import (
    AnalysisResult,
    QueryBundle,
    ReportFilter,
    SummaryBaseRecord,
    WorkshopRecord,
    to_primitive,
)
from app.services.chart_service import ChartService
from app.services.equipment_service import summarize_equipment
from app.services.query_service import QueryService
from app.services.range_report_service import RangeReportService, previous_equal_range
from app.services.report_analysis_service import ReportAnalysisService
from app.services.report_generation.common import (
    PreparedReport,
    _chart_type,
    _is_range_job,
    _job_period,
    _period_label,
    _year_ago,
    report_meta,
)
from app.services.report_generation.equipment import analyze_legacy_daily
from app.services.statistics_service import StatisticsService, build_worker_work_summaries
from app.services.workshop_sorting import sort_workshop_by_recency


def _workshop_bundle(records: list[WorkshopRecord], data_updated_at: datetime | None) -> QueryBundle:
    return QueryBundle(
        summary_records=[
            SummaryBaseRecord(
                task_id=row.task_id, project_code=row.project_code, ship_no=row.ship_no,
                segment_name=row.segment_name, program_no=row.program_no,
                cut_length=row.cut_length, groove_length=row.groove_length,
                work_time=row.work_time, production_time=row.production_time, production_date=row.production_date,
            )
            for row in records
        ],
        workshop_records=records, equipment_records=[], data_updated_at=data_updated_at,
    )


def prepare_report(
    job: dict, report_type: str, *,
    settings: Settings,
    workshop_repository: WorkshopReportRepository,
    range_service: RangeReportService,
    query_service: QueryService,
    statistics_service: StatisticsService,
    chart_service: ChartService,
    analysis_service: ReportAnalysisService,
    ensure_active: Callable[[], None],
    update_status: Callable[[str, str], None],
    analyze: Callable[..., AnalysisResult],
) -> PreparedReport:
    ensure_active()
    timezone = ZoneInfo(settings.timezone)
    period_start, period_end = _job_period(job)
    range_job = _is_range_job(job)
    filters = ReportFilter(
        report_date=period_start,
        project_code=job["project_code"],
        ship_no=job["ship_no"],
        period_start=period_start if range_job else None,
        period_end=period_end if range_job else None,
    )
    if report_type == "workshop":
        if range_job:
            source_bundle, workshop_records = range_service.workshop_records(
                period_start,
                period_end,
                project_code=filters.project_code,
                ship_no=filters.ship_no,
            )
            workshop_records = sort_workshop_by_recency(
                workshop_records, settings.timezone
            )
            bundle = _workshop_bundle(workshop_records, source_bundle.data_updated_at)
        else:
            workshop_report, workshop_records = (
                workshop_repository.get_daily_report_records(
                    filters.report_date.isoformat()
                )
            )
            workshop_records = sort_workshop_by_recency(
                workshop_records, settings.timezone
            )
            if (
                workshop_report is None
                or workshop_report.get("scope_machine_no")
                != settings.report_machine_no
            ):
                raise ValueError("请先生成当日车间报表，再进行导出")
            workshop_records = query_service.enrich_ship_names(workshop_records)
            workshop_records = [row for row in workshop_records
                if (not filters.project_code or row.project_code == filters.project_code)
                and (not filters.ship_no or row.ship_no == filters.ship_no)]
            bundle = _workshop_bundle(workshop_records, datetime.fromisoformat(workshop_report['generated_at']))
    else:
        bundle = query_service.fetch_report_data(filters)

    energy_data_mode = "unavailable"
    energy_notice = ""
    energy_units = {"gas": "单位待确认", "electricity": "单位待确认"}

    ensure_active()
    update_status("calculating", "正在计算统计指标")
    statistics = statistics_service.calculate(bundle.summary_records)
    chart_type = _chart_type(job)
    charts = chart_service.build(
        statistics.valid_summary_records,
        bundle.equipment_records,
        chart_type,
    )
    equipment_summary = summarize_equipment(
        bundle.equipment_records, energy_data_mode
    )

    update_status("analyzing", "正在生成智能分析")

    def analyze_daily() -> AnalysisResult:
        comparison_date = filters.report_date - timedelta(days=1)
        if report_type == "workshop":
            if range_job:
                comparison_start, comparison_end = previous_equal_range(
                    period_start, period_end
                )
                _, previous_records = range_service.workshop_records(
                    comparison_start,
                    comparison_end,
                    project_code=filters.project_code,
                    ship_no=filters.ship_no,
                )
                year_ago_start = _year_ago(period_start)
                year_ago_end = _year_ago(period_end)
                _, year_ago_records = range_service.workshop_records(
                    year_ago_start,
                    year_ago_end,
                    project_code=filters.project_code,
                    ship_no=filters.ship_no,
                )
                return analysis_service.analyze_workshop(
                    _period_label(period_start, period_end),
                    bundle.workshop_records,
                    cache_filters={'project_code': filters.project_code, 'ship_no': filters.ship_no},
                    comparison_records=previous_records,
                    comparison_date=_period_label(comparison_start, comparison_end),
                    year_ago_records=year_ago_records,
                    year_ago_date=_period_label(year_ago_start, year_ago_end),
                )
            else:
                previous_report, previous_records = (
                    workshop_repository.get_daily_report_records(
                        comparison_date.isoformat()
                    )
                )
                year_ago_date = _year_ago(filters.report_date)
                year_ago_report, year_ago_records = (
                    workshop_repository.get_daily_report_records(
                        year_ago_date.isoformat()
                    )
                )
                previous_records = query_service.enrich_ship_names(previous_records)
                year_ago_records = query_service.enrich_ship_names(year_ago_records)
                return analysis_service.analyze_workshop(
                    filters.report_date.isoformat(),
                    bundle.workshop_records,
                    cache_filters={'project_code': filters.project_code, 'ship_no': filters.ship_no},
                    comparison_records=(
                        previous_records
                        if previous_report
                        and previous_report.get("scope_machine_no")
                        == settings.report_machine_no
                        else None
                    ),
                    comparison_date=(
                        comparison_date.isoformat()
                        if previous_report
                        and previous_report.get("scope_machine_no")
                        == settings.report_machine_no
                        else None
                    ),
                    year_ago_records=(
                        year_ago_records
                        if year_ago_report
                        and year_ago_report.get("scope_machine_no")
                        == settings.report_machine_no
                        else None
                    ),
                    year_ago_date=(
                        year_ago_date.isoformat()
                        if year_ago_report
                        and year_ago_report.get("scope_machine_no")
                        == settings.report_machine_no
                        else None
                    ),
                )
        else:
            return analyze_legacy_daily(
                filters.report_date, bundle.equipment_records,
                comparison_date=comparison_date, energy_data_mode=energy_data_mode,
                energy_units=energy_units, energy_notice=energy_notice,
                query_service=query_service, analysis_service=analysis_service,
            )

    cache_kind = 'workshop' if report_type == 'workshop' else 'equipment'
    cache_period = _period_label(period_start, period_end) if range_job else filters.report_date.isoformat()
    analysis = analyze(analyze_daily, cached=lambda: analysis_service.cached_analysis(cache_kind, cache_period,
        cache_filters={'project_code': filters.project_code, 'ship_no': filters.ship_no}, energy_units=energy_units))
    ensure_active()
    generated_at = datetime.now(timezone)
    report = {
        "meta": report_meta(
            settings=settings,
            job=job,
            report_type=report_type,
            generated_at=generated_at,
            data_updated_at=bundle.data_updated_at,
        ),
        "filters": to_primitive(filters),
        "units": {
            "length": "米",
            "work_time": "HH:mm:ss",
            **energy_units,
        },
        "energy_notice": energy_notice,
        "summary": to_primitive(statistics.summary),
        "equipment_summary": to_primitive(equipment_summary),
        "data_quality": to_primitive(statistics.data_quality),
        "attention_items": to_primitive(statistics.attention_items),
        "workshop_records": to_primitive(
            bundle.workshop_records[: settings.report_max_detail_rows]
        ),
        "worker_summaries": to_primitive(
            build_worker_work_summaries(bundle.workshop_records)
            if report_type == "workshop"
            else []
        ),
        "equipment_records": to_primitive(
            bundle.equipment_records[: settings.report_max_detail_rows]
        ),
        "charts": charts,
        "visualization": {"chart_type": chart_type},
        "analysis": to_primitive(analysis),
    }
    return PreparedReport(
        report=report,
        generated_at=generated_at,
        task_count=(
            statistics.summary.task_count
            if report_type == "workshop"
            else equipment_summary["task_count"]
        ),
        analysis_status=analysis.status,
    )
