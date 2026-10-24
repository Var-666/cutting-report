from types import SimpleNamespace

from app.config import DEVICE_CATALOG, Settings, settings_for_device
from app.services.device_runtime import build_device_runtime
from app.database.device_migrations import backup_before_device_upgrade
from app.database.metadata import MetadataRepository
from app.database.llm_usage import LlmUsageRepository
from app.database.leadership_snapshots import LeadershipSnapshotRepository
from app.database.automation import AutomationRepository
from app.database.definitions import DefinitionRepository
from app.database.dispatch import DispatchRepository
from app.database.downloads import DownloadRepository
from app.database.pdm import MockPdmRepository, SqlServerPdmRepository
from app.database.retention import RetentionRepository
from app.database.workshop import WorkshopRepository
from app.database.sqlite import SQLiteDatabase
from app.services.retention_service import RetentionService
from app.services.worker_auth_service import WorkerAuthService
from app.services.automation_service import AutomationService
from app.services.automation_runtime import AutomationRuntime
from app.services.cutting_dispatch_service import CuttingDispatchService
from app.services.email_service import EmailService
from app.services.file_source import build_file_source
from app.services.file_download_service import FileDownloadService
from app.services.magic_box_service import MagicBoxConfigProvider
from app.services.pdf_service import PdfService
from app.workers.retention_worker import RetentionWorker
from app.workers.download_cleanup_worker import DownloadCleanupWorker

from app.database.coordination import CoordinationRepository, backup_before_process_upgrade
from app.services.process_lock import FileLock
from app.services.worker_status import WorkerStatusReader


def build_runtime(settings: Settings, *, role: str) -> SimpleNamespace:
    settings.ensure_runtime_dirs()
    with FileLock(settings.metadata_db_path.with_suffix(".initialize.lock")):
        return _build_runtime(settings, role=role)


def _build_runtime(settings: Settings, *, role: str) -> SimpleNamespace:
    state = SimpleNamespace()
    settings.ensure_runtime_dirs()
    database = SQLiteDatabase(settings.metadata_db_path)
    backup_before_process_upgrade(database)
    backup_before_device_upgrade(database)
    database.initialize()
    coordination = CoordinationRepository(database)
    coordination.initialize()
    state.coordination = coordination
    state.worker_status_reader = WorkerStatusReader(database.path)
    metadata = MetadataRepository(database)
    metadata.initialize()
    llm_usage = LlmUsageRepository(database)
    llm_usage.initialize()
    state.llm_usage = llm_usage
    leadership_snapshots = LeadershipSnapshotRepository(database)
    leadership_snapshots.initialize()
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
    devices = {
        device.id: build_device_runtime(settings_for_device(settings, device.id), database,
                                        definitions, leadership_snapshots, pdf_service, task_queue=coordination)
        for device in DEVICE_CATALOG.devices
    }
    state.devices = devices
    default = devices[DEVICE_CATALOG.default_device_id]
    automation_runtime = AutomationRuntime(settings)
    email_service = EmailService(settings, runtime=automation_runtime)
    automation_service = AutomationService(
        settings, automation, metadata, email_service, runtime=automation_runtime,
    )
    automation_service.device_services = devices
    state.metadata = metadata
    state.sqlite_database = database
    state.workshop = workshop  # Authentication and users are shared across devices.
    state.definitions = definitions
    state.automation = automation
    state.automation_runtime = automation_runtime
    state.automation_service = automation_service
    state.email_service = email_service
    state.worker_auth = WorkerAuthService(settings)
    for name in ('query_service', 'energy_service', 'energy_period_service', 'range_report_service',
                 'leadership_report_service', 'report_analysis_service', 'leadership_refresh_worker',
                 'current_report_refresh_worker', 'energy_history_worker', 'alarm_repository',
                 'alarm_monitor_service', 'alarm_worker'):
        setattr(state, name, getattr(default, name))
    pdm = (
        SqlServerPdmRepository(settings)
        if settings.file_database_enabled
        else MockPdmRepository()
    )
    state.dispatches = dispatches
    file_source = build_file_source(settings)
    downloads = DownloadRepository(database)
    downloads.initialize()
    file_downloads = FileDownloadService(settings, downloads, pdm, file_source)
    state.file_downloads = file_downloads
    if role == "web":
        downloads.recover_interrupted(file_downloads.now())
    download_cleanup_worker = DownloadCleanupWorker(settings, file_downloads, managed=True)
    state.download_cleanup_worker = download_cleanup_worker
    retention_repository = RetentionRepository(database)
    retention_repository.initialize()
    retention_service = RetentionService(settings, retention_repository)
    retention_worker = RetentionWorker(settings, retention_service, managed=True)
    state.retention_service = retention_service
    state.retention_worker = retention_worker
    magic_config = MagicBoxConfigProvider(settings)
    state.cutting_dispatch = CuttingDispatchService(
        settings,
        pdm,
        dispatches,
        file_source,
        file_downloads,
        magic_config,
    )
    if role == "web":
        dispatches.recover_interrupted(state.cutting_dispatch._now())
    state.pdf_service = pdf_service
    return state
