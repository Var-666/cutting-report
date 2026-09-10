from __future__ import annotations

import calendar
import json
from dataclasses import replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import Settings
from app.database.energy import EnergyReportRepository
from app.database.metadata import MetadataRepository
from app.database.definitions import DefinitionRepository
from app.database.workshop import WorkshopRepository
from app.database.unit_of_work import UnitOfWork
from app.models.domain import (
    QueryBundle,
    ReportFilter,
    SummaryBaseRecord,
    to_primitive,
)
from app.services.chart_service import ChartService
from app.services.custom_report_service import CustomReportService
from app.services.equipment_service import summarize_equipment
from app.services.energy_service import EquipmentEnergyService
from app.services.energy_period_service import (
    EnergyPeriodService,
    restore_task_energy_records,
)
from app.services.leadership_report_service import LeadershipReportService
from app.services.query_service import QueryService
from app.services.range_report_service import RangeReportService, previous_equal_range
from app.services.report_analysis_service import ReportAnalysisService
from app.services.report_service import ReportService
from app.services.statistics_service import StatisticsService, build_worker_work_summaries


REPORT_TITLES = {
    "leadership": "切割生产领导月报",
    "workshop": "切割生产车间日报",
    "equipment": "切割生产设备报表",
}


CHART_TYPES = {"bar", "line", "area", "bar_line"}


