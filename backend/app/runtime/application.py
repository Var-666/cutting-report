from __future__ import annotations

from copy import copy
from dataclasses import dataclass
from functools import partial
from typing import Literal
import logging

from app.core.operations_logging import event

from app.config import Settings
from app.device_config import ProductionLineCatalog, load_line_catalog
from app.runtime.production_lines import ProductionLineRuntime
from app.modules.cutting.runtime import build_cutting_line
from app.modules.reports.repository import MetadataRepository
from app.database.llm_usage import LlmUsageRepository
from app.modules.cutting.leadership_snapshots import LeadershipSnapshotRepository
from app.modules.reports.snapshot_repository import ReportSnapshotRepository
from app.modules.automation.repository import AutomationRepository
from app.database.definitions import DefinitionRepository
from app.modules.cutting.dispatch_repository import DispatchRepository
from app.database.downloads import DownloadRepository
from app.database.database_monitors import DatabaseMonitorRepository
from app.services.database_monitor_service import DatabaseMonitorService
from app.database.pdm import MockPdmRepository, SqlServerPdmRepository
from app.database.retention import RetentionRepository
from app.database.users import UserRepository
from app.database.sessions import SessionRepository
from app.database.sso import SsoRequestRepository
from app.services.sso_service import SsoService
from app.database.postgres import PostgresDatabase
from app.services.retention_service import RetentionService
from app.services.worker_auth_service import WorkerAuthService
from app.modules.automation.schedules import AutomationService
from app.modules.automation.runtime import AutomationRuntime
from app.services.configuration_diagnostics import ConfigurationDiagnostics
from app.modules.cutting.dispatch import CuttingDispatchService
from app.modules.automation.email import EmailService
from app.services.file_source import build_file_source
from app.services.file_download_service import FileDownloadService
from app.services.magic_box_service import MagicBoxConfigProvider
from app.modules.reports.pdf import PdfService
from app.workers.retention_worker import RetentionWorker
from app.workers.download_cleanup_worker import DownloadCleanupWorker

from app.database.coordination import CoordinationRepository
from app.core.sql_capacity import SqlCapacity
from app.services.worker_status import WorkerStatusReader
from app.services.operations_service import OperationsService
from app.runtime.database import DatabaseRuntime, DatabaseServices, source_bindings, validate_sources
from app.database.sqlserver import SqlServerClient
from app.database.hr import HrRepository
from app.database.magic_box import MagicBoxConfigRepository
from app.modules.welding.runtime import build_welding_line
from app.database.source_registry import DatabaseSourceRegistry, SourceNotFound
from app.database.bm_projects import BmProjectRepository
from app.services.ship_name_service import ShipNameService


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
    """Process resources; every operation selects database_runtime.snapshot()."""
    coordination: CoordinationRepository
    worker_status_reader: WorkerStatusReader
    database: PostgresDatabase
    metadata: MetadataRepository
    llm_usage: LlmUsageRepository
    report_snapshots: ReportSnapshotRepository
    users: UserRepository
    sessions: SessionRepository
    sso: SsoService
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
    database_monitor: DatabaseMonitorService
    operations: OperationsService


def build_runtime(settings: Settings, *, role: Literal["web", "scheduler", "worker", "automation"]) -> ApplicationRuntime:
    settings.ensure_runtime_dirs()
    database = PostgresDatabase(settings)
    try:
        try:
            database.__enter__()
            database.check_runtime()
        except Exception:
            event(logging.getLogger('app.postgres'), logging.CRITICAL, 'PostgreSQL 状态库初始化或结构校验失败',
                  error_code='POSTGRES_STARTUP_FAILED', exc_info=True)
            raise
        return _build_runtime(settings, database, role=role)
    except BaseException:
        database.close()
        raise


