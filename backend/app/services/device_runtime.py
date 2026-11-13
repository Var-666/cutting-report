from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo
from app.models.domain import ReportFilter
from app.services.leadership_report_service import _previous_month
from typing import TYPE_CHECKING

from app.config import Settings
from app.database.sqlserver import SqlServerClient
from app.database.cutting_queries import CuttingQueryRepository
from app.database.alarms import AlarmRepository
from app.database.alarm_source import SqlServerAlarmSource
from app.database.metadata import MetadataRepository
from app.database.definitions import DefinitionRepository
from app.database.leadership_snapshots import LeadershipSnapshotRepository
from app.database.postgres import PostgresDatabase
from app.database.workshop_reports import WorkshopReportRepository
from app.database.confirmations import ConfirmationRepository
from app.services.alarm_analysis_service import AlarmAnalysisService
from app.services.alarm_monitor_service import AlarmMonitorService
from app.services.alarm_report_service import AlarmReportService
from app.services.leadership_report_service import LeadershipReportService
from app.services.production_reports import CuttingReportProvider
from app.services.pipeline import ReportPipeline
from app.services.report_analysis_service import ReportAnalysisService
from app.services.pdf_service import PdfService
from app.services.query_service import QueryService
from app.services.ship_name_service import ShipNameService
from app.services.energy_service import EquipmentEnergyService
from app.services.energy_period_service import EnergyPeriodService
from app.services.range_report_service import RangeReportService
from app.workers.alarm_worker import AlarmWorker
from app.workers.current_report_refresh_worker import CurrentReportRefreshWorker
from app.workers.energy_history_worker import EnergyHistoryWorker
from app.workers.leadership_refresh_worker import LeadershipRefreshWorker

if TYPE_CHECKING:
    from app.database.coordination import CoordinationRepository
    from app.services.production_lines import BackgroundSchedule


@dataclass
class DeviceRuntime:
    settings: Settings
    metadata: MetadataRepository
    workshop: WorkshopReportRepository
    confirmations: ConfirmationRepository
    pipeline: ReportPipeline
    leadership_report_service: LeadershipReportService
    report_analysis_service: ReportAnalysisService
    leadership_refresh_worker: LeadershipRefreshWorker
    current_report_refresh_worker: CurrentReportRefreshWorker
    energy_history_worker: EnergyHistoryWorker
    alarm_repository: AlarmRepository
    alarm_monitor_service: AlarmMonitorService
    alarm_worker: AlarmWorker
    report_provider: CuttingReportProvider

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


    def reuse_pending(self, previous: DeviceRuntime) -> None:
        self.energy_history_worker._completion = previous.energy_history_worker._completion

    def start_background(self) -> None:
        history = self.energy_history_worker
        if history.enabled:
            self.recover_background()
            today = history.service.now().date()
            from app.workers.energy_history_worker import _shift_month
            history.schedule_range(_shift_month(today.replace(day=1), -11), today)

    def recover_background(self) -> None:
        history = self.energy_history_worker
        if history.enabled:
            history.repository.recover_history_queue(history.source_key, history._now())

    def schedule_background(self, scheduler: BackgroundSchedule, now: datetime) -> None:
        repository = scheduler.repository
        if self.current_report_refresh_worker.enabled:
            interval = max(30, self.settings.report_background_refresh_seconds)
            scheduler.schedule("current_refresh", self.settings.device_id, {"date": now.date().isoformat(), "source_fingerprint": self.settings.source_fingerprint}, interval,
                               source=self.settings.source_fingerprint)
        leadership = self.leadership_refresh_worker
        if leadership.enabled:
            month = now.date().replace(day=1)
            scheduler.schedule("leadership_refresh", self.settings.device_id,
                lambda: {"date": month.isoformat(), "bootstrap": leadership.service.needs_bootstrap(month), "source_fingerprint": self.settings.source_fingerprint},
                max(30, self.settings.report_background_refresh_seconds), source=self.settings.source_fingerprint)
            if now.hour >= self.settings.leadership_report_reconcile_hour:
                # The current month is already scheduled by the periodic refresh.
                for offset in range(1, 3):
                    selected = _previous_month(month, offset)
                    scheduler.enqueue_once("leadership_refresh", self.settings.device_id, {"date": selected.isoformat(), "bootstrap": False, "source_fingerprint": self.settings.source_fingerprint},
                        key=f"reconcile:{self.settings.device_id}:{now.date()}:{selected}")
        history = self.energy_history_worker
        payload = {'source_fingerprint': self.settings.source_fingerprint}
        if (history.enabled and history.repository.history_ready(history.source_key, history._now())
                and not repository.active("energy_sync", self.settings.device_id, payload)):
            repository.enqueue("energy_sync", self.settings.device_id, payload)
        if self.alarm_worker.enabled:
            scheduler.schedule("alarm", self.settings.device_id, payload, max(30, self.settings.alarm_monitor_interval_seconds),
                               source=self.settings.source_fingerprint)

    def flush_background_results(self) -> None:
        self.energy_history_worker._flush_pending_finish()

    def handle_background(self, kind: str, payload: dict) -> None:
        current = self.current_report_refresh_worker
        if kind == "current_refresh":
            current.run_once(date.fromisoformat(payload["date"]))
            self.query_service.cache_revision.publish()
        elif kind == "leadership_refresh":
            self.leadership_refresh_worker._refresh(date.fromisoformat(payload["date"]), payload["bootstrap"])
        elif kind == "analysis_leadership":
            self.leadership_refresh_worker._analyze(date.fromisoformat(payload["date"]))
        elif kind == "analysis_workshop":
            current._analyze_workshop(date.fromisoformat(payload["date"]))
        elif kind == "analysis_equipment":
            selected = date.fromisoformat(payload["date"])
            equipment = self.query_service.fetch_equipment_data(ReportFilter(report_date=selected))
            task_report = self.energy_period_service.get_task_day(selected, equipment.records)
            current._analyze_equipment(selected, equipment.records, task_report)
        elif kind == "alarm":
            self.alarm_monitor_service.run_once(datetime.now(ZoneInfo(self.settings.timezone)))
        elif kind == "energy_sync":
            history = self.energy_history_worker
            history._flush_pending_finish()
            claim = history._claim()
            if claim:
                history._process(claim)
        else:
            raise ValueError(f"未知后台任务类型: {kind}")


