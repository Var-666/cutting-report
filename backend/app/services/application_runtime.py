from __future__ import annotations

from copy import copy
from dataclasses import dataclass, replace
from typing import Literal, cast

from app.config import Settings, settings_for_line
from app.device_config import ProductionLineCatalog, ProductionLineInstance, load_line_catalog
from app.services.production_lines import ProductionLineRuntime
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
from app.services.database_runtime import DatabaseRuntime, DatabaseServices, source_bindings, validate_sources
from app.database.welding import WeldingRepository
from app.database.mysql import MySqlClient
from app.database.sqlserver import SqlServerClient
from app.services.welding_service import WeldingService
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


def build_runtime(settings: Settings, *, role: Literal["web", "worker"]) -> ApplicationRuntime:
    settings.ensure_runtime_dirs()
    with FileLock(settings.metadata_db_path.with_suffix(".initialize.lock")):
        return _build_runtime(settings, role=role)


def _build_runtime(settings: Settings, *, role: Literal["web", "worker"]) -> ApplicationRuntime:
    catalog = load_line_catalog(settings.production_lines_json)
    database_sources = DatabaseSourceRegistry.from_json(settings.database_sources_json, bindings=source_bindings(settings, catalog))
    validate_sources(settings, catalog, database_sources)
    database = SQLiteDatabase(settings.metadata_db_path)
    backup_before_process_upgrade(database)
    migrate_device_isolation(database, {line.id: line.machine_no for line in catalog.lines if line.type == "cutting"})
    from app.database.migrations import apply_migrations
    apply_migrations(database)
    from app.database.migrations.v015_alarm_sources import bind_legacy_sources
    bind_legacy_sources(database, {f'production:{line.id}': (line.machine_no, f'{line.id}:{database_sources.connection_identity(line.source_key)}')
                                  for line in catalog.lines if line.type == 'cutting'},
                        analysis_sources={line.id: f'{line.id}:{database_sources.fingerprint(line.source_key)}'
                                          for line in catalog.lines if line.type == 'cutting'})
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

    def cutting(configuration: Settings, line: ProductionLineInstance, sources: DatabaseSourceRegistry, ship_names: ShipNameService | None, previous: ProductionLineRuntime | None) -> ProductionLineRuntime:
        cfg = settings_for_line(configuration, line, sources.fingerprint(line.source_key))
        cfg = replace(cfg, alarm_source_name=f'{line.id}:{sources.connection_identity(line.source_key)}')
        client = sources.client(line.source_key) if cfg.effective_data_source_mode == "sqlserver" else None
        service = build_device_runtime(cfg, database, definitions, leadership_snapshots, pdf_service,
                                    client=cast(SqlServerClient, client), task_queue=coordination, ship_names=ship_names)
        if previous:
            service.reuse_pending(previous.cutting())
        return ProductionLineRuntime(line, service, background=service, pipeline=service.pipeline)

    def welding(configuration: Settings, line: ProductionLineInstance, sources: DatabaseSourceRegistry, ship_names: ShipNameService | None, previous: ProductionLineRuntime | None) -> ProductionLineRuntime:
        cfg = settings_for_line(configuration, line, sources.fingerprint(line.source_key))
        service = WeldingService(cfg, WeldingRepository(cast(MySqlClient, sources.client(line.source_key)), device_nos=line.device_nos),
                              snapshots=report_snapshots, coordination=coordination)
        return ProductionLineRuntime(line, service)

    builders = {"cutting": (cutting, ("data_source_mode", "report_background_refresh_seconds"), ("bm",)),
                "welding": (welding, ("report_background_refresh_seconds",), ())}

    def build_lines(configuration: Settings, catalog: ProductionLineCatalog, sources: DatabaseSourceRegistry,
                    previous: DatabaseServices | None = None) -> dict[str, ProductionLineRuntime]:
        result = {}
        ship_names = build_ship_names(configuration, sources)
        for line in catalog.lines:
            old = previous.lines.get(line.id) if previous else None
            unchanged = old and old.config == line and sources.get(line.source_key) == previous.database_sources.get(line.source_key)
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

    automation_runtime = AutomationRuntime(settings)
    email_service = EmailService(settings, runtime=automation_runtime)
    worker_auth = WorkerAuthService(settings, client=sql_client(database_sources, settings.worker_source_key, settings.worker_database_auth_enabled))
    pdm = (
        SqlServerPdmRepository(settings, client=sql_client(database_sources, settings.file_source_key, True))
        if settings.file_database_enabled
        else MockPdmRepository()
    )
    file_source = build_file_source(settings)
    downloads = DownloadRepository(database)
    downloads.initialize()
    file_downloads = FileDownloadService(settings, downloads, pdm, file_source)
    if role == "web":
        downloads.recover_interrupted(file_downloads.now())
    download_cleanup_worker = DownloadCleanupWorker(settings, None, managed=True)
    retention_repository = RetentionRepository(database)
    retention_repository.initialize()
    retention_service = RetentionService(settings, retention_repository)
    retention_worker = RetentionWorker(settings, retention_service, managed=True)
    magic_config = MagicBoxConfigProvider(settings, client=sql_client(database_sources, settings.dispatch_source_key, settings.magic_box_enabled))
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
            updated_magic = MagicBoxConfigProvider(configuration, client=sql_client(sources, configuration.dispatch_source_key, configuration.magic_box_enabled))
            updated_dispatch = CuttingDispatchService(configuration, updated_pdm, dispatches, file_source, updated_downloads, updated_magic)
        return DatabaseServices(settings=configuration, catalog=updated_catalog, lines=updated_lines,
            worker_auth=previous.worker_auth if auth_unchanged else WorkerAuthService(configuration, client=sql_client(sources, configuration.worker_source_key, configuration.worker_database_auth_enabled)),
            file_downloads=updated_downloads, database_sources=sources, cutting_dispatch=updated_dispatch)

    context = DatabaseServices(
        settings=settings, catalog=catalog, lines=lines, worker_auth=worker_auth,
        file_downloads=file_downloads, cutting_dispatch=cutting_dispatch, database_sources=database_sources,
    )
    database_runtime = DatabaseRuntime(settings, context, rebuild_database_services, automation_runtime)
    automation_service = AutomationService(
        settings, automation, metadata, email_service, runtime=automation_runtime,
        database_runtime=database_runtime,
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
    )
