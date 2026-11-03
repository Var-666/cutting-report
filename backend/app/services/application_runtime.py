from __future__ import annotations

from copy import copy
from dataclasses import dataclass, replace
from typing import Literal

from app.config import DEVICE_CATALOG, Settings, settings_for_device
from app.services.device_runtime import DeviceRuntime, build_device_runtime
from app.database.device_migrations import migrate_device_isolation
from app.database.metadata import MetadataRepository
from app.database.llm_usage import LlmUsageRepository
from app.database.leadership_snapshots import LeadershipSnapshotRepository
from app.database.report_snapshots import ReportSnapshotRepository
from app.database.automation import AutomationRepository
from app.database.definitions import DefinitionRepository
from app.database.dispatch import DispatchRepository
from app.database.downloads import DownloadRepository
from app.database.pdm import MockPdmRepository, SqlServerPdmRepository
from app.database.retention import RetentionRepository
from app.database.workshop import WorkshopRepository
from app.database.users import UserRepository
from app.database.sessions import SessionRepository
from app.database.sqlite import SQLiteDatabase
from app.services.retention_service import RetentionService
from app.services.worker_auth_service import WorkerAuthService
from app.services.automation_service import AutomationService
from app.services.automation_runtime import AutomationRuntime
from app.services.configuration_diagnostics import ConfigurationDiagnostics
from app.services.cutting_dispatch_service import CuttingDispatchService
from app.services.email_service import EmailService
from app.services.file_source import build_file_source
from app.services.file_download_service import FileDownloadService
from app.services.magic_box_service import MagicBoxConfigProvider
from app.services.pdf_service import PdfService
from app.workers.retention_worker import RetentionWorker
from app.workers.download_cleanup_worker import DownloadCleanupWorker

from app.database.coordination import CoordinationRepository, backup_before_process_upgrade
from app.core.process_lock import FileLock
from app.services.worker_status import WorkerStatusReader
from app.services.database_runtime import DatabaseRuntime, DatabaseServices, DATABASE_FIELDS, WELD_FIELDS
from app.database.welding import WeldingClient, WeldingRepository
from app.services.welding_service import WeldingService
from app.database.source_registry import DatabaseSourceRegistry, SourceNotFound
from app.database.bm_projects import BmProjectRepository
from app.services.ship_name_service import ShipNameService


# BM is optional until registered; an installed source is protected from accidental deletion.
DATABASE_SOURCE_BINDINGS: dict[str, tuple[str, ...]] = {'bm': ('切割报表船名关联',)}


def build_ship_names(settings: Settings, sources: DatabaseSourceRegistry) -> ShipNameService | None:
    try:
        config = sources.get('bm')
    except SourceNotFound:
        return None
    if config.engine != 'sqlserver':
        raise ValueError('bm 船名数据库必须使用 SQL Server')
    if not config.enabled or settings.effective_data_source_mode != 'sqlserver':
        return None
    client = sources.client('bm')
    return ShipNameService(BmProjectRepository(client), cache_seconds=settings.report_data_cache_seconds)


@dataclass
class ApplicationRuntime:
    """Process resources; database_runtime owns the active database service graph.

    The initial service references remain available to embedded callers. New
    operations select database_runtime.snapshot() rather than these references.
    """
    coordination: CoordinationRepository
    worker_status_reader: WorkerStatusReader
    sqlite_database: SQLiteDatabase
    metadata: MetadataRepository
    llm_usage: LlmUsageRepository
    report_snapshots: ReportSnapshotRepository
    workshop: WorkshopRepository
    users: UserRepository
    sessions: SessionRepository
    definitions: DefinitionRepository
    automation: AutomationRepository
    automation_runtime: AutomationRuntime
    database_runtime: DatabaseRuntime
    configuration_diagnostics: ConfigurationDiagnostics
    automation_service: AutomationService
    email_service: EmailService
    dispatches: DispatchRepository
    pdf_service: PdfService
    retention_service: RetentionService
    retention_worker: RetentionWorker
    download_cleanup_worker: DownloadCleanupWorker
    devices: dict[str, DeviceRuntime]
    welding: WeldingService
    worker_auth: WorkerAuthService
    file_downloads: FileDownloadService
    cutting_dispatch: CuttingDispatchService


def build_runtime(settings: Settings, *, role: Literal["web", "worker"]) -> ApplicationRuntime:
    settings.ensure_runtime_dirs()
    with FileLock(settings.metadata_db_path.with_suffix(".initialize.lock")):
        return _build_runtime(settings, role=role)