def _job_parameters(job: dict) -> dict:
    try:
        value = json.loads(job.get("parameters_json") or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _chart_type(job: dict) -> str:
    value = str(_job_parameters(job).get("chart_type") or "bar_line")
    return value if value in CHART_TYPES else "bar_line"


def _year_ago(value: date) -> date:
    try:
        return value.replace(year=value.year - 1)
    except ValueError:
        return value.replace(year=value.year - 1, day=28)


def _job_period(job: dict) -> tuple[date, date]:
    start = date.fromisoformat(job.get("period_start") or job["report_date"])
    end = date.fromisoformat(job.get("period_end") or job.get("period_start") or job["report_date"])
    return start, end


def _is_range_job(job: dict) -> bool:
    return bool(job.get("period_start") and job.get("period_end"))


def _period_label(start: date, end: date) -> str:
    return start.isoformat() if start == end else f"{start.isoformat()} 至 {end.isoformat()}"


class ReportPipeline:
    def __init__(
        self,
        settings: Settings,
        metadata: MetadataRepository,
        definitions: DefinitionRepository | None = None,
    ) -> None:
        self.settings = settings
        self.metadata = metadata
        self.database = metadata.database
        self.definitions = definitions or DefinitionRepository(
            self.database, settings.timezone
        )
        self.definitions.initialize()
        self.query_service = QueryService(settings)
        self.energy_service = EquipmentEnergyService(settings)
        energy_reports = EnergyReportRepository(self.database)
        energy_reports.initialize()
        self.energy_period_service = EnergyPeriodService(
            settings,
            energy_reports,
            client=self.energy_service.client,
            query_service=self.query_service,
        )
        self.leadership_service = LeadershipReportService(settings)
        self.statistics_service = StatisticsService()
        self.chart_service = ChartService(settings.chart_top_n)
        self.analysis_service = ReportAnalysisService(settings, metadata)
        self.report_service = ReportService(settings)
        self.custom_report_service = CustomReportService(settings)
        self.workshop_repository = WorkshopRepository(self.database)
        self.workshop_repository.initialize()
        self.range_service = RangeReportService(
            settings,
            self.query_service,
            self.workshop_repository,
            self.energy_service,
        )

    def run(self, job: dict) -> None:
        report_type = job.get("report_type") or "workshop"
        if report_type == "custom" and job.get("definition_id"):
            self._run_custom(job)
        elif report_type == "leadership":
            self._run_leadership(job)
        elif report_type == "equipment":
            self._run_equipment(job)
        else:
            self._run_daily(job, report_type)

    def _run_custom(self, job: dict) -> None:
        report_id = job["report_id"]
        definition = self.definitions.get_definition(job["definition_id"])
        if not definition or not definition["enabled"] or definition["is_builtin"]:
            raise ValueError("自定义报表定义不存在或尚未启用")
        query_task = self.definitions.get_query_task(definition["query_task_id"])
        template = self.definitions.get_template(definition["template_id"])
        if not query_task or not query_task["enabled"] or not template or not template["enabled"]:
            raise ValueError("报表定义引用的查询任务或模板不可用")

        report_date = date.fromisoformat(job["report_date"])
        parameters = json.loads(job.get("parameters_json") or "{}")
        rows = self.custom_report_service.execute(
            query_task,
            report_date=report_date,
            period_type=definition["period_type"],
            project_code=job.get("project_code"),
            ship_no=job.get("ship_no"),
            parameters=parameters,
        )
        self.metadata.update_status(report_id, "calculating", "正在计算自定义指标")
        custom = self.custom_report_service.build_report_data(
            definition=definition,
            query_task=query_task,
            template=template,
            rows=rows,
        )
        self.metadata.update_status(report_id, "analyzing", "正在应用报表模板")
        generated_at = datetime.now(ZoneInfo(self.settings.timezone))
        report = {
            "meta": self._meta(
                job=job,
                report_type="custom",
                generated_at=generated_at,
                data_updated_at=None,
                title=definition["name"],
            ),
            "definition": definition,
            "query_task": {
                "query_task_id": query_task["query_task_id"],
                "name": query_task["name"],
                "version": query_task["version"],
            },
            "template_meta": {
                "template_id": template["template_id"],
                "name": template["name"],
                "version": template["version"],
            },
            "filters": {
                "report_date": job["report_date"],
                "project_code": job.get("project_code"),
                "ship_no": job.get("ship_no"),
                "parameters": parameters,
            },
            "custom": custom,
        }
        self._render_and_complete(
            job=job,
            report=report,
            generated_at=generated_at,
            task_count=len(rows),
            analysis_status="skipped",
        )

    def _run_leadership(self, job: dict) -> None:
        report_id = job["report_id"]
        timezone = ZoneInfo(self.settings.timezone)
        period_start, period_end = _job_period(job)
        if _is_range_job(job):
            leadership = self.range_service.leadership(period_start, period_end)
            comparison_start, comparison_end = previous_equal_range(
                period_start, period_end
            )
            comparison = self.range_service.leadership(
                comparison_start, comparison_end
            )
            leadership = replace(
                leadership,
                comparison_total=comparison.current_month_total,
                comparison_start=comparison_start,
                comparison_end=comparison_end,
            )
        else:
            leadership = self.leadership_service.fetch(period_start)
        chart_type = _chart_type(job)
        charts = self.chart_service.build_leadership(leadership, chart_type)

        self.metadata.update_status(report_id, "calculating", "正在计算月度汇总")
        self.metadata.update_status(report_id, "analyzing", "正在生成智能分析")
        analysis = self.analysis_service.analyze_leadership(leadership)
        generated_at = datetime.now(timezone)
        report = {
            "meta": self._meta(
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
        self._render_and_complete(
            job=job,
            report=report,
            generated_at=generated_at,
            task_count=leadership.current_month_total.task_count,
            analysis_status=analysis.status,
        )

    def _run_equipment(self, job: dict) -> None:
        report_id = job["report_id"]
        timezone = ZoneInfo(self.settings.timezone)
        report_date, period_end = _job_period(job)
        parameters = _job_parameters(job)
        if _is_range_job(job):
            self.metadata.update_status(report_id, "calculating", "正在计算区间任务与能耗")
            range_data = self.range_service.equipment(report_date, period_end)
            task_report = range_data["task_report"]
            month_report = range_data["period_report"]
            chart_type = _chart_type(job)
            charts = self.chart_service.build_energy_period(
                task_report, month_report, chart_type
            )
            source_records = self.query_service.fetch_equipment_data(
                ReportFilter(
                    report_date=report_date,
                    period_start=report_date,
                    period_end=period_end,
                )
            ).records
            calculated_records = restore_task_energy_records(
                source_records, task_report
            )
            self.metadata.update_status(report_id, "analyzing", "正在生成智能分析")
            comparison_start, comparison_end = previous_equal_range(
                report_date, period_end
            )
            comparison_records = self.query_service.fetch_equipment_data(
                ReportFilter(
                    report_date=comparison_start,
                    period_start=comparison_start,
                    period_end=comparison_end,
                )
            ).records
            analysis = self.analysis_service.analyze_equipment(
                _period_label(report_date, period_end),
                calculated_records,
                energy_data_mode=task_report["summary"].get("energy_data_mode") or "unavailable",
                energy_units=task_report["units"],
                energy_notice=task_report.get("energy_notice") or "",
                comparison_records=comparison_records,
                comparison_date=_period_label(comparison_start, comparison_end),
            )
            generated_at = datetime.now(timezone)
            report = {
                "meta": self._meta(
                    job=job,
                    report_type="equipment",
                    generated_at=generated_at,
                    data_updated_at=(
                        datetime.fromisoformat(task_report["data_updated_at"])
                        if task_report.get("data_updated_at")
                        else None
                    ),
                ),
                "filters": {
                    "report_date": report_date.isoformat(),
                    "period_start": report_date.isoformat(),
                    "period_end": period_end.isoformat(),
                    "project_code": None,
                    "ship_no": None,
                },
                "units": task_report["units"],
                "energy_notice": task_report.get("energy_notice") or "",
                "equipment_summary": task_report["summary"],
                "equipment_records": task_report["records"],
                "energy_month": month_report,
                "charts": charts,
                "visualization": {"chart_type": chart_type},
                "analysis": to_primitive(analysis),
            }
            self._render_and_complete(
                job=job,
                report=report,
                generated_at=generated_at,
                task_count=task_report["summary"]["task_count"],
                analysis_status=analysis.status,
            )
            return
        report_month = str(parameters.get("report_month") or report_date.isoformat()[:7])
        try:
            month_date = date.fromisoformat(report_month + "-01")
            if not 1900 <= month_date.year <= 2100:
                raise ValueError()
        except ValueError:
            raise ValueError("设备报表导出月份无效") from None

        self.metadata.update_status(report_id, "calculating", "正在计算任务与月度能耗")
        equipment = self.query_service.fetch_equipment_data(
            ReportFilter(report_date=report_date)
        )
        task_report = self.energy_period_service.get_task_day(
            report_date,
            equipment.records,
        )
        month_report = self.energy_period_service.get_month(report_month)
        chart_type = _chart_type(job)
        charts = self.chart_service.build_energy_period(
            task_report,
            month_report,
            chart_type,
        )

        calculated_records = restore_task_energy_records(
            equipment.records,
            task_report,
        )

        self.metadata.update_status(report_id, "analyzing", "正在生成智能分析")
        comparison_date = report_date - timedelta(days=1)
        year_ago_date = _year_ago(report_date)
        comparison_records = self.query_service.fetch_equipment_data(
            ReportFilter(report_date=comparison_date)
        ).records
        year_ago_records = self.query_service.fetch_equipment_data(
            ReportFilter(report_date=year_ago_date)
        ).records
        comparison_records = self.energy_period_service.filter_task_records(
            comparison_records
        )
        year_ago_records = self.energy_period_service.filter_task_records(
            year_ago_records
        )
        summary = task_report["summary"]
        analysis = self.analysis_service.analyze_equipment(
            report_date.isoformat(),
            calculated_records,
            energy_data_mode=summary.get("energy_data_mode") or "unavailable",
            energy_units=task_report["units"],
            energy_notice=task_report.get("energy_notice") or "",
            comparison_records=comparison_records,
            comparison_date=comparison_date.isoformat(),
            year_ago_records=year_ago_records,
            year_ago_date=year_ago_date.isoformat(),
        )

        generated_at = datetime.now(timezone)
        report = {
            "meta": self._meta(
                job=job,
                report_type="equipment",
                generated_at=generated_at,
                data_updated_at=equipment.data_updated_at,
            ),
            "filters": {
                "report_date": report_date.isoformat(),
                "report_month": report_month,
                "project_code": None,
                "ship_no": None,
            },
            "units": task_report["units"],
            "energy_notice": task_report.get("energy_notice") or "",
            "equipment_summary": summary,
            "equipment_records": task_report.get("records") or [],
            "energy_month": month_report,
            "charts": charts,
            "visualization": {"chart_type": chart_type},
            "analysis": to_primitive(analysis),
        }
        self._render_and_complete(
            job=job,
            report=report,
            generated_at=generated_at,
            task_count=summary["task_count"],
            analysis_status=analysis.status,
        )

    def _run_daily(self, job: dict, report_type: str) -> None:
        report_id = job["report_id"]
        timezone = ZoneInfo(self.settings.timezone)
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
                source_bundle, workshop_records = self.range_service.workshop_records(
                    period_start,
                    period_end,
                    project_code=filters.project_code,
                    ship_no=filters.ship_no,
                )
                bundle = replace(source_bundle, workshop_records=workshop_records)
            else:
                workshop_report, workshop_records = (
                    self.workshop_repository.get_daily_report_records(
                        filters.report_date.isoformat()
                    )
                )
                if (
                    workshop_report is None
                    or workshop_report.get("scope_machine_no")
                    != self.settings.report_machine_no
                ):
                    raise ValueError("请先生成当日车间报表，再进行导出")
                summaries = [
                    SummaryBaseRecord(
                        task_id=row.task_id,
                        project_code=row.project_code,
                        ship_no=row.ship_no,
                        segment_name=row.segment_name,
                        program_no=row.program_no,
                        cut_length=row.cut_length,
                        groove_length=row.groove_length,
                        work_time=row.work_time,
                        production_time=row.production_time,
                    )
                    for row in workshop_records
                ]
                bundle = QueryBundle(
                    summary_records=summaries,
                    workshop_records=workshop_records,
                    equipment_records=[],
                    data_updated_at=datetime.fromisoformat(
                        workshop_report["generated_at"]
                    ),
                )
        else:
            bundle = self.query_service.fetch_report_data(filters)

        energy_data_mode = "unavailable"
        energy_notice = ""
        energy_units = {"gas": "单位待确认", "electricity": "单位待确认"}
        if report_type == "equipment":
            context_records = None
            if filters.project_code or filters.ship_no:
                # Check overlaps against all tasks, including filtered-out projects.
                context_records = self.query_service.fetch_equipment_data(
                    ReportFilter(report_date=filters.report_date)
                ).records
            energy = self.energy_service.enrich(bundle.equipment_records, context_records=context_records)
            bundle = replace(
                bundle,
                equipment_records=energy.records,
            )
            energy_data_mode, energy_notice, energy_units = energy.mode, energy.notice, energy.units

        self.metadata.update_status(report_id, "calculating", "正在计算统计指标")
        statistics = self.statistics_service.calculate(bundle.summary_records)
        chart_type = _chart_type(job)
        charts = self.chart_service.build(
            statistics.valid_summary_records,
            bundle.equipment_records,
            chart_type,
        )
        equipment_summary = summarize_equipment(
            bundle.equipment_records, energy_data_mode
        )

        self.metadata.update_status(report_id, "analyzing", "正在生成智能分析")
        comparison_date = filters.report_date - timedelta(days=1)
        if report_type == "workshop":
            if range_job:
                comparison_start, comparison_end = previous_equal_range(
                    period_start, period_end
                )
                _, previous_records = self.range_service.workshop_records(
                    comparison_start,
                    comparison_end,
                    project_code=filters.project_code,
                    ship_no=filters.ship_no,
                )
                year_ago_start = _year_ago(period_start)
                year_ago_end = _year_ago(period_end)
                _, year_ago_records = self.range_service.workshop_records(
                    year_ago_start,
                    year_ago_end,
                    project_code=filters.project_code,
                    ship_no=filters.ship_no,
                )
                analysis = self.analysis_service.analyze_workshop(
                    _period_label(period_start, period_end),
                    bundle.workshop_records,
                    comparison_records=previous_records,
                    comparison_date=_period_label(comparison_start, comparison_end),
                    year_ago_records=year_ago_records,
                    year_ago_date=_period_label(year_ago_start, year_ago_end),
                )
            else:
                previous_report, previous_records = (
                    self.workshop_repository.get_daily_report_records(
                        comparison_date.isoformat()
                    )
                )
                year_ago_date = _year_ago(filters.report_date)
                year_ago_report, year_ago_records = (
                    self.workshop_repository.get_daily_report_records(
                        year_ago_date.isoformat()
                    )
                )
                analysis = self.analysis_service.analyze_workshop(
                    filters.report_date.isoformat(),
                    bundle.workshop_records,
                    comparison_records=(
                        previous_records
                        if previous_report
                        and previous_report.get("scope_machine_no")
                        == self.settings.report_machine_no
                        else None
                    ),
                    comparison_date=(
                        comparison_date.isoformat()
                        if previous_report
                        and previous_report.get("scope_machine_no")
                        == self.settings.report_machine_no
                        else None
                    ),
                    year_ago_records=(
                        year_ago_records
                        if year_ago_report
                        and year_ago_report.get("scope_machine_no")
                        == self.settings.report_machine_no
                        else None
                    ),
                    year_ago_date=(
                        year_ago_date.isoformat()
                        if year_ago_report
                        and year_ago_report.get("scope_machine_no")
                        == self.settings.report_machine_no
                        else None
                    ),
                )
        else:
            previous_bundle = self.query_service.fetch_report_data(
                ReportFilter(report_date=comparison_date)
            )
            # Comparisons currently concern task counts, not energy: don't make
            # needless historical API calls or synthesize comparison energy.
            previous_equipment = previous_bundle.equipment_records
            year_ago_date = _year_ago(filters.report_date)
            year_ago_bundle = self.query_service.fetch_report_data(
                ReportFilter(report_date=year_ago_date)
            )
            year_ago_equipment = year_ago_bundle.equipment_records
            analysis = self.analysis_service.analyze_equipment(
                filters.report_date.isoformat(),
                bundle.equipment_records,
                energy_data_mode=energy_data_mode,
                energy_units=energy_units,
                energy_notice=energy_notice,
                comparison_records=previous_equipment,
                comparison_date=comparison_date.isoformat(),
                year_ago_records=year_ago_equipment,
                year_ago_date=year_ago_date.isoformat(),
            )
        generated_at = datetime.now(timezone)
        report = {
            "meta": self._meta(
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
                bundle.workshop_records[: self.settings.report_max_detail_rows]
            ),
            "worker_summaries": to_primitive(
                build_worker_work_summaries(bundle.workshop_records)
                if report_type == "workshop"
                else []
            ),
            "equipment_records": to_primitive(
                bundle.equipment_records[: self.settings.report_max_detail_rows]
            ),
            "charts": charts,
            "visualization": {"chart_type": chart_type},
            "analysis": to_primitive(analysis),
        }
        self._render_and_complete(
            job=job,
            report=report,
            generated_at=generated_at,
            task_count=(
                statistics.summary.task_count
                if report_type == "workshop"
                else equipment_summary["task_count"]
            ),
            analysis_status=analysis.status,
        )

    def _meta(
        self,
        *,
        job: dict,
        report_type: str,
        generated_at: datetime,
        data_updated_at: datetime | None,
        title: str | None = None,
    ) -> dict:
        period_start = job.get("period_start") or job["report_date"]
        period_end = job.get("period_end") or period_start
        custom_period = _is_range_job(job)
        if report_type == "leadership" and not job.get("period_start"):
            selected = date.fromisoformat(job["report_date"])
            period_start = selected.replace(day=1).isoformat()
            period_end = selected.replace(
                day=calendar.monthrange(selected.year, selected.month)[1]
            ).isoformat()
        resolved_title = title or REPORT_TITLES.get(report_type, report_type)
        if custom_period:
            resolved_title = {
                "leadership": "切割生产领导报表",
                "workshop": "切割生产车间报表",
                "equipment": "切割生产设备报表",
            }.get(report_type, resolved_title)
        return {
            "report_id": job["report_id"],
            "report_type": report_type,
            "title": resolved_title,
            "report_date": job["report_date"],
            "period_start": period_start,
            "period_end": period_end,
            "custom_period": custom_period,
            "generated_at": generated_at.isoformat(timespec="seconds"),
            "system_version": self.settings.app_version,
            "data_updated_at": data_updated_at.isoformat(timespec="seconds")
            if data_updated_at
            else None,
        }

    def _render_and_complete(
        self,
        *,
        job: dict,
        report: dict,
        generated_at: datetime,
        task_count: int,
        analysis_status: str,
    ) -> None:
        report_id = job["report_id"]
        self.metadata.update_status(
            report_id,
            "rendering",
            "正在生成 HTML 报告",
            ai_status=analysis_status,
        )
        file_info = self.report_service.render(
            report_id=report_id,
            report=report,
            generated_at=generated_at,
        )
        final_status = (
            "completed_with_warnings"
            if analysis_status == "degraded"
            else "completed"
        )
        final_message = (
            "报告生成完成，智能分析已降级"
            if analysis_status == "degraded"
            else "报告生成成功"
        )
        with UnitOfWork(self.database, self.settings.timezone) as unit:
            unit.metadata.save_report(
                report_id=report_id,
                task_count=task_count,
                generated_at=generated_at.isoformat(timespec="seconds"),
                system_version=self.settings.app_version,
                **file_info,
            )
            unit.metadata.update_status(
                report_id,
                final_status,
                final_message,
                ai_status=analysis_status,
                finished_at=generated_at.isoformat(timespec="seconds"),
            )