def _build_runtime(settings: Settings, database: PostgresDatabase, *, role: Literal["web", "scheduler", "worker", "automation"]) -> ApplicationRuntime:
    catalog = load_line_catalog(settings.production_lines_json)
    database_sources = DatabaseSourceRegistry.from_json(settings.database_sources_json, bindings=source_bindings(settings, catalog), capacity=SqlCapacity(settings))
    validate_sources(settings, catalog, database_sources)
    coordination = CoordinationRepository(database)
    metadata = MetadataRepository(database)
    llm_usage = LlmUsageRepository(database)
    llm_usage.initialize()
    leadership_snapshots = LeadershipSnapshotRepository(database)
    report_snapshots = ReportSnapshotRepository(database)
    automation = AutomationRepository(database)
    definitions = DefinitionRepository(database, settings.timezone)
    definitions.initialize()
    dispatches = DispatchRepository(database)
    dispatches.initialize()
    pdf_service = PdfService(operation_timeout_seconds=settings.pdf_render_timeout_seconds,
                             coordination_path=settings.data_dir / ".coordination" / "pdf-render.lock")

    cutting = partial(build_cutting_line, database=database, definitions=definitions,
                      leadership_snapshots=leadership_snapshots, pdf_service=pdf_service,
                      report_snapshots=report_snapshots, coordination=coordination)
    welding = partial(build_welding_line, database=database, report_snapshots=report_snapshots,
                      coordination=coordination)

    from app.modules.reports.cache_policy import REPORT_CACHE_FIELDS
    cache_fields = tuple(REPORT_CACHE_FIELDS.values())
    builders = {"cutting": (cutting, ("data_source_mode", "llm_model", *cache_fields), ("bm",)),
                "welding": (welding, ("llm_model", *cache_fields), ())}

    def build_lines(configuration: Settings, catalog: ProductionLineCatalog, sources: DatabaseSourceRegistry,
                    previous: DatabaseServices | None = None) -> dict[str, ProductionLineRuntime]:
        result = {}
        ship_names = build_ship_names(configuration, sources)
        for line in catalog.lines:
            old = previous.lines.get(line.id) if previous else None
            unchanged = old and old.config == line and sources.fingerprint(line.source_key) == previous.database_sources.fingerprint(line.source_key)
            builder, global_fields, dependencies = builders[line.type]
            unchanged = unchanged and all(getattr(previous.settings, key) == getattr(configuration, key) for key in global_fields)
            unchanged = unchanged and ([x for x in sources.configs() if x.id in dependencies] == [x for x in previous.database_sources.configs() if x.id in dependencies])
            result[line.id] = old if unchanged else builder(configuration, line, sources, ship_names, old)
        return result

    lines = build_lines(settings, catalog, database_sources)

    def sql_client(sources: DatabaseSourceRegistry, key: str, enabled: bool) -> SqlServerClient | None:
        if not enabled:
            return None
        client = sources.client(key)
        if not isinstance(client, SqlServerClient):
            raise ValueError("HR、文件及魔盒配置必须绑定 SQL Server 数据源")
        return client

    def shared_reports(configuration: Settings, sources: DatabaseSourceRegistry):
        from app.database.pdm_statistics import PdmStatisticsRepository
        from app.services.pdm_statistics import PdmStatisticsService, PDM_STATISTICS_SCOPE
        if not configuration.file_database_enabled:
            return {}
        service = PdmStatisticsService(PdmStatisticsRepository(sql_client(sources, configuration.file_source_key, True)),
            settings=configuration, snapshots=report_snapshots, coordination=coordination,
            source_identity=sources.fingerprint(configuration.file_source_key))
        return {PDM_STATISTICS_SCOPE: service}

    automation_runtime = AutomationRuntime(settings)
    email_service = EmailService(settings, runtime=automation_runtime, pdf_service=pdf_service)
    worker_auth = WorkerAuthService(settings, repository=HrRepository(sql_client(database_sources, settings.worker_source_key, settings.worker_database_auth_enabled)))
    pdm = (
        SqlServerPdmRepository(settings, client=sql_client(database_sources, settings.file_source_key, True))
        if settings.file_database_enabled
        else MockPdmRepository()
    )
    file_source = build_file_source(settings)
    downloads = DownloadRepository(database)
    file_downloads = FileDownloadService(settings, downloads, pdm, file_source)
    if role == "web":
        downloads.recover_interrupted(file_downloads.now())
    download_cleanup_worker = DownloadCleanupWorker(settings, None, managed=True)
    retention_repository = RetentionRepository(database)
    retention_service = RetentionService(settings, retention_repository)
    retention_worker = RetentionWorker(settings, retention_service, managed=True)
    magic_config = MagicBoxConfigProvider(settings, repository=MagicBoxConfigRepository(sql_client(database_sources, settings.dispatch_source_key, settings.magic_box_enabled)))
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
    def rebuild_database_services(configuration: Settings, sources: DatabaseSourceRegistry) -> DatabaseServices:
        previous = database_runtime.current_context
        updated_catalog = load_line_catalog(configuration.production_lines_json)
        sources.validate_lines(updated_catalog, demo=configuration.effective_data_source_mode == "demo")
        updated_lines = build_lines(configuration, updated_catalog, sources, previous)
        def unchanged_binding(key_field: str, flag_field: str) -> bool:
            before, after = getattr(previous.settings, key_field), getattr(configuration, key_field)
            if before != after or getattr(previous.settings, flag_field) != getattr(configuration, flag_field):
                return False
            return not after or previous.database_sources.get(before) == sources.get(after)

        auth_unchanged = unchanged_binding('worker_source_key', 'worker_database_auth_enabled')
        file_unchanged = unchanged_binding('file_source_key', 'file_database_enabled')
        magic_unchanged = unchanged_binding('dispatch_source_key', 'magic_box_enabled')
        updated_downloads = previous.file_downloads
        updated_pdm = updated_downloads.pdm
        if not file_unchanged:
            updated_pdm = SqlServerPdmRepository(configuration, client=sql_client(sources, configuration.file_source_key, True)) if configuration.file_database_enabled else MockPdmRepository()
            updated_downloads = copy(previous.file_downloads)
            updated_downloads.settings = configuration
            updated_downloads.pdm = updated_pdm
        updated_dispatch = previous.cutting_dispatch
        if not file_unchanged or not magic_unchanged:
            updated_magic = MagicBoxConfigProvider(configuration, repository=MagicBoxConfigRepository(sql_client(sources, configuration.dispatch_source_key, configuration.magic_box_enabled)))
            updated_dispatch = CuttingDispatchService(configuration, updated_pdm, dispatches, file_source, updated_downloads, updated_magic)
        return DatabaseServices(settings=configuration, catalog=updated_catalog, lines=updated_lines,
            worker_auth=previous.worker_auth if auth_unchanged else WorkerAuthService(configuration, repository=HrRepository(sql_client(sources, configuration.worker_source_key, configuration.worker_database_auth_enabled))),
            file_downloads=updated_downloads, database_sources=sources, cutting_dispatch=updated_dispatch,
            shared_reports=shared_reports(configuration, sources))

    context = DatabaseServices(
        settings=settings, catalog=catalog, lines=lines, worker_auth=worker_auth,
        file_downloads=file_downloads, cutting_dispatch=cutting_dispatch, database_sources=database_sources,
        shared_reports=shared_reports(settings, database_sources),
    )
    database_runtime = DatabaseRuntime(settings, context, rebuild_database_services, automation_runtime)
    automation_service = AutomationService(
        settings, automation, metadata, email_service, runtime=automation_runtime,
        database_runtime=database_runtime,
    )
    monitor = DatabaseMonitorService(DatabaseMonitorRepository(database), settings.timezone)
    diagnostics = ConfigurationDiagnostics(settings, automation_runtime, database_runtime=database_runtime)
    reader = WorkerStatusReader(database, expected_consumers=settings.consumer_replicas)
    return ApplicationRuntime(
        coordination=coordination, worker_status_reader=reader,
        database=database, metadata=metadata, llm_usage=llm_usage,
        report_snapshots=report_snapshots,
        users=UserRepository(database), sessions=SessionRepository(database),
        sso=SsoService(settings, SsoRequestRepository(database), UserRepository(database)),
        definitions=definitions, automation=automation, automation_runtime=automation_runtime,
        database_runtime=database_runtime,
        configuration_diagnostics=diagnostics,
        automation_service=automation_service, email_service=email_service,
        dispatches=dispatches, pdf_service=pdf_service,
        retention_service=retention_service, retention_worker=retention_worker,
        download_cleanup_worker=download_cleanup_worker,
        database_monitor=monitor,
        operations=OperationsService(settings, database, coordination, reader, monitor, diagnostics, role),
    )
