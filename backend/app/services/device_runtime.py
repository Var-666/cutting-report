from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo
from pydantic import TypeAdapter
from app.models.domain import EquipmentRecord, ReportFilter
from app.modules.cutting.leadership import _previous_month
from typing import TYPE_CHECKING

from app.config import Settings
from app.database.sqlserver import SqlServerClient
from app.modules.cutting.repository import CuttingQueryRepository
from app.modules.alarms.repository import AlarmRepository
from app.modules.alarms.source import SqlServerAlarmSource
from app.database.metadata import MetadataRepository
from app.database.definitions import DefinitionRepository
from app.modules.cutting.leadership_snapshots import LeadershipSnapshotRepository
from app.database.postgres import PostgresDatabase
from app.modules.cutting.workshop_repository import WorkshopReportRepository
from app.modules.cutting.confirmations import ConfirmationRepository
from app.modules.alarms.analysis import AlarmAnalysisService
from app.modules.alarms.monitoring import AlarmMonitorService
from app.modules.alarms.reports import AlarmReportService
from app.modules.cutting.leadership import LeadershipReportService
from app.modules.cutting.reports import CuttingReportProvider
from app.services.pipeline import ReportPipeline
from app.services.report_analysis_service import ReportAnalysisService
from app.services.pdf_service import PdfService
from app.modules.cutting.service import QueryService
from app.services.ship_name_service import ShipNameService
from app.modules.energy.tasks import EquipmentEnergyService
from app.modules.energy.periods import EnergyPeriodService
from app.modules.cutting.range_reports import RangeReportService
from app.modules.alarms.worker import AlarmWorker
from app.workers.current_report_refresh_worker import CurrentReportRefreshWorker
from app.modules.energy.history import EnergyHistoryWorker
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
            from app.modules.energy.history import _shift_month
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
            current.run_once(date.fromisoformat(payload["date"]), requested_at=payload.get('requested_at'))
            self.query_service.cache_revision.publish()
        elif kind == "leadership_refresh":
            self.leadership_refresh_worker._refresh(date.fromisoformat(payload["date"]), payload["bootstrap"], requested_at=payload.get('requested_at'))
        elif kind == "analysis_leadership":
            self.leadership_refresh_worker._analyze(date.fromisoformat(payload["date"]))
        elif kind == "analysis_workshop":
            current._analyze_workshop(date.fromisoformat(payload["date"]))
        elif kind == "analysis_equipment":
            selected = date.fromisoformat(payload["date"])
            if self.report_provider.snapshot_service is not None:
                task_report = self.report_provider.fetch_summary('energy:tasks', {'report_date': selected}).payload
                records = TypeAdapter(list[EquipmentRecord]).validate_python(task_report['records'])
            else:
                equipment = self.query_service.fetch_equipment_data(ReportFilter(report_date=selected))
                task_report = self.energy_period_service.get_task_day(selected, equipment.records)
                records = equipment.records
            current._analyze_equipment(selected, records, task_report)
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
    statistics_snapshots=None, statistics_source: str | None = None,
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
    provider = CuttingReportProvider(leadership, pipeline=pipeline, snapshots=statistics_snapshots,
        coordination=task_queue, source_identity=statistics_source)
    return DeviceRuntime(
        settings=settings, metadata=metadata, workshop=workshop,
        confirmations=ConfirmationRepository(database, scope_machine_no=settings.report_machine_no),
        pipeline=pipeline, leadership_report_service=leadership, report_analysis_service=analysis,
        leadership_refresh_worker=LeadershipRefreshWorker(settings, leadership, analysis, task_queue=task_queue, report_provider=provider),
        current_report_refresh_worker=CurrentReportRefreshWorker(settings, pipeline.query_service, pipeline.range_service,
                                   pipeline.energy_period_service, analysis, task_queue=task_queue, report_provider=provider),
        energy_history_worker=EnergyHistoryWorker(settings, pipeline.energy_period_service, managed=task_queue is not None),
        alarm_repository=alarms, alarm_monitor_service=monitor,
        alarm_worker=AlarmWorker(settings, monitor, managed=task_queue is not None),
        report_provider=provider,
    )
