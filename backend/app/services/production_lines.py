"""One instance catalog and typed services; business implementations retain their own APIs."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, cast
from datetime import datetime
from app.config import Settings

from app.device_config import ProductionLineInstance

if TYPE_CHECKING:
    from app.services.database_runtime import DatabaseServices
    from app.services.production_reports import ProductionReportProvider
    from app.services.device_runtime import DeviceRuntime
    from app.services.pipeline import ReportPipeline
    from app.database.coordination import CoordinationRepository


LINE_TYPES = {
    'cutting': {'type_name': '切割', 'home_path': '/admin/home',
                'capabilities': ['reports', 'alarms', 'dispatch', 'settings'],
                'api_modules': ('cutting', 'downloads', 'alarms', 'automation', 'workshop',
                                'leadership-report', 'equipment-report', 'energy-report', 'analysis',
                                'reports', 'options', 'projects', 'ships', 'conditions', 'designer')},
    'welding': {'type_name': '焊接', 'home_path': '/admin/welding/home',
                'capabilities': ['reports', 'analysis', 'settings'], 'api_modules': ('welding',)},
}

class ProductionLineService(Protocol):
    settings: Settings

    @property
    def report_provider(self) -> ProductionReportProvider[Any]: ...


class LineBackgroundTasks(Protocol):
    def start_background(self) -> None: ...
    def schedule_background(self, repository: CoordinationRepository, now: datetime) -> None: ...
    def handle_background(self, kind: str, payload: dict) -> None: ...


@dataclass(frozen=True)
class ProductionLineRuntime:
    config: ProductionLineInstance
    service: ProductionLineService
    background: LineBackgroundTasks | None = None
    pipeline: ReportPipeline | None = None

    @property
    def settings(self) -> Settings:
        return self.service.settings

    @property
    def report_provider(self) -> ProductionReportProvider[Any]:
        return self.service.report_provider

    def cutting(self) -> DeviceRuntime:
        if self.config.type != "cutting":
            raise ValueError("当前产线不支持切割业务")
        return cast("DeviceRuntime", self.service)


def registered_lines(context: DatabaseServices) -> list[dict]:
    items = []
    for line in context.catalog.lines:
        definition = LINE_TYPES[line.type]
        items.append({"id": line.id, "name": line.name, "type": line.type, "enabled": line.enabled,
            "source_key": line.source_key, "device_id": line.device_id,
            "production_line_id": line.production_line_id, "machine_no": line.machine_no,
            "energy_connected": line.energy is not None, "alarm_start_at": line.alarm.start_at if line.alarm else None,
            **{key: value for key, value in definition.items() if key != "api_modules"}})
    return items


def historical_path(path: str) -> bool:
    return (path == '/api/v1/reports' or path.startswith(('/api/v1/reports/', '/reports/'))
            or path == '/api/v1/alarms/reports' or path.startswith('/api/v1/alarms/reports/')
            or path == '/api/v1/alarms/incidents' or path.startswith('/api/v1/alarms/incidents/'))


def supports_path(context: DatabaseServices, line_id: str, path: str, method: str = "GET") -> bool:
    module = path.removeprefix('/api/v1/').split('/')[0]
    business_modules = {module for definition in LINE_TYPES.values() for module in definition['api_modules']}
    if not path.startswith('/api/v1/') or module not in business_modules:
        return True
    try:
        line = context.catalog.get(line_id)
    except ValueError:
        return False
    return (line.enabled or method == 'GET' and historical_path(path)) and module in LINE_TYPES[line.type]['api_modules']


def resolve_line_service(context: DatabaseServices, line_id: str) -> ProductionLineService:
    line = context.catalog.get(line_id)
    if not line.enabled:
        raise ValueError("产线已停用")
    return context.lines[line_id].service
