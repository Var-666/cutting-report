from __future__ import annotations

import logging
from datetime import datetime
from collections.abc import Callable
from functools import partial
from zoneinfo import ZoneInfo

from app.config import Settings
from app.database.energy import EnergyReportRepository
from app.database.metadata import MetadataRepository
from app.database.definitions import DefinitionRepository
from app.database.workshop import WorkshopRepository
from app.database.workshop_reports import WorkshopReportRepository
from app.database.unit_of_work import UnitOfWork
from app.models.domain import AnalysisResult
from app.services.chart_service import ChartService
from app.services.custom_report_service import CustomReportService
from app.services.energy_service import EquipmentEnergyService
from app.services.energy_period_service import EnergyPeriodService
from app.services.leadership_report_service import LeadershipReportService
from app.services.query_service import QueryService
from app.services.range_report_service import RangeReportService
from app.services.report_analysis_service import AnalysisUnavailableError, ReportAnalysisService
from app.core.report_deadline import ReportOperationTimeout, ensure_time_remaining, report_deadline
from app.core.request_cancellation import SqlRequestCancelled
from app.services.report_service import ReportService
from app.services.statistics_service import StatisticsService
from app.services.report_generation.leadership import prepare_report as prepare_leadership_report
from app.services.report_generation.workshop import prepare_report as prepare_workshop_report
from app.services.report_generation.equipment import prepare_report as prepare_equipment_report
from app.services.report_generation.custom import prepare_report as prepare_custom_report


logger = logging.getLogger(__name__)


def _unavailable_analysis(settings: Settings) -> AnalysisResult:
    """Keep deterministic report delivery independent from model availability."""
    return AnalysisResult(
        status="unavailable",
        overview="确定性报表已生成，但大模型分析暂不可用。",
        features="本区域未生成模型判断，页面中的指标、图表和明细仍可使用。",
        alerts="当前没有可用的 AI 结论，不能据此判断异常、原因或趋势。",
        suggestions="请稍后重试智能分析，并结合本报告的确定性指标和明细进行核对。",
        mode="unavailable",
        generated_at=datetime.now(ZoneInfo(settings.timezone)).isoformat(timespec="seconds"),
    )


class ReportPipelineCancelled(RuntimeError):
    """Raised when the report worker is shutting down between pipeline stages."""


class ReportPipeline:
    def __init__(
        self,
        settings: Settings,
        metadata: MetadataRepository,
        definitions: DefinitionRepository | None = None,
        *,
        workshop_repository: WorkshopReportRepository | None = None,
        query_service: QueryService | None = None,
    ) -> None:
        self.settings = settings
        self.metadata = metadata
        self.database = metadata.database
        self.definitions = definitions or DefinitionRepository(
            self.database, settings.timezone
        )
        self.definitions.initialize()
        self.query_service = query_service or QueryService(settings)
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
        if workshop_repository is None:
            workshop_repository = WorkshopRepository(self.database, scope_machine_no=settings.report_machine_no)
            workshop_repository.initialize()
        self.workshop_repository = workshop_repository
        self.range_service = RangeReportService(
            settings,
            self.query_service,
            self.workshop_repository,
            self.energy_period_service,
        )
        self._cancellation_check: Callable[[], bool] | None = None

    def _ensure_active(self) -> None:
        ensure_time_remaining()
        if self._cancellation_check is not None and self._cancellation_check():
            raise ReportPipelineCancelled("报表生成已因服务停止而取消")

    def _analyze_or_unavailable(
        self,
        analyze: Callable[[], AnalysisResult],
        *,
        report_id: str,
        report_type: str,
    ) -> AnalysisResult:
        self._ensure_active()
        if not self.settings.llm_enabled:
            return _unavailable_analysis(self.settings)
        try:
            return analyze()
        except (ReportPipelineCancelled, ReportOperationTimeout, SqlRequestCancelled):
            raise
        except AnalysisUnavailableError:
            logger.warning(
                "report AI analysis unavailable; continuing deterministic report",
                extra={"report_id": report_id, "report_type": report_type},
            )
            return _unavailable_analysis(self.settings)
        except Exception:
            # This callback contains only AI preparation/inference. Required
            # report queries and rendering remain outside this failure boundary.
            logger.exception(
                "report AI preparation failed; continuing deterministic report",
                extra={"report_id": report_id, "report_type": report_type},
            )
            self._ensure_active()
            return _unavailable_analysis(self.settings)

    def run(
        self,
        job: dict,
        *,
        cancellation_check: Callable[[], bool] | None = None,
    ) -> None:
        if job.get("device_id") != self.settings.device_id:
            raise ValueError("报表任务与执行设备不一致")
        previous_check = self._cancellation_check
        self._cancellation_check = cancellation_check
        try:
            with report_deadline(self.settings.report_operation_timeout_seconds):
                self._ensure_active()
                report_type = job.get("report_type") or "workshop"
                update_status = partial(self.metadata.update_status, job["report_id"])
                analyze = partial(self._analyze_or_unavailable, report_id=job["report_id"], report_type=report_type)
                if report_type == "custom" and job.get("definition_id"):
                    prepared = prepare_custom_report(
                        job, settings=self.settings,
                        definitions=self.definitions,
                        custom_report_service=self.custom_report_service,
                        ensure_active=self._ensure_active, update_status=update_status,
                    )
                elif report_type == "leadership":
                    prepared = prepare_leadership_report(
                        job, settings=self.settings,
                        leadership_service=self.leadership_service,
                        range_service=self.range_service,
                        chart_service=self.chart_service,
                        analysis_service=self.analysis_service,
                        ensure_active=self._ensure_active, update_status=update_status,
                        analyze=analyze,
                    )
                elif report_type == "equipment":
                    prepared = prepare_equipment_report(
                        job, settings=self.settings,
                        range_service=self.range_service,
                        query_service=self.query_service,
                        energy_period_service=self.energy_period_service,
                        chart_service=self.chart_service,
                        analysis_service=self.analysis_service,
                        ensure_active=self._ensure_active, update_status=update_status,
                        analyze=analyze,
                    )
                else:
                    prepared = prepare_workshop_report(
                        job, report_type, settings=self.settings,
                        workshop_repository=self.workshop_repository,
                        range_service=self.range_service,
                        query_service=self.query_service,
                        statistics_service=self.statistics_service,
                        chart_service=self.chart_service,
                        analysis_service=self.analysis_service,
                        ensure_active=self._ensure_active, update_status=update_status,
                        analyze=analyze,
                    )
                self._render_and_complete(
                    job=job, report=prepared.report, generated_at=prepared.generated_at,
                    task_count=prepared.task_count, analysis_status=prepared.analysis_status,
                )
        finally:
            self._cancellation_check = previous_check

    def _render_and_complete(
        self,
        *,
        job: dict,
        report: dict,
        generated_at: datetime,
        task_count: int,
        analysis_status: str,
    ) -> None:
        self._ensure_active()
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
        self._ensure_active()
        with UnitOfWork(self.database, self.settings.timezone, device_id=self.settings.device_id) as unit:
            unit.metadata.save_report(
                report_id=report_id,
                task_count=task_count,
                generated_at=generated_at.isoformat(timespec="seconds"),
                system_version=self.settings.app_version,
                **file_info,
            )
            unit.metadata.update_status(
                report_id,
                "completed",
                "报告生成成功",
                ai_status=analysis_status,
                finished_at=generated_at.isoformat(timespec="seconds"),
            )
