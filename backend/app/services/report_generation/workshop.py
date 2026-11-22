"""Workshop report content preparation using the pipeline's existing dependencies."""
from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import Settings
from app.modules.cutting.workshop_repository import WorkshopReportRepository
from app.models.domain import (
    AnalysisResult,
    QueryBundle,
    ReportFilter,
    SummaryBaseRecord,
    WorkshopRecord,
    to_primitive,
)
from app.models.report_contracts import WorkshopAnalysisScope
from app.services.chart_service import ChartService
from app.modules.cutting.equipment import summarize_equipment
from app.modules.cutting.service import QueryService
from app.modules.cutting.range_reports import RangeReportService, filter_workshop_records, previous_equal_range
from app.services.report_analysis_service import ReportAnalysisService
from app.services.report_closing_comparison import Totals, business_day_window
from app.services.report_generation.common import (
    report_analysis_payload,
    PreparedReport,
    _chart_type,
    _is_range_job,
    _job_parameters,
    _job_period,
    _period_label,
    _year_ago,
    report_meta,
    report_reference_at,
)
from app.services.report_generation.equipment import analyze_legacy_daily
from app.modules.cutting.statistics import StatisticsService, build_worker_work_summaries
from app.modules.cutting.workshop_sorting import sort_workshop_by_recency


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
    parameters = _job_parameters(job)
    detail_filters = WorkshopAnalysisScope(
        task_id=parameters.get('task_id'), confirmation_status=parameters.get('confirmation_status', 'all'),
    ).model_dump(exclude_defaults=True) if report_type == 'workshop' and range_job else {}
    cache_filters = {'project_code': filters.project_code, 'ship_no': filters.ship_no, **detail_filters}
    workshop_window = business_day_window(period_start, period_end, timezone) if report_type == "workshop" else None

    def load_workshop(start: date, end: date, *, force: bool = False) -> tuple[QueryBundle, list[WorkshopRecord]]:
        source, records = range_service.workshop_records(
            start, end, project_code=filters.project_code, ship_no=filters.ship_no,
            force=force, business_days=True,
        )
        return source, filter_workshop_records(records, **cache_filters)

    if workshop_window is not None:
        if not range_job:
            workshop_report = workshop_repository.get_daily_report(filters.report_date.isoformat())
            if not workshop_report or workshop_report.get("scope_machine_no") != settings.report_machine_no:
                raise ValueError("请先生成当日车间报表，再进行导出")
        source, records = load_workshop(period_start, period_end, force=True)
        records = sort_workshop_by_recency(records, settings.timezone)
        bundle = _workshop_bundle(records, source.data_updated_at)
    else:
        bundle = query_service.fetch_report_data(filters)

    energy_data_mode = "unavailable"
    energy_notice = ""
    energy_units = {"gas": "单位待确认", "electricity": "单位待确认"}

    ensure_active()
    update_status("calculating", "正在计算统计指标")
    statistics = statistics_service.calculate(bundle.summary_records)

    def business_day_totals(day: date) -> Totals:
        summary = statistics.summary
        if period_start != period_end:
            summary = statistics_service.calculate(
                [row for row in statistics.valid_summary_records if row.production_date == day]
            ).summary
        return dict(task_count=summary.task_count, cut_length_m=summary.total_cut_length,
                    groove_length_m=summary.total_groove_length, work_time_seconds=summary.total_work_time)

    closing_comparison = [] if detail_filters else range_service.cutting_closing_comparison(
        period_start, period_end, report_reference_at(job, settings.timezone),
        project_code=filters.project_code, ship_no=filters.ship_no,
        business_day_totals=business_day_totals if workshop_window is not None else None,
    )
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
        if workshop_window is not None:
            comparison_start, comparison_end = previous_equal_range(period_start, period_end)
            _, previous_records = load_workshop(comparison_start, comparison_end)
            year_ago_start, year_ago_end = _year_ago(period_start), _year_ago(period_end)
            _, year_ago_records = load_workshop(year_ago_start, year_ago_end)
            return analysis_service.analyze_workshop(
                cache_period,
                bundle.workshop_records,
                cache_filters=cache_filters,
                comparison_records=previous_records,
                comparison_date=_period_label(comparison_start, comparison_end),
                year_ago_records=year_ago_records,
                year_ago_date=_period_label(year_ago_start, year_ago_end),
                business_window=workshop_window,
            )
        return analyze_legacy_daily(
            filters.report_date, bundle.equipment_records,
            comparison_date=filters.report_date - timedelta(days=1), energy_data_mode=energy_data_mode,
            energy_units=energy_units, energy_notice=energy_notice,
            query_service=query_service, analysis_service=analysis_service,
        )

    cache_kind = 'workshop' if workshop_window is not None else 'equipment'
    cache_period = _period_label(period_start, period_end)
    analysis = analyze(analyze_daily, cached=lambda: analysis_service.cached_analysis(cache_kind, cache_period,
        cache_filters=cache_filters, energy_units=energy_units, business_window=workshop_window))
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
        "filters": {**to_primitive(filters), **detail_filters},
        "units": {
            "length": "米",
            "work_time": "HH:mm:ss",
            **energy_units,
        },
        "energy_notice": energy_notice,
        "summary": to_primitive(statistics.summary),
        "closing_comparison": closing_comparison,
        "workshop_window": workshop_window,
        "workshop_window_open": workshop_window is not None and generated_at < workshop_window[1],
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
        "analysis": report_analysis_payload(analysis),
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
