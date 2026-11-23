from __future__ import annotations

from typing import TYPE_CHECKING, cast

from app.config import Settings, settings_for_line
from app.database.mysql import MySqlClient
from app.database.source_registry import DatabaseSourceRegistry
from app.device_config import ProductionLineInstance
from app.modules.reports.repository import MetadataRepository
from app.modules.reports.snapshot_repository import fingerprint
from app.modules.welding.repository import WeldingRepository
from app.modules.welding.service import WeldingService
from app.runtime.production_lines import ProductionLineRuntime

if TYPE_CHECKING:
    from app.database.coordination import CoordinationRepository
    from app.database.postgres import PostgresDatabase
    from app.modules.reports.snapshot_repository import ReportSnapshotRepository


def build_welding_line(
    settings: Settings,
    line: ProductionLineInstance,
    sources: DatabaseSourceRegistry,
    _ship_names,
    _previous,
    *,
    database: PostgresDatabase,
    report_snapshots: ReportSnapshotRepository,
    coordination: CoordinationRepository,
) -> ProductionLineRuntime:
    cfg = settings_for_line(settings, line, sources.fingerprint(line.source_key))
    scope = fingerprint((cfg.weld_db_timezone, line.device_nos))
    analyses = MetadataRepository(database, device_id=line.id,
        analysis_scope=f'welding:{line.id}:{cfg.source_fingerprint}:{scope}')
    client = cast(MySqlClient, sources.client(line.source_key))
    service = WeldingService(cfg, WeldingRepository(client, device_nos=line.device_nos),
                             snapshots=report_snapshots, coordination=coordination, analyses=analyses)
    return ProductionLineRuntime(line, service)
