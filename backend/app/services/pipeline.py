from __future__ import annotations

import logging
from datetime import datetime
from collections.abc import Callable
from functools import partial
from zoneinfo import ZoneInfo

from psycopg import Error as PostgresError

from app.core.operations_logging import event
from app.database.postgres import ExecutionLeaseLost
from app.config import Settings
from app.database.automation import AutomationRepository
from app.database.energy import EnergyReportRepository
from app.database.metadata import MetadataRepository
from app.database.definitions import DefinitionRepository
from app.database.workshop_reports import WorkshopReportRepository
from app.database.confirmations import ConfirmationRepository
from app.database.unit_of_work import UnitOfWork
from app.models.domain import AnalysisResult
from app.services.chart_service import ChartService
from app.services.custom_report_service import CustomReportService
from app.services.energy_service import EquipmentEnergyService
from app.services.energy_period_service import EnergyPeriodService
from app.services.leadership_report_service import LeadershipReportService
from app.services.query_service import QueryService
from app.services.range_report_service import RangeReportService
from app.services.report_analysis_service import AnalysisDataVersionMismatch, AnalysisUnavailableError, ReportAnalysisService
from app.core.report_deadline import ReportOperationTimeout, ensure_time_remaining, report_deadline
from app.core.request_cancellation import SqlRequestCancelled
from app.services.report_service import ReportService
from app.services.statistics_service import StatisticsService
from app.services.report_generation.leadership import prepare_report as prepare_leadership_report
from app.services.report_generation.workshop import prepare_report as prepare_workshop_report
from app.services.report_generation.equipment import prepare_report as prepare_equipment_report
from app.services.report_generation.custom import prepare_report as prepare_custom_report


logger = logging.getLogger(__name__)


class ReportAnalysisPendingError(RuntimeError):
    """Keep the job queued until a usable model result or matching cache exists."""


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
        if definitions is None:
            self.definitions.initialize()
        self.query_service = query_service or QueryService(settings)
        self.energy_service = EquipmentEnergyService(settings)
        energy_reports = EnergyReportRepository(self.database)
        self.energy_period_service = EnergyPeriodService(
            settings,
            energy_reports,
            client=self.energy_service.client,
            query_service=self.query_service,
        )
        self.leadership_service = LeadershipReportService(settings, client=self.query_service.client)
        self.statistics_service = StatisticsService()
        self.chart_service = ChartService(settings.chart_top_n)
        self.analysis_service = ReportAnalysisService(settings, metadata, query_service=self.query_service)
        self.report_service = ReportService(settings)
        self.custom_report_service = CustomReportService(settings, client=self.query_service.client)
        if workshop_repository is None:
            workshop_repository = WorkshopReportRepository(self.database, scope_machine_no=settings.report_machine_no)
        self.workshop_repository = workshop_repository
        self.range_service = RangeReportService(
            settings,
            self.query_service,
            ConfirmationRepository(self.database, scope_machine_no=settings.report_machine_no),
            self.energy_period_service,
        )
        self._cancellation_check: Callable[[], bool] | None = None

    def _ensure_active(self) -> None:
        ensure_time_remaining()
        if self._cancellation_check is not None and self._cancellation_check():
            raise ReportPipelineCancelled("报表生成已因服务停止而取消")

    def _analyze_or_wait(
        self,
        analyze: Callable[[], AnalysisResult],
        *,
        report_id: str,
        report_type: str,
        cached: Callable[[], AnalysisResult | None] | None = None,
    ) -> AnalysisResult:
        self._ensure_active()
        try:
            return analyze()
        except (ReportPipelineCancelled, SqlRequestCancelled, AnalysisDataVersionMismatch):
            raise
        except Exception as exc:
            # This callback contains only AI preparation/inference. Required
            # report queries and rendering remain outside this failure boundary.
            if cached is not None:
                previous = cached()
                if previous is not None:
                    logger.warning('report analysis failed; reusing historical success: report_id=%s report_type=%s reason=%s',
                                   report_id, report_type, exc)
                    return previous
            if isinstance(exc, (AnalysisUnavailableError, ReportOperationTimeout)):
                logger.warning(
                    "report awaiting successful analysis: report_id=%s report_type=%s reason=%s",
                    report_id, report_type, exc,
                    extra={"report_id": report_id, "report_type": report_type},
                )
                raise ReportAnalysisPendingError(str(exc)) from exc
            logger.exception(
                "report AI preparation failed; waiting before publication",
                extra={"report_id": report_id, "report_type": report_type},
            )
            self._ensure_active()
            raise ReportAnalysisPendingError('分析数据准备尚未完成，等待自动重试') from exc

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
        stage = 'querying'

        def update_status(status, message, **kwargs):
            nonlocal stage
            stage = status
            return self.metadata.update_status(job['report_id'], status, message, **kwargs)

        try:
            with report_deadline(self.settings.report_operation_timeout_seconds):
                self._ensure_active()
                report_type = job.get("report_type") or "workshop"
                analyze = partial(self._analyze_or_wait, report_id=job["report_id"], report_type=report_type)
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
                stage = "rendering"
                self._render_and_complete(
                    job=job, report=prepared.report, generated_at=prepared.generated_at,
                    task_count=prepared.task_count, analysis_status=prepared.analysis_status,
                )
        except (ReportPipelineCancelled, SqlRequestCancelled, ReportAnalysisPendingError, ExecutionLeaseLost):
            raise
        except Exception as exc:
            code = 'REPORT_TIMEOUT' if isinstance(exc, ReportOperationTimeout) else 'REPORT_STAGE_FAILED'
            exc.report_stage = stage
            event(logger, logging.ERROR, '报表阶段执行失败', error_code=code, stage=stage,
                  report_id=job['report_id'], line_id=self.settings.device_id, error_type=type(exc).__name__)
            raise
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
        rendered_analysis = report.get("analysis")
        if analysis_status == "success" and isinstance(rendered_analysis, dict) and rendered_analysis.get("status") != "success":
            if AutomationRepository(self.database).get_run_for_report(report_id) is not None:
                raise ReportAnalysisPendingError("归档报告尚无与本次数据匹配的成功分析，等待自动重试")
            analysis_status = rendered_analysis["status"]
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
        try:
            self._ensure_active()
            with UnitOfWork(self.database, self.settings.timezone, device_id=self.settings.device_id, scope_machine_no=self.settings.report_machine_no) as unit:
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
        except Exception:
            # The commit reply can be lost. Remove the attempt file only after
            # PostgreSQL confirms it was never published.
            try:
                published = self.metadata.get_job(report_id)
                if published and published.get("relative_path") == file_info["relative_path"]:
                    if published["status"] == "completed" and published.get("sha256") == file_info["sha256"]:
                        return
                else:
                    (self.settings.reports_dir / file_info["relative_path"]).unlink(missing_ok=True)
            except (PostgresError, OSError) as cleanup:
                event(logger, logging.WARNING, '归档结果待核对', error_code='REPORT_ARCHIVE_UNCERTAIN',
                      report_id=report_id, line_id=self.settings.device_id, stage='archiving', error_type=type(cleanup).__name__)
            raise
