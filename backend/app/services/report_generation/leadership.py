"""Leadership report content preparation using the pipeline's existing dependencies."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.config import Settings
from app.models.domain import AnalysisResult, LeadershipReportResult, to_primitive
from app.services.chart_service import ChartService
from app.services.leadership_report_service import LeadershipReportService
from app.services.range_report_service import RangeReportService, previous_equal_range
from app.services.report_analysis_service import ReportAnalysisService
from app.services.report_generation.common import (
    PreparedReport,
    _chart_type,
    _is_range_job,
    _job_period,
    _recent_twelve_month_period,
    report_meta,
)


def _leadership_data(
    job: dict, period_start: date, period_end: date, *,
    leadership_service: LeadershipReportService, range_service: RangeReportService
) -> LeadershipReportResult:
    """Adapt the legacy month query to the common period report shape."""
    if _is_range_job(job):
        primary = range_service.leadership(period_start, period_end)
        _, summary_end = _recent_twelve_month_period(job, period_end)
        monthly_leadership = leadership_service.fetch(summary_end)
    else:
        # Keep the aggregated month query and its natural-month comparisons.
        monthly_leadership = leadership_service.fetch(period_start)
        primary = monthly_leadership
    return replace(
        primary,
        period_start=period_start,
        period_end=period_end,
        monthly_records=monthly_leadership.monthly_records,
        twelve_month_total=monthly_leadership.twelve_month_total,
    )


def prepare_report(
    job: dict, *,
    settings: Settings,
    leadership_service: LeadershipReportService,
    range_service: RangeReportService,
    chart_service: ChartService,
    analysis_service: ReportAnalysisService,
    ensure_active: Callable[[], None],
    update_status: Callable[[str, str], None],
    analyze: Callable[..., AnalysisResult],
) -> PreparedReport:
    ensure_active()
    timezone = ZoneInfo(settings.timezone)
    period_start, period_end = _job_period(job)
    leadership = _leadership_data(job, period_start, period_end, leadership_service=leadership_service, range_service=range_service)
    chart_type = _chart_type(job)
    charts = chart_service.build_leadership(leadership, chart_type)
    ensure_active()

    update_status("calculating", "正在计算月度汇总")
    update_status("analyzing", "正在生成智能分析")

    def analyze_leadership() -> AnalysisResult:
        analysis_report = leadership
        if _is_range_job(job):
            comparison_start, comparison_end = previous_equal_range(period_start, period_end)
            comparison = range_service.leadership(comparison_start, comparison_end)
            analysis_report = replace(
                analysis_report,
                comparison_total=comparison.current_month_total,
                comparison_start=comparison_start,
                comparison_end=comparison_end,
            )
        else:
            # Preserve the legacy model's monthly label and cache identity.
            analysis_report = replace(leadership, period_start=None, period_end=None)
        return analysis_service.analyze_leadership(
            analysis_report, monthly_records=leadership.monthly_records,
            cache_filters=dict(zip(('summary_period_start', 'summary_period_end'),
                                   (day.isoformat() for day in _recent_twelve_month_period(job, period_end)))),
        )

    cache_report = leadership if _is_range_job(job) else replace(leadership, period_start=None, period_end=None)
    analysis = analyze(analyze_leadership, cached=lambda: analysis_service.cached_analysis('leadership', cache_report,
        monthly_records=leadership.monthly_records,
        cache_filters=dict(zip(('summary_period_start', 'summary_period_end'),
                               (day.isoformat() for day in _recent_twelve_month_period(job, period_end))))))
    generated_at = datetime.now(timezone)
    report = {
        "meta": report_meta(
            settings=settings,
            job=job,
            report_type="leadership",
            generated_at=generated_at,
            data_updated_at=None,
        ),
        "filters": {
            "report_date": period_start.isoformat(),
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "project_code": None,
            "ship_no": None,
        },
        "units": {
            "length": "米",
            "work_time": "HH:mm:ss",
            "gas": "数据库原始单位",
            "electricity": "数据库原始单位",
        },
        "leadership": to_primitive(leadership),
        "charts": charts,
        "visualization": {"chart_type": chart_type},
        "analysis": to_primitive(analysis),
    }
    return PreparedReport(
        report=report,
        generated_at=generated_at,
        task_count=leadership.current_month_total.task_count,
        analysis_status=analysis.status,
    )
