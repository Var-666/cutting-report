from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.config import Settings
from app.database.metadata import MetadataRepository
from app.database.definitions import DefinitionRepository
from app.database.workshop import WorkshopRepository
from app.models.domain import (
    QueryBundle,
    ReportFilter,
    SummaryBaseRecord,
    to_primitive,
)
from app.services.chart_service import ChartService
from app.services.custom_report_service import CustomReportService
from app.services.equipment_service import (
    mock_energy,
    sort_equipment_records,
    summarize_equipment,
)
from app.services.leadership_report_service import LeadershipReportService
from app.services.query_service import QueryService
from app.services.report_analysis_service import ReportAnalysisService
from app.services.report_service import ReportService
from app.services.statistics_service import StatisticsService


REPORT_TITLES = {
    "leadership": "切割生产领导月报",
    "workshop": "切割生产车间日报",
    "equipment": "切割生产设备日报",
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


class ReportPipeline:
    def __init__(
        self,
        settings: Settings,
        metadata: MetadataRepository,
        definitions: DefinitionRepository | None = None,
    ) -> None:
        self.settings = settings
        self.metadata = metadata
        self.definitions = definitions or DefinitionRepository(
            settings.metadata_db_path, settings.timezone
        )
        self.definitions.initialize()
        self.query_service = QueryService(settings)
        self.leadership_service = LeadershipReportService(settings)
        self.statistics_service = StatisticsService()
        self.chart_service = ChartService(settings.chart_top_n)
        self.analysis_service = ReportAnalysisService(settings, metadata)
        self.report_service = ReportService(settings)
        self.custom_report_service = CustomReportService(settings)
        self.workshop_repository = WorkshopRepository(settings.metadata_db_path)
        self.workshop_repository.initialize()

    def run(self, job: dict) -> None:
        report_type = job.get("report_type") or "workshop"
        if report_type == "custom" and job.get("definition_id"):
            self._run_custom(job)
        elif report_type == "leadership":
            self._run_leadership(job)
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
        report_date = date.fromisoformat(job["report_date"])
        leadership = self.leadership_service.fetch(report_date)
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
                "report_date": report_date.isoformat(),
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

    def _run_daily(self, job: dict, report_type: str) -> None:
        report_id = job["report_id"]
        timezone = ZoneInfo(self.settings.timezone)
        filters = ReportFilter(
            report_date=date.fromisoformat(job["report_date"]),
            project_code=job["project_code"],
            ship_no=job["ship_no"],
        )
        if report_type == "workshop":
            workshop_report, workshop_records = (
                self.workshop_repository.get_daily_report_records(
                    filters.report_date.isoformat()
                )
            )
            if workshop_report is None:
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
        if report_type == "equipment":
            bundle = replace(
                bundle,
                equipment_records=sort_equipment_records(
                    mock_energy(bundle.equipment_records)
                ),
            )
            energy_data_mode = "mock"

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
        if report_type == "workshop":
            analysis = self.analysis_service.analyze_workshop(
                filters.report_date.isoformat(), bundle.workshop_records
            )
        else:
            analysis = self.analysis_service.analyze_equipment(
                filters.report_date.isoformat(),
                bundle.equipment_records,
                energy_data_mode=energy_data_mode,
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
                "gas": "Nm³（模拟）" if energy_data_mode == "mock" else "数据库原始单位",
                "electricity": "kWh（模拟）" if energy_data_mode == "mock" else "数据库原始单位",
            },
            "summary": to_primitive(statistics.summary),
            "equipment_summary": to_primitive(equipment_summary),
            "data_quality": to_primitive(statistics.data_quality),
            "attention_items": to_primitive(statistics.attention_items),
            "workshop_records": to_primitive(
                bundle.workshop_records[: self.settings.report_max_detail_rows]
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
        return {
            "report_id": job["report_id"],
            "report_type": report_type,
            "title": title or REPORT_TITLES.get(report_type, report_type),
            "report_date": job["report_date"],
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
        self.metadata.save_report(
            report_id=report_id,
            task_count=task_count,
            generated_at=generated_at.isoformat(timespec="seconds"),
            system_version=self.settings.app_version,
            **file_info,
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
        self.metadata.update_status(
            report_id,
            final_status,
            final_message,
            ai_status=analysis_status,
            finished_at=generated_at.isoformat(timespec="seconds"),
        )