def build_device_runtime(
    settings: Settings, database: PostgresDatabase, definitions: DefinitionRepository,
    snapshots: LeadershipSnapshotRepository, pdf_service: PdfService,
    *, client: SqlServerClient | None = None, task_queue: CoordinationRepository | None = None, ship_names: ShipNameService | None = None,
) -> DeviceRuntime:
    metadata = MetadataRepository(database, device_id=settings.device_id, analysis_scope=f"{settings.device_id}:{settings.source_fingerprint}:end-time-v1")
    workshop = WorkshopReportRepository(database, scope_machine_no=settings.report_machine_no)
    pipeline = ReportPipeline(settings, metadata, definitions, workshop_repository=workshop,
                              query_service=QueryService(settings, client=client, ship_names=ship_names,
                                  repository=CuttingQueryRepository(client, settings.report_machine_no)))
    leadership = LeadershipReportService(settings, snapshots, client=client)
    analysis = pipeline.analysis_service
    source_name = settings.alarm_source_name or f'{settings.production_data_source}:{settings.device_id}'
    alarms = AlarmRepository(database, machine_no=settings.report_machine_no, source_name=source_name)
    alarms.initialize()
    monitor = AlarmMonitorService(settings, alarms, SqlServerAlarmSource(settings, client=client),
                                  AlarmAnalysisService(settings, database), AlarmReportService(settings, pdf_service))
    return DeviceRuntime(
        settings=settings, metadata=metadata, workshop=workshop,
        confirmations=ConfirmationRepository(database, scope_machine_no=settings.report_machine_no),
        pipeline=pipeline, leadership_report_service=leadership, report_analysis_service=analysis,
        leadership_refresh_worker=LeadershipRefreshWorker(settings, leadership, analysis, task_queue=task_queue),
        current_report_refresh_worker=CurrentReportRefreshWorker(settings, pipeline.query_service, pipeline.range_service,
                                   pipeline.energy_period_service, analysis, task_queue=task_queue),
        energy_history_worker=EnergyHistoryWorker(settings, pipeline.energy_period_service, managed=task_queue is not None),
        alarm_repository=alarms, alarm_monitor_service=monitor,
        alarm_worker=AlarmWorker(settings, monitor, managed=task_queue is not None),
        report_provider=CuttingReportProvider(leadership),
    )