def _build_runtime(settings: Settings, *, role: Literal["web", "worker"]) -> ApplicationRuntime:
    database = SQLiteDatabase(settings.metadata_db_path)
    backup_before_process_upgrade(database)
    migrate_device_isolation(database, DEVICE_CATALOG)
    from app.database.migrations import apply_migrations
    apply_migrations(database)
    coordination = CoordinationRepository(database)
    coordination.initialize()
    metadata = MetadataRepository(database)
    metadata.initialize()
    llm_usage = LlmUsageRepository(database)
    llm_usage.initialize()
    leadership_snapshots = LeadershipSnapshotRepository(database)
    leadership_snapshots.initialize()
    report_snapshots = ReportSnapshotRepository(database)
    report_snapshots.initialize()
    workshop = WorkshopRepository(database)
    workshop.initialize()
    automation = AutomationRepository(database)
    automation.initialize()
    definitions = DefinitionRepository(database, settings.timezone)
    definitions.initialize()
    dispatches = DispatchRepository(database)
    dispatches.initialize()
    pdf_service = PdfService(operation_timeout_seconds=settings.pdf_render_timeout_seconds,
                             coordination_path=settings.metadata_db_path.parent / ".coordination" / "pdf-render.lock")
    database_sources = DatabaseSourceRegistry.from_json(settings.database_sources_json, bindings=DATABASE_SOURCE_BINDINGS)
    ship_names = build_ship_names(settings, database_sources)
    devices = {
        device.id: build_device_runtime(settings_for_device(settings, device.id), database,
                                        definitions, leadership_snapshots, pdf_service, task_queue=coordination, ship_names=ship_names)
        for device in DEVICE_CATALOG.devices
    }
    automation_runtime = AutomationRuntime(settings)
    email_service = EmailService(settings, runtime=automation_runtime)
    worker_auth = WorkerAuthService(settings)
    pdm = (
        SqlServerPdmRepository(settings)
        if settings.file_database_enabled
        else MockPdmRepository()
    )
    file_source = build_file_source(settings)
    downloads = DownloadRepository(database)
    downloads.initialize()
    file_downloads = FileDownloadService(settings, downloads, pdm, file_source)
    if role == "web":
        downloads.recover_interrupted(file_downloads.now())
    download_cleanup_worker = DownloadCleanupWorker(settings, file_downloads, managed=True)
    retention_repository = RetentionRepository(database)
    retention_repository.initialize()
    retention_service = RetentionService(settings, retention_repository)
    retention_worker = RetentionWorker(settings, retention_service, managed=True)
    magic_config = MagicBoxConfigProvider(settings)
    cutting_dispatch = CuttingDispatchService(
        settings,
        pdm,
        dispatches,
        file_source,
        file_downloads,
        magic_config,
    )
    if role == "web":
        dispatches.recover_interrupted(cutting_dispatch._now())
    welding = WeldingService(settings, WeldingRepository(WeldingClient(settings)), snapshots=report_snapshots, coordination=coordination)

    def rebuild_database_services(configuration: Settings, sources: DatabaseSourceRegistry) -> DatabaseServices:
        previous = database_runtime.current_context
        welding = previous.welding if all(getattr(previous.settings, name) == getattr(configuration, name) for name in WELD_FIELDS) else WeldingService(configuration, WeldingRepository(WeldingClient(configuration)), snapshots=report_snapshots, coordination=coordination)
        bm_changed = ([item for item in previous.database_sources.configs() if item.id == 'bm']
                      != [item for item in sources.configs() if item.id == 'bm'])
        if not bm_changed and all(getattr(previous.settings, name) == getattr(configuration, name) for name in DATABASE_FIELDS - WELD_FIELDS - {'database_sources_json'}):
            return replace(previous, settings=configuration, welding=welding)
        updated_ship_names = build_ship_names(configuration, sources)
        updated_devices = {
            device.id: build_device_runtime(settings_for_device(configuration, device.id), database,
                                            definitions, leadership_snapshots, pdf_service, task_queue=coordination, ship_names=updated_ship_names)
            for device in DEVICE_CATALOG.devices
        }
        for device_id, device in updated_devices.items():
            device.energy_history_worker._completion = previous.devices[device_id].energy_history_worker._completion
        updated_pdm = SqlServerPdmRepository(configuration) if configuration.file_database_enabled else MockPdmRepository()
        # Keep the existing download concurrency and cleanup locks across snapshots.
        updated_downloads = copy(file_downloads)
        updated_downloads.settings = configuration
        updated_downloads.pdm = updated_pdm
        return DatabaseServices(settings=configuration, devices=updated_devices,
            welding=welding,
            worker_auth=WorkerAuthService(configuration), file_downloads=updated_downloads,
            cutting_dispatch=CuttingDispatchService(configuration, updated_pdm, dispatches,
                                                   file_source, updated_downloads, magic_config))

    context = DatabaseServices(
        settings=settings, devices=devices, welding=welding, worker_auth=worker_auth,
        file_downloads=file_downloads, cutting_dispatch=cutting_dispatch, database_sources=database_sources,
    )
    database_runtime = DatabaseRuntime(settings, context, rebuild_database_services, automation_runtime,
                                       source_bindings=DATABASE_SOURCE_BINDINGS)
    automation_service = AutomationService(
        settings, automation, metadata, email_service, runtime=automation_runtime,
        device_services=devices, database_runtime=database_runtime,
    )
    return ApplicationRuntime(
        coordination=coordination, worker_status_reader=WorkerStatusReader(database.path),
        sqlite_database=database, metadata=metadata, llm_usage=llm_usage,
        report_snapshots=report_snapshots, workshop=workshop,
        users=UserRepository(database), sessions=SessionRepository(database),
        definitions=definitions, automation=automation, automation_runtime=automation_runtime,
        database_runtime=database_runtime,
        configuration_diagnostics=ConfigurationDiagnostics(settings, automation_runtime, database_runtime=database_runtime),
        automation_service=automation_service, email_service=email_service,
        dispatches=dispatches, pdf_service=pdf_service,
        retention_service=retention_service, retention_worker=retention_worker,
        download_cleanup_worker=download_cleanup_worker,
        devices=devices, welding=welding, worker_auth=worker_auth,
        file_downloads=file_downloads, cutting_dispatch=cutting_dispatch,
    )
