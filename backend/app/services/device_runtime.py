from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings
from app.database.alarms import AlarmRepository
from app.database.alarm_source import SqlServerAlarmSource
from app.database.metadata import MetadataRepository
from app.database.definitions import DefinitionRepository
from app.database.leadership_snapshots import LeadershipSnapshotRepository
from app.database.sqlite import SQLiteDatabase
from app.database.workshop import WorkshopRepository
from app.services.alarm_analysis_service import AlarmAnalysisService
from app.services.alarm_monitor_service import AlarmMonitorService
from app.services.alarm_report_service import AlarmReportService
from app.services.leadership_report_service import LeadershipReportService
from app.services.pipeline import ReportPipeline
from app.services.report_analysis_service import ReportAnalysisService
from app.services.pdf_service import PdfService
from app.services.query_service import QueryService
from app.services.energy_service import EquipmentEnergyService
from app.services.energy_period_service import EnergyPeriodService
from app.services.range_report_service import RangeReportService
from app.workers.alarm_worker import AlarmWorker
from app.workers.current_report_refresh_worker import CurrentReportRefreshWorker
from app.workers.energy_history_worker import EnergyHistoryWorker
from app.workers.leadership_refresh_worker import LeadershipRefreshWorker


@dataclass
class DeviceRuntime:
    settings: Settings
    metadata: MetadataRepository
    workshop: WorkshopRepository
    pipeline: ReportPipeline
    leadership_report_service: LeadershipReportService
    report_analysis_service: ReportAnalysisService
    leadership_refresh_worker: LeadershipRefreshWorker
    current_report_refresh_worker: CurrentReportRefreshWorker
    energy_history_worker: EnergyHistoryWorker
    alarm_repository: AlarmRepository
    alarm_monitor_service: AlarmMonitorService
    alarm_worker: AlarmWorker

    @property
    def query_service(self) -> QueryService:
        return self.pipeline.query_service

    @property
    def energy_service(self) -> EquipmentEnergyService:
        return self.pipeline.energy_service

    @property
    def energy_period_service(self) -> EnergyPeriodService:
        return self.pipeline.energy_period_service

    @property
    def range_report_service(self) -> RangeReportService:
        return self.pipeline.range_service

    @property
    def workers(self) -> tuple[LeadershipRefreshWorker, CurrentReportRefreshWorker, EnergyHistoryWorker, AlarmWorker]:
        return tuple(worker for worker in (self.leadership_refresh_worker, self.current_report_refresh_worker,
                self.energy_history_worker, self.alarm_worker) if worker.thread is not None)


def build_device_runtime(
    settings: Settings, database: SQLiteDatabase, definitions: DefinitionRepository,
    snapshots: LeadershipSnapshotRepository, pdf_service: PdfService,
    *, task_queue=None,
) -> DeviceRuntime:
    metadata = MetadataRepository(database, device_id=settings.device_id)
    workshop = WorkshopRepository(database, scope_machine_no=settings.report_machine_no)
    pipeline = ReportPipeline(settings, metadata, definitions)
    leadership = LeadershipReportService(settings, snapshots)
    analysis = pipeline.analysis_service
    # Keep the original cursor for Cutter01; every added device owns its cursor and lease.
    source_name = 'production' if settings.device_id == 'Cutter01' else f'production:{settings.device_id}'
    alarms = AlarmRepository(database, machine_no=settings.report_machine_no, source_name=source_name)
    alarms.initialize()
    monitor = AlarmMonitorService(settings, alarms, SqlServerAlarmSource(settings),
                                  AlarmAnalysisService(settings), AlarmReportService(settings, pdf_service))
    return DeviceRuntime(
        settings, metadata, workshop, pipeline, leadership, analysis,
        LeadershipRefreshWorker(settings, leadership, analysis, task_queue=task_queue),
        CurrentReportRefreshWorker(settings, pipeline.query_service, pipeline.range_service,
                                   pipeline.energy_period_service, analysis, task_queue=task_queue),
        EnergyHistoryWorker(settings, pipeline.energy_period_service, managed=task_queue is not None),
        alarms, monitor, AlarmWorker(settings, monitor, managed=task_queue is not None),
    )
